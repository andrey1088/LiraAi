"""Embedded mini-browser: SearXNG + result navigation (separate QWebEngineProfile)."""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
from ui.search_downloads import (
    DOWNLOADS_DIR,
    ensure_downloads_dir,
    finalize_download_path,
    resolve_download_filename,
    unique_path,
)


def _log(msg: str) -> None:
    print(f"[SEARCH] {msg}", file=sys.stderr, flush=True)


def _is_pdf_url(url: QUrl) -> bool:
    path = (url.path() or "").lower()
    return path.endswith(".pdf") or ".pdf?" in path


_IMAGE_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".avif",
)


class SearchWebPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent_view):
        super().__init__(profile, parent_view)
        self._search_view = parent_view
        self._configure_settings()

    def _configure_settings(self) -> None:
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        attr = QWebEngineSettings.WebAttribute
        if hasattr(attr, "PdfViewerEnabled"):
            settings.setAttribute(attr.PdfViewerEnabled, True)
        if hasattr(attr, "JavascriptEnabled"):
            settings.setAttribute(attr.JavascriptEnabled, True)

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        if is_main_frame and url.isValid():
            scheme = (url.scheme() or "").lower()
            if scheme in ("http", "https", "file"):
                return True
            if scheme in ("mailto", "tel", "ftp"):
                QDesktopServices.openUrl(url)
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class SearchView(QWidget):
    def _tr(self, msgid: str, **fmt) -> str:
        from infrastructure.locale.i18n import tr

        loc = self._window.config_repo.get_ui_locale()
        merged = {**self._window.config_repo.get_runtime_format_vars(), **fmt}
        text = tr(msgid, loc)
        try:
            return text.format(**merged)
        except (KeyError, ValueError):
            return text

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._route_mode = "default"
        self._home_loaded = False
        self._downloads_dir = ensure_downloads_dir()

        self._profile = QWebEngineProfile("lira-search", self)
        self._profile.downloadRequested.connect(self._on_download_requested)
        self._page = SearchWebPage(self._profile, self)
        self._browser = QWebEngineView(self)
        self._browser.setPage(self._page)
        self._browser.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

        self._page.newWindowRequested.connect(self._on_new_window_requested)
        self._page.urlChanged.connect(self._on_url_changed)
        self._page.loadFinished.connect(self._on_load_finished)

        self._chat_btn = self._make_nav_button(self._tr("Chat"))
        self._back_btn = self._make_nav_button("←", width=36)
        self._forward_btn = self._make_nav_button("→", width=36)
        self._reload_btn = self._make_nav_button("⟳", width=36)
        self._home_btn = self._make_nav_button("⌂", width=36)
        self._chat_btn.setToolTip(self._tr("Return to {app_name} chat"))
        self._back_btn.setToolTip(self._tr("Back"))
        self._forward_btn.setToolTip(self._tr("Forward"))
        self._reload_btn.setToolTip(self._tr("Reload"))
        self._home_btn.setToolTip(self._tr("Home (SearXNG)"))

        self._url_edit = QLineEdit()
        self._url_edit.setObjectName("searchUrlEdit")
        self._url_edit.setPlaceholderText(self._tr("Page URL"))
        self._status = QLabel("")
        self._status.setObjectName("searchStatusLabel")

        self._back_btn.clicked.connect(self._browser.back)
        self._forward_btn.clicked.connect(self._browser.forward)
        self._reload_btn.clicked.connect(self._browser.reload)
        self._home_btn.clicked.connect(self.load_home)
        self._chat_btn.clicked.connect(self._window.show_chat_surface)
        self._url_edit.returnPressed.connect(self._navigate_from_bar)

        nav = QHBoxLayout()
        nav.setContentsMargins(8, 6, 8, 4)
        nav.setSpacing(6)
        nav.addWidget(self._chat_btn)
        nav.addWidget(self._back_btn)
        nav.addWidget(self._forward_btn)
        nav.addWidget(self._reload_btn)
        nav.addWidget(self._home_btn)
        nav.addWidget(self._url_edit, stretch=1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(nav)
        root.addWidget(self._status)
        root.addWidget(self._browser, stretch=1)

        QShortcut(QKeySequence(Qt.Key.Key_Return), self._url_edit, self._navigate_from_bar)
        QShortcut(QKeySequence("Alt+Left"), self, self._browser.back)
        QShortcut(QKeySequence("Alt+Right"), self, self._browser.forward)
        QShortcut(QKeySequence("F5"), self, self._browser.reload)

    @staticmethod
    def _make_nav_button(text: str, width: int | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("searchNavBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if width is not None:
            btn.setFixedWidth(width)
        return btn

    def set_status(self, text: str) -> None:
        self._status.setText(text or "")

    def _on_download_requested(self, item: QWebEngineDownloadRequest) -> None:
        try:
            state = item.state()
        except AttributeError:
            state = None
        if state is not None and state != QWebEngineDownloadRequest.DownloadState.DownloadRequested:
            return

        url = item.url().toString()
        mime = ""
        try:
            mime = item.mimeType() or ""
        except AttributeError:
            pass
        suggested = resolve_download_filename(item.downloadFileName() or "", url, mime)
        full_path = unique_path(self._downloads_dir, suggested)
        item.setDownloadDirectory(self._downloads_dir)
        item.setDownloadFileName(os.path.basename(full_path))
        item.accept()
        _log(f"download start → {full_path}")

        item.stateChanged.connect(lambda _item=item, _path=full_path: self._on_download_state(_item, _path))

    def _on_download_state(self, item: QWebEngineDownloadRequest, path: str) -> None:
        try:
            state = item.state()
        except AttributeError:
            return
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self.set_status(self._tr("Load cancelled"))
            return
        if state != QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            if state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                self.set_status(self._tr("Load aborted"))
            return

        mime = ""
        try:
            mime = item.mimeType() or ""
        except AttributeError:
            pass
        path = finalize_download_path(path, mime)
        name = os.path.basename(path)
        lower = name.lower()
        if lower.endswith(".pdf"):
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            if opened:
                self.set_status(self._tr("PDF opened: {name}", name=name))
            else:
                self.set_status(self._tr("PDF saved: {dir}/{name}", dir=DOWNLOADS_DIR, name=name))
            _log(f"download pdf done {path}")
            return

        if any(lower.endswith(ext) for ext in _IMAGE_SUFFIXES):
            self.set_status(self._tr("Image: {dir}/{name}", dir=DOWNLOADS_DIR, name=name))
        else:
            self.set_status(self._tr("Saved: {dir}/{name}", dir=DOWNLOADS_DIR, name=name))
        _log(f"download done {path}")

    def _on_new_window_requested(self, request) -> None:
        url = request.requestedUrl()
        if url.isValid():
            self._browser.setUrl(url)

    def _on_url_changed(self, url: QUrl) -> None:
        self._url_edit.setText(url.toString())
        self._sync_nav_buttons()
        if _is_pdf_url(url):
            self.set_status("PDF…")

    def _on_load_finished(self, ok: bool) -> None:
        self._sync_nav_buttons()
        url = self._page.url()
        if ok:
            if not _is_pdf_url(url):
                self.set_status("")
            return
        if _is_pdf_url(url):
            self._open_pdf_externally(url)
            return
        if self._status.text().startswith(self._tr("Connecting…")):
            self.set_status(self._tr("Could not load page"))

    def _open_pdf_externally(self, url: QUrl) -> None:
        """If built-in PDF viewer fails — download to temp and open."""
        self.set_status(self._tr("PDF: loading for preview…"))
        _log(f"pdf fallback fetch {url.toString()}")

        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        nam = QNetworkAccessManager(self)

        def finished(reply):
            if reply.error():
                self.set_status(self._tr("Could not open PDF"))
                reply.deleteLater()
                return
            data = reply.readAll()
            reply.deleteLater()
            if not data:
                self.set_status(self._tr("PDF is empty"))
                return
            name = os.path.basename((url.path() or "document.pdf").split("/")[-1]) or "document.pdf"
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            path = unique_path(self._downloads_dir, name)
            try:
                with open(path, "wb") as f:
                    f.write(bytes(data))
            except OSError as e:
                self.set_status(self._tr("PDF: write error"))
                _log(f"pdf write error: {e}")
                return
            if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                self.set_status(self._tr("PDF opened: {name}", name=os.path.basename(path)))
            else:
                self.set_status(f"PDF: {path}")

        req = QNetworkRequest(url)
        nam.finished.connect(finished)
        nam.get(req)

    def _sync_nav_buttons(self) -> None:
        hist = self._page.history()
        self._back_btn.setEnabled(hist.canGoBack())
        self._forward_btn.setEnabled(hist.canGoForward())

    def _navigate_from_bar(self) -> None:
        raw = (self._url_edit.text() or "").strip()
        if not raw:
            return
        if "://" not in raw:
            raw = "https://" + raw
        url = QUrl(raw)
        if url.isValid():
            self._browser.setUrl(url)

    def _home_url(self) -> QUrl:
        base = self._window.get_web_search_url(self._route_mode).rstrip("/")
        return QUrl(base + "/")

    def load_home(self, route_mode: str | None = None) -> None:
        if route_mode is not None:
            self._route_mode = route_mode
        self.set_status(self._tr("Connecting to SearXNG…"))
        ok, detail = self._window.ensure_web_search_service(self._route_mode)
        if not ok:
            self.set_status(f"SearXNG: {detail}")
            _log(f"ensure failed: {detail}")
            return
        url = self._home_url()
        _log(f"home {url.toString()}")
        self._browser.setUrl(url)
        self._home_loaded = True
        self.set_status("")

    def reset_for_model_switch(self) -> None:
        try:
            self._page.history().clear()
        except Exception:
            pass
        self._home_loaded = False
        self.load_home(self._window.search_route_mode())

    def ensure_visible(self) -> None:
        self.load_home(self._window.search_route_mode())
