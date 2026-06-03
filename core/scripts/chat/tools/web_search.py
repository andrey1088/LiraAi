import json
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from infrastructure.locale.i18n import tr_tools


def _domain_from_url(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _route_hint_for_domain(domain: str) -> str:
    d = (domain or "").lower()
    if d.endswith(".ru") or d.endswith(".su") or ".ru." in d:
        return "ru"
    return "default"


def web_search(
    query,
    repository,
    semantic_engine=None,
    window=None,
    limit=5,
    language="ru",
    time_range="",
    locale="ru",
    **kwargs,
):
    """Search via local SearXNG; persist run/steps/artifacts in the DB."""
    loc = str(locale or "ru")
    q = (query or "").strip()
    if not q:
        return tr_tools("tools.web_search.empty_query", loc)

    if window is None:
        return tr_tools("tools.web_search.no_window", loc)

    route_mode = (kwargs.get("route_mode") or "auto").strip().lower()
    if route_mode not in ("auto", "default", "ru"):
        route_mode = "auto"
    if route_mode == "auto":
        route_mode = "default"

    ok, details = window.ensure_web_search_service(route_mode=route_mode)
    if not ok:
        return tr_tools("tools.web_search.unavailable", loc).format(details=details)

    run_id = repository.create_research_run(
        session_id=window.session_controller.current_session_id,
        user_query_raw=q,
        user_query_norm=q.lower().strip(),
        mode="news",
        status="running",
        retention_class="warm",
        expires_at=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    step_id = repository.add_research_step(
        run_id=run_id,
        step_type="search_web",
        input_json=json.dumps(
            {
                "query": q,
                "limit": int(limit),
                "language": language,
                "time_range": time_range,
                "route_mode": route_mode,
            },
            ensure_ascii=False,
        ),
        status="running",
    )

    try:
        base = window.get_web_search_url(route_mode=route_mode).rstrip("/") + "/search"
        params = {
            "q": q,
            "format": "json",
            "language": language or "ru",
        }
        if time_range:
            params["time_range"] = time_range
        url = f"{base}?{urlencode(params)}"
        req = Request(
            url,
            headers={
                "User-Agent": "Lira2/1.0",
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
            },
        )
        with urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        payload = json.loads(raw or "{}")
        results = payload.get("results") or []
    except Exception as e:
        repository.finish_research_step(
            step_id=step_id,
            status="failed",
            error_text=str(e),
            output_json=json.dumps({"error": str(e)}, ensure_ascii=False),
        )
        repository.finish_research_run(run_id, status="failed", notes=str(e))
        return tr_tools("tools.web_search.error", loc).format(e=e)

    top = results[: max(1, min(int(limit), 10))]
    if not top:
        repository.finish_research_step(
            step_id=step_id,
            status="done",
            output_json=json.dumps({"count": 0}, ensure_ascii=False),
        )
        repository.finish_research_run(run_id, status="done", notes="empty results")
        return tr_tools("tools.web_search.no_results", loc)

    no_title = tr_tools("tools.web_search.no_title", loc)
    line_tpl = tr_tools("tools.web_search.result_line", loc)
    lines = []
    for i, item in enumerate(top, 1):
        title = (item.get("title") or "").strip()
        link = (item.get("url") or "").strip()
        snippet = (item.get("content") or "").strip()
        engine = (item.get("engine") or "searxng").strip()
        published = item.get("publishedDate")
        domain = _domain_from_url(link)
        route_hint = _route_hint_for_domain(domain)

        source_id = repository.upsert_source(
            source_kind="domain",
            source_key=domain or engine,
            display_name=domain or engine,
            language=language or "ru",
        )
        artifact_id = repository.add_artifact(
            source_id=source_id,
            origin_type="web_search",
            media_type="text",
            url=link,
            canonical_url=link,
            title=title,
            author=engine,
            published_at=published,
            language=language or "ru",
            content_hash=f"{domain}|{link}",
            retention_class="warm",
            expires_at=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        repository.add_artifact_payload(
            artifact_id=artifact_id,
            text_content=snippet,
            summary_short=snippet[:500],
            keywords_json=json.dumps([f"route_hint:{route_hint}"], ensure_ascii=False),
        )
        repository.link_run_artifact(run_id, artifact_id, step_id=step_id, rank_score=float(i))

        short_title = title if title else no_title
        lines.append(
            line_tpl.format(
                i=i,
                title=short_title,
                link=link,
                snippet=snippet[:300],
                route_hint=route_hint,
            )
        )

    repository.finish_research_step(
        step_id=step_id,
        status="done",
        output_json=json.dumps({"count": len(top)}, ensure_ascii=False),
    )
    repository.finish_research_run(run_id, status="done")

    header = tr_tools("tools.web_search.header", loc).format(route_mode=route_mode, run_id=run_id)
    return header + "\n\n".join(lines)
