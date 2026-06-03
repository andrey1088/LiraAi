"""QThread: background model load via model_backends.registry."""

from __future__ import annotations

import sys
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from infrastructure.model_backends.registry import load_model_backend


class ModelLoader(QThread):
    loaded = pyqtSignal(object)
    load_failed = pyqtSignal(str)

    def __init__(self, model_data):
        super().__init__()
        self.model_data = model_data

    def run(self) -> None:
        try:
            backend = load_model_backend(
                self.model_data,
                interruption_requested=self.isInterruptionRequested,
            )
            self.loaded.emit(backend)
        except InterruptedError:
            return
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(
                f"[ModelLoader] FAILED {msg}\n{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
            self.load_failed.emit(msg)
