"""camera_capture tool: modal capture (logic in ChatController)."""

from infrastructure.locale.i18n import tr_tools


def camera_capture_tool(
    *,
    reason="",
    repository=None,
    semantic_engine=None,
    window=None,
    locale="ru",
):
    loc = str(locale or "ru")
    if window is None or not getattr(window, "chat_controller", None):
        return {
            "status": "error",
            "path": None,
            "message": tr_tools("tools.camera_capture.no_window", loc),
        }
    return window.chat_controller.capture_camera_frame_for_tool(reason=(reason or "").strip())
