"""Re-serve saved web_search from DB (no SearXNG)."""

from infrastructure.locale.i18n import tr_tools


def web_search_saved(run_id, repository, semantic_engine=None, window=None, locale="ru", **kwargs):
    loc = str(locale or "ru")
    if window is None:
        return tr_tools("tools.web_search_saved.no_window", loc)
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        return tr_tools("tools.web_search_saved.bad_run_id", loc)

    sid = window.session_controller.current_session_id
    rows = repository.get_saved_web_search_results_for_run(rid, sid)
    if rows is None:
        return tr_tools("tools.web_search_saved.not_found", loc).format(rid=rid)
    if not rows:
        return tr_tools("tools.web_search_saved.empty", loc).format(rid=rid)

    no_title = tr_tools("tools.web_search.no_title", loc)
    line_tpl = tr_tools("tools.web_search_saved.line", loc)
    lines = []
    for i, (url, title, _rank, snippet) in enumerate(rows, 1):
        t = (title or "").strip() or no_title
        u = (url or "").strip()
        sn = (snippet or "").strip()[:400]
        lines.append(line_tpl.format(i=i, title=t, u=u, sn=sn))

    body = "\n\n".join(lines)
    print(f"[TOOL] web_search_saved: run_id={rid} rows={len(rows)}")
    header = tr_tools("tools.web_search_saved.header", loc).format(rid=rid)
    footer = tr_tools("tools.web_search_saved.footer", loc).format(rid=rid)
    return f"{header}{body}\n\n{footer}"
