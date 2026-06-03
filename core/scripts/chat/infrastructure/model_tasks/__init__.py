"""
Model tasks from the UI (sidebar): batch operations, not chat function-calling.

Subpackages:
- gallery — frame captions, CLIP/e5, subprocess
"""

from infrastructure.model_tasks.registry import (
    SIDEBAR_MODEL_TASKS,
    SidebarModelTask,
    sidebar_tasks_for_ui,
    task_by_id,
    task_params_dict,
    tasks_for_model,
)

__all__ = [
    "SIDEBAR_MODEL_TASKS",
    "SidebarModelTask",
    "sidebar_tasks_for_ui",
    "task_by_id",
    "task_params_dict",
    "tasks_for_model",
]
