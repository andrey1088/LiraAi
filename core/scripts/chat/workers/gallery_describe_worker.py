"""Background gallery frame captions — do not call llama on GUI thread."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from PyQt6.QtCore import QThread, pyqtSignal


class GalleryDescribeWorker(QThread):
    """Single thread, image queue; describe_one(path) → text."""

    item_done = pyqtSignal(int, str, str)  # gen_id, expanded_path, description
    batch_finished = pyqtSignal()

    def __init__(
        self,
        items: list[tuple[int, str]],
        describe_one: Callable[[str], str],
        *,
        cuda_refresh_hook: Callable[[], None] | None = None,
        cuda_refresh_every: int = 15,
        pause_after_refresh_ms: int = 120,
        parent=None,
    ):
        super().__init__(parent)
        self._items = list(items)
        self._describe_one = describe_one
        self._cuda_refresh_hook = cuda_refresh_hook
        self._cuda_refresh_every = max(1, int(cuda_refresh_every))
        self._pause_after_refresh_ms = max(0, int(pause_after_refresh_ms))
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        since_refresh = 0
        for gen_id, file_path in self._items:
            if self._cancel_requested:
                break
            expanded = os.path.expanduser(file_path)
            if not os.path.isfile(expanded):
                self.item_done.emit(gen_id, expanded, "")
                continue
            desc = ""
            try:
                desc = (self._describe_one(expanded) or "").strip()
            except Exception as e:
                print(f"[GALLERY] describe error id={gen_id}: {e!r}", flush=True)
            if self._cancel_requested:
                break
            self.item_done.emit(gen_id, expanded, desc)
            since_refresh += 1
            if self._cuda_refresh_hook is not None and since_refresh >= self._cuda_refresh_every:
                self._cuda_refresh_hook()
                since_refresh = 0
                if self._pause_after_refresh_ms > 0:
                    time.sleep(self._pause_after_refresh_ms / 1000.0)
        self.batch_finished.emit()
