"""Background image gen/edit — does not block Qt GUI."""

from __future__ import annotations

import os
import traceback
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal


def _img_worker_dprint(msg: str) -> None:
    if os.environ.get("LIRA_QWEN_EDIT_DEBUG", "0").lower() not in ("1", "true", "yes", "on"):
        return
    print(f"[ImageGenWorker] {msg}", flush=True)


class ImageGenerationWorker(QThread):
    succeeded = pyqtSignal(str, str, str, str)  # full_path, prompt, negative, model_name
    failed = pyqtSignal(str)
    empty = pyqtSignal()
    keepalive = pyqtSignal()

    def __init__(
        self,
        llm: Any,
        gen_kwargs: dict,
        full_path: str,
        prompt: str,
        negative: str,
        model_name: str,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._gen_kwargs = gen_kwargs
        self._full_path = full_path
        self._prompt = prompt
        self._negative = negative
        self._model_name = model_name

    def run(self) -> None:
        import threading

        _img_worker_dprint(f"run: start thread={threading.current_thread().name!r} ident={threading.get_ident()}")
        stop_pulse = threading.Event()

        def _pulse_main_loop() -> None:
            while not stop_pulse.wait(1.0):
                self.keepalive.emit()

        pulse_thread = threading.Thread(target=_pulse_main_loop, name="img-gen-keepalive", daemon=True)
        pulse_thread.start()
        try:
            # Inference in QThread: without CUDA context on this thread accelerate/diffusers
            # may return execution_device=None and crash in C++ (NoneType → int).
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.set_device(0)
                    _img_worker_dprint("run: torch.cuda.set_device(0)")
            except Exception as ex:
                _img_worker_dprint(f"run: CUDA init block: {ex!r}\n{traceback.format_exc()}")

            _img_worker_dprint(f"run: calling llm.generate keys={list(self._gen_kwargs.keys())}")
            output = self._llm.generate(**self._gen_kwargs)
            _img_worker_dprint("run: llm.generate returned")
            if not output:
                self.empty.emit()
                return
            img = output[0] if isinstance(output, list) and len(output) > 0 else output
            img.save(self._full_path)
            self.succeeded.emit(self._full_path, self._prompt, self._negative, self._model_name)
        except Exception as e:
            _img_worker_dprint(f"run: FAILED {type(e).__name__}: {e}\n{traceback.format_exc()}")
            self.failed.emit(str(e))
        finally:
            stop_pulse.set()
