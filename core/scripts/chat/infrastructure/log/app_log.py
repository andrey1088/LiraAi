"""
Application diagnostic log: ~/Lira2/logs/lira.log is cleared on startup.
stdout and stderr are tee'd into this file — all print() and typical logging output land here.
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_log_fp = None
_orig_stdout = None
_orig_stderr = None
_initialized = False


def default_log_path() -> Path:
    from infrastructure.log.paths import lira_session_log_path

    return lira_session_log_path()


class _TeeTextStream:
    __slots__ = ("_stream", "_file", "_lock")

    def __init__(self, stream, file_obj, lock: threading.Lock):
        self._stream = stream
        self._file = file_obj
        self._lock = lock

    def write(self, data):
        self._stream.write(data)
        self._stream.flush()
        if self._file is not None:
            try:
                with self._lock:
                    self._file.write(data)
                    self._file.flush()
            except Exception:
                pass

    def flush(self):
        self._stream.flush()
        if self._file is not None:
            try:
                with self._lock:
                    self._file.flush()
            except Exception:
                pass

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()

    def fileno(self):
        return self._stream.fileno()

    def writable(self):
        return True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _close_log_file():
    global _log_fp
    if _log_fp is not None:
        try:
            _log_fp.flush()
            _log_fp.close()
        except Exception:
            pass
        _log_fp = None


def init_app_log(log_path: Path | None = None) -> Path:
    """
    Once per process: clear/create the log file and tee sys.stdout/sys.stderr into it.
    """
    global _log_fp, _orig_stdout, _orig_stderr, _initialized

    if _initialized:
        return log_path or default_log_path()

    path = log_path or default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_fp = open(path, "w", encoding="utf-8", buffering=1)

    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    sys.stdout = _TeeTextStream(_orig_stdout, _log_fp, _lock)
    sys.stderr = _TeeTextStream(_orig_stderr, _log_fp, _lock)

    _initialized = True
    atexit.register(_close_log_file)
    try:
        faulthandler.enable(file=_log_fp, all_threads=True)
    except Exception:
        pass

    # Less noise in lira.log from HF/httpx during lazy embedder load and Hub requests.
    for _name in (
        "httpx",
        "httpcore",
        "sentence_transformers",
        "transformers",
        "huggingface_hub",
        "urllib3",
    ):
        logging.getLogger(_name).setLevel(logging.WARNING)

    return path
