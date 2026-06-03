"""Modal frame capture: Qt Multimedia (preview + QImageCapture).

QMediaCaptureSession: QCamera + setVideoOutput(QVideoWidget) + setImageCapture.
Capture: captureToFile() → imageSaved; on Linux briefly detach video output before capture.
"""

from __future__ import annotations

import gc
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    _MULTIMEDIA_OK = True
except ImportError:
    _MULTIMEDIA_OK = False

# On Linux disable preview before captureToFile (fewer backend conflicts).
_PAUSE_VIDEO_OUTPUT_BEFORE_CAPTURE = True
_CAPTURE_WAIT_S = 20.0


def _cam_diag(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(f"[CameraCapture] {msg}", flush=True)
    try:
        from infrastructure.log.paths import camera_capture_log_path

        with open(camera_capture_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


@dataclass
class CameraCaptureOutcome:
    status: str  # captured | denied | cancelled | unavailable | error
    image_path: Optional[str] = None
    remember_session: bool = False
    consent_granted: bool = False
    error_message: Optional[str] = None


def _image_luma_stats(img: QImage) -> tuple[float, float]:
    if img.isNull() or img.width() < 8 or img.height() < 8:
        return 0.0, 0.0
    small = img.scaled(32, 32, Qt.AspectRatioMode.IgnoreAspectRatio)
    if small.format() != QImage.Format.Format_RGB32:
        small = small.convertToFormat(QImage.Format.Format_RGB32)
    lumas: list[float] = []
    for y in range(32):
        for x in range(32):
            c = small.pixelColor(x, y)
            lumas.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
    mean = sum(lumas) / len(lumas)
    var = sum((v - mean) ** 2 for v in lumas) / len(lumas)
    return mean, var


def _image_usable(img: QImage) -> bool:
    mean, var = _image_luma_stats(img)
    if mean < 12.0 or mean > 243.0 or var < 25.0:
        return False
    return True


class CameraCaptureDialog(QDialog):
    def _ui_locale(self) -> str:
        w = self.parent()
        if w is not None and hasattr(w, "config_repo"):
            return w.config_repo.get_ui_locale()
        return "ru"

    def _runtime_fmt(self) -> dict[str, str]:
        w = self.parent()
        if w is not None and hasattr(w, "config_repo"):
            return w.config_repo.get_runtime_format_vars()
        from infrastructure.locale.runtime_vars import runtime_format_vars

        return runtime_format_vars(locale=self._ui_locale())

    def _tr(self, msgid: str, **fmt) -> str:
        from infrastructure.locale.i18n import tr

        merged = {**self._runtime_fmt(), **fmt}
        text = tr(msgid, self._ui_locale())
        try:
            return text.format(**merged)
        except (KeyError, ValueError):
            return text

    def __init__(
        self,
        parent=None,
        *,
        skip_consent: bool = False,
        on_consent_granted: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self._tr("Camera"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self._skip_consent = skip_consent
        self._on_consent_granted = on_consent_granted
        self._outcome = CameraCaptureOutcome(status="cancelled")
        self._camera: QCamera | None = None
        self._capture_session: QMediaCaptureSession | None = None
        self._image_capture: QImageCapture | None = None
        self._video_widget: QVideoWidget | None = None
        self._pending_capture_path: str | None = None
        self._capture_in_progress = False
        self._preview_paused = False
        self._captured_image: QImage | None = None
        self._pending_review_image: QImage | None = None
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self._on_capture_timeout)
        self._build_ui()
        backend = os.environ.get("QT_MEDIA_BACKEND", "(default)")
        _cam_diag(f"dialog open skip_consent={skip_consent} mode=QImageCapture backend={backend}")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._consent_box = QWidget()
        consent_l = QVBoxLayout(self._consent_box)
        consent_l.addWidget(
            QLabel(self._tr("{app_name} requests camera access to capture one frame for the vision model."))
        )
        self._remember_cb = QCheckBox(self._tr("Remember for this chat"))
        self._remember_cb.setChecked(True)
        consent_l.addWidget(self._remember_cb)
        consent_row = QHBoxLayout()
        self._allow_btn = QPushButton(self._tr("Allow"))
        self._deny_btn = QPushButton(self._tr("Cancel"))
        self._allow_btn.clicked.connect(self._on_allow)
        self._deny_btn.clicked.connect(self.reject)
        consent_row.addWidget(self._allow_btn)
        consent_row.addWidget(self._deny_btn)
        consent_l.addLayout(consent_row)
        layout.addWidget(self._consent_box)

        self._preview_host = QWidget()
        preview_l = QVBoxLayout(self._preview_host)
        self._status_label = QLabel("")
        preview_l.addWidget(self._status_label)

        self._live_host = QWidget()
        live_l = QVBoxLayout(self._live_host)
        if _MULTIMEDIA_OK:
            self._video_widget = QVideoWidget()
            self._video_widget.setMinimumSize(480, 360)
            live_l.addWidget(self._video_widget)
        live_row = QHBoxLayout()
        self._snap_btn = QPushButton(self._tr("Capture frame"))
        self._snap_btn.setEnabled(False)
        self._snap_btn.clicked.connect(self._on_snap)
        self._cancel_preview_btn = QPushButton(self._tr("Cancel"))
        self._cancel_preview_btn.clicked.connect(self.reject)
        live_row.addWidget(self._snap_btn)
        live_row.addWidget(self._cancel_preview_btn)
        live_l.addLayout(live_row)
        preview_l.addWidget(self._live_host)

        self._review_host = QWidget()
        review_l = QVBoxLayout(self._review_host)
        self._review_label = QLabel()
        self._review_label.setMinimumSize(480, 360)
        self._review_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._review_label.setStyleSheet("background: #1a1a1a; border-radius: 4px;")
        review_l.addWidget(self._review_label)
        review_l.addWidget(QLabel(self._tr("Review the frame before sending to chat.")))
        review_row = QHBoxLayout()
        self._confirm_btn = QPushButton(self._tr("Confirm"))
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._retake_btn = QPushButton(self._tr("Retake"))
        self._retake_btn.clicked.connect(self._on_retake)
        review_row.addWidget(self._confirm_btn)
        review_row.addWidget(self._retake_btn)
        review_l.addLayout(review_row)
        preview_l.addWidget(self._review_host)
        self._review_host.hide()

        layout.addWidget(self._preview_host)
        self._preview_host.hide()

        if self._skip_consent:
            self._consent_box.hide()
            self._preview_host.show()
            self._live_host.show()
            self._review_host.hide()
            QTimer.singleShot(0, self._start_camera)
        elif not _MULTIMEDIA_OK:
            self._status_label.setText(self._tr("Qt Multimedia unavailable on this system."))
            self._allow_btn.setEnabled(False)

    def outcome(self) -> CameraCaptureOutcome:
        return self._outcome

    def _on_allow(self) -> None:
        self._outcome.consent_granted = True
        self._outcome.remember_session = True
        if self._on_consent_granted is not None:
            try:
                self._on_consent_granted()
            except Exception as exc:
                _cam_diag(f"on_consent_granted failed: {exc!r}")
        self._consent_box.hide()
        self._preview_host.show()
        self._start_camera()

    def _start_camera(self) -> None:
        _cam_diag("start_camera (QImageCapture)")
        self._teardown_camera(wait_s=0.25)
        if not _MULTIMEDIA_OK:
            self._outcome = CameraCaptureOutcome(
                status="unavailable",
                error_message=self._tr("Qt Multimedia not installed"),
            )
            self.reject()
            return
        devices = QMediaDevices.videoInputs()
        if not devices:
            backend = os.environ.get("QT_MEDIA_BACKEND", "(auto)")
            _cam_diag(f"no video inputs (QT_MEDIA_BACKEND={backend})")
            hint = self._tr(
                "Webcam not found.\n\n"
                "If console showed «No QtMultimedia backends found» — remove forced "
                "QT_MEDIA_BACKEND=gstreamer and restart {app_name}, or install plugins:\n"
                "  sudo apt install qtmultimedia6-plugins gstreamer1.0-plugins-good\n"
                "or set before launch: QT_MEDIA_BACKEND=ffmpeg"
            )
            self._outcome = CameraCaptureOutcome(
                status="unavailable",
                error_message=self._tr("Webcam not found or no Qt Multimedia backend"),
            )
            QMessageBox.warning(self, self._tr("Camera"), hint)
            self.reject()
            return
        try:
            dev = devices[0]
            self._camera = QCamera(dev)
            self._capture_session = QMediaCaptureSession()
            self._capture_session.setCamera(self._camera)

            self._image_capture = QImageCapture()
            self._image_capture.setFileFormat(QImageCapture.FileFormat.JPEG)
            self._image_capture.setQuality(QImageCapture.Quality.HighQuality)
            self._image_capture.imageSaved.connect(self._on_image_saved)
            self._image_capture.errorOccurred.connect(self._on_capture_error)
            self._image_capture.readyForCaptureChanged.connect(self._on_ready_for_capture)
            self._capture_session.setImageCapture(self._image_capture)

            if self._video_widget is not None:
                self._capture_session.setVideoOutput(self._video_widget)

            self._camera.start()
            self._status_label.setText(self._tr("Wait for preview, then «Capture frame» (Qt QImageCapture)."))
            QTimer.singleShot(2500, self._enable_snap_fallback)
            _cam_diag(f"camera started device={dev.description()!r}")
        except Exception as e:
            _cam_diag(f"start_camera error: {e!r}")
            self._outcome = CameraCaptureOutcome(status="error", error_message=str(e))
            QMessageBox.warning(self, self._tr("Camera"), self._tr("Could not open camera:\n{e}", e=e))
            self.reject()

    def _enable_snap_fallback(self) -> None:
        if self._live_host.isVisible() and not self._review_host.isVisible():
            if self._image_capture is not None and self._image_capture.isReadyForCapture():
                self._snap_btn.setEnabled(True)
            elif not self._snap_btn.isEnabled():
                self._snap_btn.setEnabled(True)
                _cam_diag("snap enabled (fallback timer, ready unknown)")

    def _on_ready_for_capture(self, ready: bool) -> None:
        if ready and self._live_host.isVisible() and not self._review_host.isVisible():
            self._snap_btn.setEnabled(True)
            self._status_label.setText(self._tr("Preview ready — press «Capture frame»."))
            _cam_diag("readyForCapture=True")

    def _pause_video_output(self) -> None:
        if not _PAUSE_VIDEO_OUTPUT_BEFORE_CAPTURE:
            return
        if self._capture_session is None or self._video_widget is None:
            return
        try:
            self._capture_session.setVideoOutput(None)
            self._preview_paused = True
            for _ in range(8):
                QApplication.processEvents()
            time.sleep(0.12)
            _cam_diag("video output paused before capture")
        except Exception as e:
            _cam_diag(f"pause video output: {e!r}")

    def _resume_video_output(self) -> None:
        if not self._preview_paused:
            return
        if self._capture_session is not None and self._video_widget is not None:
            try:
                self._capture_session.setVideoOutput(self._video_widget)
            except Exception as e:
                _cam_diag(f"resume video output: {e!r}")
        self._preview_paused = False
        for _ in range(6):
            QApplication.processEvents()

    def _on_snap(self) -> None:
        if self._capture_in_progress:
            return
        if self._image_capture is None:
            return
        _cam_diag("snap clicked (captureToFile)")
        if not self._image_capture.isReadyForCapture():
            self._status_label.setText(self._tr("Camera not ready — wait a second…"))
            _cam_diag("snap: not ready for capture")
            return
        self._capture_in_progress = True
        self._snap_btn.setEnabled(False)
        self._status_label.setText(self._tr("Capturing frame…"))
        QApplication.processEvents()
        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="lira_qt_cam_")
        os.close(fd)
        self._pending_capture_path = path
        self._capture_timer.start(int(_CAPTURE_WAIT_S * 1000))
        self._pause_video_output()
        try:
            ok = self._image_capture.captureToFile(path)
            _cam_diag(f"captureToFile({path!r}) -> {ok}")
            if not ok:
                self._finish_capture_failed("captureToFile returned false")
        except Exception as e:
            self._finish_capture_failed(str(e))

    def _on_image_saved(self, _id: int, path: str) -> None:
        self._capture_timer.stop()
        local = path
        if local.startswith("file:"):
            local = QUrl(local).toLocalFile()
        _cam_diag(f"imageSaved path={local!r}")
        self._resume_video_output()
        img = QImage(local)
        if img.isNull():
            self._finish_capture_failed("could not read saved JPEG")
            return
        mean, var = _image_luma_stats(img)
        if not _image_usable(img):
            self._finish_capture_failed(f"frame empty or flat (mean={mean:.0f}, var={var:.0f})")
            return
        self._capture_in_progress = False
        self._cleanup_pending_capture_file()
        self._show_review(img)
        _cam_diag(f"capture ok mean={mean:.1f} var={var:.1f}")

    def _on_capture_error(self, _id: int, error: int, message: str) -> None:
        self._capture_timer.stop()
        _cam_diag(f"capture error={error} msg={message!r}")
        self._finish_capture_failed(message or f"QImageCapture error {error}")

    def _on_capture_timeout(self) -> None:
        _cam_diag("capture timeout")
        self._finish_capture_failed("imageSaved wait timeout")

    def _finish_capture_failed(self, reason: str) -> None:
        self._capture_in_progress = False
        self._capture_timer.stop()
        self._resume_video_output()
        self._cleanup_pending_capture_file()
        self._status_label.setText(self._tr("Could not capture frame: {reason}", reason=reason))
        self._snap_btn.setEnabled(True)
        _cam_diag(f"snap failed: {reason}")

    def _cleanup_pending_capture_file(self) -> None:
        p = self._pending_capture_path
        self._pending_capture_path = None
        if p and os.path.isfile(p):
            try:
                os.unlink(p)
            except OSError:
                pass

    def _set_review_pixmap(self, image: QImage) -> None:
        pix = QPixmap.fromImage(image)
        target = self._review_label.size()
        if target.width() < 32:
            target = self._review_label.minimumSize()
        scaled = pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._review_label.setPixmap(scaled)

    def _show_live_preview(self) -> None:
        self._review_host.hide()
        self._live_host.show()
        self._status_label.show()
        self._pending_review_image = None
        self._review_label.clear()

    def _show_review(self, image: QImage) -> None:
        self._pending_review_image = image
        self._live_host.hide()
        self._status_label.setText(self._tr("This is how {app_name} will see the frame. Confirm or retake."))
        self._review_host.show()
        self._set_review_pixmap(image)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (
            self._review_host.isVisible()
            and self._pending_review_image is not None
            and not self._pending_review_image.isNull()
        ):
            self._set_review_pixmap(self._pending_review_image)

    def _on_confirm(self) -> None:
        if self._pending_review_image is None or self._pending_review_image.isNull():
            self._on_retake()
            return
        self._captured_image = self._pending_review_image
        self._outcome = CameraCaptureOutcome(status="captured")
        mean, var = _image_luma_stats(self._captured_image)
        _cam_diag(
            f"confirmed {self._captured_image.width()}x{self._captured_image.height()} mean={mean:.1f} var={var:.1f}"
        )
        self.accept()

    def _on_retake(self) -> None:
        _cam_diag("retake")
        self._show_live_preview()
        self._status_label.setText(self._tr("Wait for preview again and press «Capture frame»."))
        self._snap_btn.setEnabled(False)
        self._start_camera()

    def _teardown_camera(self, *, wait_s: float = 0) -> None:
        self._capture_timer.stop()
        self._capture_in_progress = False
        self._resume_video_output()
        try:
            if self._capture_session is not None:
                self._capture_session.setImageCapture(None)
                self._capture_session.setVideoOutput(None)
                self._capture_session.setCamera(None)
        except Exception:
            pass
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception:
                pass
            try:
                self._camera.deleteLater()
            except Exception:
                pass
        self._camera = None
        self._image_capture = None
        if self._capture_session is not None:
            try:
                self._capture_session.deleteLater()
            except Exception:
                pass
        self._capture_session = None
        self._cleanup_pending_capture_file()
        gc.collect()
        for _ in range(16):
            QApplication.processEvents()
        if wait_s > 0:
            time.sleep(wait_s)
        _cam_diag("camera torn down")

    def reject(self) -> None:
        _cam_diag(f"reject outcome={self._outcome.status}")
        if self._outcome.status == "cancelled" and not self._skip_consent and self._consent_box.isVisible():
            self._outcome = CameraCaptureOutcome(status="denied")
        self._teardown_camera()
        super().reject()

    def accept(self) -> None:
        _cam_diag("accept")
        self._teardown_camera()
        super().accept()

    def captured_image(self) -> QImage | None:
        return self._captured_image
