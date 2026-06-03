import os
import sys
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QLockFile, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox, QSplashScreen

# 1. CRITICAL WEBENGINE FIX (must be at the very top)
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

# Path setup (unchanged)
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "9223"

# Qt Multimedia: do not set QT_MEDIA_BACKEND by default — Qt picks an installed plugin
# (often ffmpeg). Forcing gstreamer without packages yields “No QtMultimedia backends found”.
# Optional: QT_MEDIA_BACKEND=ffmpeg or =gstreamer before starting Lira.

if __name__ == "__main__":
    import traceback

    from infrastructure.log.app_log import init_app_log
    from infrastructure.paths import lira_data, lira_root
    from infrastructure.model_backends.image_qwen.diag_log import (
        qwen_diag_append,
        qwen_diag_log_path,
    )

    log_path = init_app_log()
    qp = qwen_diag_log_path()
    print(
        f"\n[Lira] Full session log (print, stderr, uncaught exceptions):\n  {log_path}\n"
        f"[Lira] Qwen Image Edit log (load/edit stages, append):\n  {qp}\n",
        flush=True,
    )

    _orig_excepthook = sys.excepthook

    def _lira_excepthook(exc_type, exc, tb):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n--- UNCAUGHT EXCEPTION ---\n")
                f.writelines(traceback.format_exception(exc_type, exc, tb))
        except Exception:
            pass
        try:
            qwen_diag_append("UNCAUGHT:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
        _orig_excepthook(exc_type, exc, tb)

    sys.excepthook = _lira_excepthook

    # 2. Create QApplication AFTER setting OpenGL attribute
    app = QApplication(sys.argv)
    app.setApplicationName("LiraAI")
    app.setDesktopFileName("lira")

    # SINGLE-INSTANCE GUARD
    lock_path = str(lira_root() / "lira.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_file = QLockFile(lock_path)

    if not lock_file.tryLock(100):
        from infrastructure.locale.i18n import tr_ui_format
        from infrastructure.locale.runtime_vars import runtime_format_vars

        _fmt = runtime_format_vars()
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setText(tr_ui_format("{app_name} is already running", "ru", **_fmt))
        msg.setInformativeText("Two instances cannot run on the same GPU.")
        msg.setWindowTitle("Startup error")
        msg.show()
        sys.exit(app.exec())

    # === INSTANT SPLASH with image ===

    splash_image_path = str(lira_data("media", "splash.png"))

    # 2. Load QPixmap from file
    splash_pixmap = QPixmap(splash_image_path)

    # (Optional) If the image failed to load (e.g. missing file),
    # create a placeholder so the app does not crash
    if splash_pixmap.isNull():
        splash_pixmap = QPixmap(768, 448)
        splash_pixmap.fill(Qt.GlobalColor.darkGray)

    # Splash without always-on-top — frame and normal Z-order only.
    splash = QSplashScreen(
        splash_pixmap,
        Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint,
    )

    # If the PNG has alpha, this attribute preserves it
    # splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    # 4. Message font for readability on the splash image
    font = splash.font()
    font.setPointSize(12)
    font.setBold(True)
    splash.setFont(font)

    splash.show()

    # Important on Ubuntu/Wayland:
    # Draw the background first
    splash.repaint()
    app.processEvents()

    # 6. Draw text
    from infrastructure.locale.i18n import tr_ui_format
    from infrastructure.locale.runtime_vars import runtime_format_vars

    _splash_fmt = runtime_format_vars()
    splash.showMessage(
        tr_ui_format(
            "Initializing {app_name}…\nLoading model into CUDA",
            "ru",
            **_splash_fmt,
        ),
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter,
        Qt.GlobalColor.white,
    )

    # Force text repaint over the image
    splash.repaint()

    # Short pause so Wayland registers the window as alive
    # (remove if it works without this)
    import time

    time.sleep(0.1)
    app.processEvents()

    # LOAD MAIN WINDOW (import safe after WebEngine fix)
    from ui.window import LiraWindow

    window = LiraWindow()
    window.showMaximized()
    window._apply_maximized_chrome()

    splash.finish(window)
    sys.exit(app.exec())
