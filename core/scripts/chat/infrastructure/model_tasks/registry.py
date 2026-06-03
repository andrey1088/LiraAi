"""Sidebar task registry (batch/UI), not chat-tools."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SidebarModelTask:
    """Toolbar button/action descriptor (limbic-tools)."""

    id: str
    label: str
    description: str
    bridge_method: str | None = None
    # Bridge args (e.g. mode=missing for gallery describe)
    params: tuple[tuple[str, str], ...] = ()
    # none | missing_count — button badge
    badge: str = "none"
    attention_when_missing: bool = False


def task_params_dict(task: SidebarModelTask) -> dict[str, str]:
    return dict(task.params)


SIDEBAR_MODEL_TASKS: tuple[SidebarModelTask, ...] = (
    SidebarModelTask(
        id="gallery_describe_missing",
        label="Add descriptions",
        description="Generate descriptions for frames without description",
        bridge_method="start_gallery_description_refresh",
        params=(("mode", "missing"),),
        badge="missing_count",
        attention_when_missing=True,
    ),
    SidebarModelTask(
        id="gallery_describe_repair",
        label="Fix descriptions",
        description="Regenerate broken or empty descriptions",
        bridge_method="start_gallery_description_refresh",
        params=(("mode", "repair"),),
    ),
)


def task_by_id(task_id: str) -> SidebarModelTask | None:
    for t in SIDEBAR_MODEL_TASKS:
        if t.id == task_id:
            return t
    return None


def tasks_for_model(m_info) -> list[SidebarModelTask]:
    """Sidebar tasks available for this model."""
    from infrastructure.model_tasks.gallery.capabilities import (
        model_supports_gallery_describe,
    )

    if not model_supports_gallery_describe(m_info):
        return []
    return list(SIDEBAR_MODEL_TASKS)


def sidebar_tasks_for_ui(m_info, locale: str = "ru") -> list[dict]:
    """JSON for Web UI."""
    from infrastructure.locale.i18n import tr

    return [
        {
            "id": t.id,
            "label": tr(t.label, locale),
            "description": tr(t.description, locale),
            "params": task_params_dict(t),
            "badge": t.badge,
            "attention_when_missing": t.attention_when_missing,
        }
        for t in tasks_for_model(m_info)
    ]
