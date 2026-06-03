import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from infrastructure.locale.i18n import tr_tools
from infrastructure.memory.repo import normalize_research_url

try:
    import trafilatura
except ImportError:
    trafilatura = None


def _domain_from_url(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _route_for_url(url: str, route_mode: str) -> str:
    mode = (route_mode or "auto").strip().lower()
    if mode in ("default", "ru"):
        return mode
    d = _domain_from_url(url)
    if d.endswith(".ru") or d.endswith(".su") or ".ru." in d:
        return "ru"
    return "default"


def _extract_text_naive(html: str) -> str:
    no_script = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    no_tags = re.sub(r"(?s)<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", no_tags).strip()


def _extract_text(html: str, page_url: str | None = None) -> str:
    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                html,
                url=page_url or None,
                include_comments=False,
                include_tables=True,
                favor_precision=False,
            )
            if extracted:
                compact = re.sub(r"\s+", " ", extracted).strip()
                if len(compact) > 80:
                    return compact
        except Exception:
            pass
    return _extract_text_naive(html)


def _serp_followup_block(repository, run_id, session_id, locale: str) -> str:
    loc = str(locale or "ru")
    if not run_id or not session_id:
        return ""
    if not hasattr(repository, "get_saved_web_search_results_for_run"):
        return ""
    rows = repository.get_saved_web_search_results_for_run(run_id, session_id)
    if rows is None or not rows:
        return ""
    if not hasattr(repository, "get_fetch_urls_normalized_for_run"):
        return ""
    done = repository.get_fetch_urls_normalized_for_run(run_id)
    no_title = tr_tools("tools.web_search.no_title", loc)
    line_tpl = tr_tools("tools.web_fetch.serp_line", loc)
    chunks = []
    seen = set()
    for url, title, _rank, snip in rows:
        u = (url or "").strip()
        if not u:
            continue
        nu = normalize_research_url(u)
        if nu in done or nu in seen:
            continue
        seen.add(nu)
        t = ((title or "").strip() or no_title)[:120]
        sn = ((snip or "").strip())[:260]
        chunks.append(line_tpl.format(title=t, url=u, sn=sn))
    header = tr_tools("tools.web_fetch.serp_tail", loc).format(run_id=run_id)
    if chunks:
        return header + "\n".join(chunks)
    return header + tr_tools("tools.web_fetch.serp_done", loc)


def web_fetch_url(
    url,
    repository,
    semantic_engine=None,
    window=None,
    run_id=None,
    route_mode="auto",
    locale="ru",
    **kwargs,
):
    """Fetch page by URL, extract text, persist in research pipeline."""
    loc = str(locale or "ru")
    target_url = (url or "").strip()
    if not target_url:
        print("[TOOL] web_fetch_url: empty url — exit")
        return tr_tools("tools.web_fetch.empty_url", loc)
    if window is None:
        print("[TOOL] web_fetch_url: window is None — exit")
        return tr_tools("tools.web_fetch.no_window", loc)

    route = _route_for_url(target_url, route_mode)
    proxy_url = window.get_proxy_url(route_mode=route)

    sid = window.session_controller.current_session_id
    if not run_id:
        run_id = repository.get_last_research_run_id(sid)
    if not run_id:
        run_id = repository.create_research_run(
            session_id=sid,
            user_query_raw=f"fetch:{target_url}",
            user_query_norm=f"fetch:{target_url.lower()}",
            mode="news",
            status="running",
            retention_class="warm",
            expires_at=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
        )

    print(
        f"[TOOL] web_fetch_url: start url={target_url!r} route={route!r} "
        f"route_mode={route_mode!r} run_id={run_id!r} session_id={sid!r} proxy={'yes' if proxy_url else 'no'}"
    )

    step_id = repository.add_research_step(
        run_id=run_id,
        step_type="fetch_url",
        input_json=json.dumps({"url": target_url, "route": route}, ensure_ascii=False),
        status="running",
    )
    print(f"[TOOL] web_fetch_url: research_step_id={step_id!r}")

    try:
        req = Request(
            target_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Lira2/1.0)",
                "Accept-Language": "ru,en;q=0.8",
            },
        )
        if proxy_url:
            opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
            resp = opener.open(req, timeout=12)
        else:
            opener = build_opener()
            resp = opener.open(req, timeout=12)
        with resp:
            ctype = resp.headers.get("content-type", "")
            raw = resp.read()
        print(f"[TOOL] web_fetch_url: HTTP ok bytes={len(raw)} content-type={ctype!r}")
        html = raw.decode("utf-8", errors="ignore")
        text = _extract_text(html, page_url=target_url)
        short = text[:1200]
    except Exception as e:
        print(f"[TOOL] web_fetch_url: fetch failed step_id={step_id!r} error={e!r}")
        repository.finish_research_step(
            step_id=step_id,
            status="failed",
            error_text=str(e),
            output_json=json.dumps({"error": str(e)}, ensure_ascii=False),
        )
        tail = _serp_followup_block(repository, run_id, sid, loc)
        return tr_tools("tools.web_fetch.error", loc).format(e=e) + tail

    domain = _domain_from_url(target_url)
    source_id = repository.upsert_source(
        source_kind="domain",
        source_key=domain or "unknown",
        display_name=domain or "unknown",
    )
    artifact_id = repository.add_artifact(
        source_id=source_id,
        origin_type="web_fetch",
        media_type="text",
        url=target_url,
        canonical_url=target_url,
        title=target_url,
        author="web_fetch_url",
        language="ru",
        content_hash=f"{domain}|{target_url}",
        retention_class="warm",
        expires_at=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    repository.add_artifact_payload(
        artifact_id=artifact_id,
        text_content=text[:20000],
        compressed_text=short,
        summary_short=short,
        keywords_json=json.dumps([f"route:{route}", f"content_type:{ctype}"], ensure_ascii=False),
    )
    repository.link_run_artifact(run_id, artifact_id, step_id=step_id, rank_score=0.0, is_selected_for_report=1)
    repository.finish_research_step(
        step_id=step_id,
        status="done",
        output_json=json.dumps({"url": target_url, "chars": len(text), "route": route}, ensure_ascii=False),
    )
    repository.finish_research_run(run_id, status="done")

    print(
        f"[TOOL] web_fetch_url: done artifact_id={artifact_id!r} source_id={source_id!r} "
        f"text_chars={len(text)} preview={short[:200]!r}..."
    )

    body = tr_tools("tools.web_fetch.loaded", loc).format(route=route, n=len(text), url=target_url, short=short)
    return body + _serp_followup_block(repository, run_id, sid, loc)
