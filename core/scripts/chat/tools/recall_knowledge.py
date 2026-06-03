from core.scripts.chat.tools.gallery_search import gallery_tool
from core.scripts.chat.tools.memory_search import recall_knowledge as memory_tool
from infrastructure.locale.i18n import tr_tools
from infrastructure.locale.variables import var_list


def recall_knowledge(
    query,
    repository,
    semantic_engine,
    user_raw_text=None,
    locale="ru",
    **kwargs,
):
    loc = str(locale or "ru")
    raw_text = (user_raw_text or query).lower()
    gallery_triggers = var_list("recall_knowledge.gallery_triggers", loc)

    if any(trigger in raw_text for trigger in gallery_triggers):
        window = kwargs.get("window")
        g_repo = window.gallery_repo if window else repository
        gallery_results = gallery_tool(
            query,
            repository=g_repo,
            semantic_engine=semantic_engine,
            window=window,
            locale=loc,
        )
        return {
            "memory_content": tr_tools("tools.recall_knowledge.skipped_visual", loc),
            "gallery_content": gallery_results if isinstance(gallery_results, list) else None,
        }

    memory_results = memory_tool(query, repository, semantic_engine, locale=loc)
    return {"memory_content": memory_results, "gallery_content": None}
