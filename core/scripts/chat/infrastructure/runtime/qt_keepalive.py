"""Pump Qt event loop during long GUI-thread work (avoids “app not responding”)."""

from __future__ import annotations

import inspect
from typing import Any


def pump_qt_events(max_ms: int = 80) -> None:
    """Process Qt event queue (repaint, WM ping, WebEngine)."""
    try:
        from PyQt6.QtCore import QEventLoop
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, int(max_ms))
    except Exception:
        pass


def diffusion_step_keepalive(
    pipe: Any,
    step_index: int,
    timestep: int,
    callback_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """diffusers callback_on_step_end / callback — invoked each denoise step."""
    pump_qt_events()
    return callback_kwargs


def _append_pipe_keepalive_kwargs(call_kwargs: dict[str, Any], pipe: Any) -> None:
    """Inject callback into pipeline call when the signature supports it."""
    try:
        params = inspect.signature(pipe.__call__).parameters
    except (TypeError, ValueError):
        return
    if "callback_on_step_end" in params:
        call_kwargs["callback_on_step_end"] = diffusion_step_keepalive
        if "callback_on_step_end_tensor_inputs" in params:
            call_kwargs["callback_on_step_end_tensor_inputs"] = []
    elif "callback" in params:
        call_kwargs["callback"] = diffusion_step_keepalive
        if "callback_steps" in params:
            call_kwargs["callback_steps"] = 1


def call_pipe_with_keepalive(pipe: Any, **inputs: Any) -> Any:
    call_kwargs = dict(inputs)
    _append_pipe_keepalive_kwargs(call_kwargs, pipe)
    return pipe(**call_kwargs)
