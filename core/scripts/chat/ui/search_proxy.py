"""Search mode proxy: PyQt6 without QWebEngineProfile.setHttpProxy → QNetworkProxyFactory."""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from PyQt6.QtNetwork import QNetworkProxy, QNetworkProxyFactory, QNetworkProxyQuery

_ROUTER: "SearchProxyRouter | None" = None


def _log(msg: str) -> None:
    print(f"[SEARCH] {msg}", file=sys.stderr, flush=True)


def _is_local_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return False
    if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if h.startswith("127."):
        return True
    return False


def _proxy_from_url(raw: str) -> QNetworkProxy | None:
    text = (raw or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if not parsed.hostname:
        return None
    proxy = QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy)
    proxy.setHostName(parsed.hostname)
    if parsed.port:
        proxy.setPort(parsed.port)
    if parsed.username:
        proxy.setUser(parsed.username)
    if parsed.password:
        proxy.setPassword(parsed.password)
    return proxy


class _SearchProxyFactory(QNetworkProxyFactory):
    def __init__(self) -> None:
        super().__init__()
        self._search_active = False
        self._search_proxy: QNetworkProxy | None = None

    def set_search_active(self, active: bool) -> None:
        self._search_active = bool(active)

    def set_search_proxy(self, proxy: QNetworkProxy | None) -> None:
        self._search_proxy = proxy

    def queryProxy(self, query: QNetworkProxyQuery):
        if not self._search_active:
            return [QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy)]
        url = query.url()
        if _is_local_host(url.host()):
            return [QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)]
        if self._search_proxy is not None:
            return [self._search_proxy]
        return [QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy)]


class SearchProxyRouter:
    def __init__(self, service_manager) -> None:
        self._factory = _SearchProxyFactory()
        QNetworkProxyFactory.setApplicationProxyFactory(self._factory)
        raw = (service_manager.get_proxy_url("ru") or service_manager._env("HTTP_PROXY", "")).strip()
        proxy = _proxy_from_url(raw)
        self._factory.set_search_proxy(proxy)
        if proxy is None:
            _log("HTTP_PROXY not set — external sites in search without proxy")
        else:
            _log(f"proxy factory ready host={proxy.hostName()}:{proxy.port()}")

    def set_active(self, active: bool) -> None:
        self._factory.set_search_active(active)
        _log(f"proxy active={active}")


def install_search_proxy_router(service_manager) -> SearchProxyRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = SearchProxyRouter(service_manager)
    return _ROUTER
