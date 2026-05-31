"""Source ingestion.

Two stages:

1. Feed parsing (always): ``feedparser`` reads each source feed with an
   identifiable User-Agent and conditional-GET caching (ETag / Last-Modified).
   A 304 Not Modified short-circuits the source.

2. Full-text (only for sources marked ``fulltext: allow``): for each new
   entry we consult the publisher's ``robots.txt`` via
   ``urllib.robotparser``; if our UA is allowed we fetch the page with a
   polite per-host delay and extract the main text with ``trafilatura``.

The extracted body is attached to the in-memory entry under a transient
``_fulltext`` key purely as input to summarisation. It is NEVER persisted to
data/items.json and never republished (store.strip_transient enforces this).

All heavy imports are lazy so the rest of the pipeline (and the build) run
even when these libraries are not installed.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from . import util


def _noop(*_args, **_kwargs):
    pass


def _entry_link(entry) -> str | None:
    link = entry.get("link")
    if link:
        return link
    for ln in entry.get("links", []) or []:
        if ln.get("rel") in (None, "alternate") and ln.get("href"):
            return ln["href"]
    return None


def _entry_snippet(entry) -> str:
    """Best-effort feed-provided summary/snippet (used for extractive fallback
    and as LLM input when full text is unavailable)."""
    if entry.get("summary"):
        return entry["summary"]
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return content[0]["value"]
    return ""


def _raw_entry(entry, source: dict) -> dict:
    link = _entry_link(entry)
    # Google News aggregated entries carry the real publisher in entry.source.
    publisher = None
    src_obj = entry.get("source") or {}
    if isinstance(src_obj, dict):
        publisher = src_obj.get("title")

    author = entry.get("author") or None

    published_iso = (
        util.struct_time_to_iso(entry.get("published_parsed"))
        or util.struct_time_to_iso(entry.get("updated_parsed"))
    )

    tags = []
    for t in entry.get("tags", []) or []:
        term = t.get("term") if isinstance(t, dict) else None
        if term:
            tags.append(term)

    return {
        "title": entry.get("title", ""),
        "link": link,
        "author": author,
        "published_iso": published_iso,
        "publisher": publisher,
        "feed_tags": tags,
        "_snippet": _entry_snippet(entry),
        "_fulltext": "",
        "_source": source,
    }


def fetch_sources(
    sources: list[dict],
    settings: dict,
    *,
    allow_fulltext: bool = True,
    feed_cache: dict | None = None,
    log=_noop,
) -> tuple[list[dict], dict]:
    """Fetch all sources. Returns ``(raw_entries, updated_feed_cache)``.

    Network/parse failures are caught per-source so one bad feed never aborts
    the run.
    """
    try:
        import feedparser
    except ImportError:
        log("WARN: feedparser not installed; skipping all feed fetches.")
        return [], (feed_cache or {})

    fetch_cfg = settings.get("fetch", {})
    user_agent = fetch_cfg.get("user_agent", "network-observability-news-bot")
    max_items = int(fetch_cfg.get("max_items_per_feed", 60))

    cache = dict(feed_cache or {})
    raw_entries: list[dict] = []

    for source in sources:
        feed_url = source["feed"]
        sid = source["id"]
        prev = cache.get(sid, {}) if isinstance(cache.get(sid), dict) else {}
        try:
            parsed = feedparser.parse(
                feed_url,
                agent=user_agent,
                etag=prev.get("etag"),
                modified=prev.get("modified"),
            )
        except Exception as exc:  # noqa: BLE001 - never let one feed kill the run
            log(f"WARN: failed to parse feed {sid} ({feed_url}): {exc}")
            continue

        status = getattr(parsed, "status", None)
        if status == 304:
            log(f"  {sid}: 304 Not Modified")
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            exc = getattr(parsed, "bozo_exception", "unknown")
            log(f"WARN: {sid}: malformed feed / no entries ({exc})")
            # still fall through in case entries exist

        new_cache: dict = {}
        if getattr(parsed, "etag", None):
            new_cache["etag"] = parsed.etag
        if getattr(parsed, "modified", None):
            new_cache["modified"] = parsed.modified
        if new_cache:
            cache[sid] = new_cache

        entries = list(parsed.entries or [])[:max_items]
        count = 0
        for entry in entries:
            raw = _raw_entry(entry, source)
            if not raw["link"]:
                continue
            raw_entries.append(raw)
            count += 1
        log(f"  {sid}: {count} entries"
            f"{' (status ' + str(status) + ')' if status else ''}")

    if allow_fulltext:
        _maybe_fetch_fulltext(raw_entries, settings, log=log)

    return raw_entries, cache


def _maybe_fetch_fulltext(raw_entries: list[dict], settings: dict, *, log=_noop) -> None:
    """For ``fulltext: allow`` sources only: robots-gated, polite full-text
    fetch + extraction. Mutates entries in place by setting ``_fulltext``."""
    targets = [
        e for e in raw_entries
        if str(e["_source"].get("fulltext", "deny")).lower() == "allow"
    ]
    if not targets:
        return

    try:
        import httpx
        import trafilatura
    except ImportError:
        log("INFO: httpx/trafilatura not installed; skipping full-text fetch.")
        return

    fetch_cfg = settings.get("fetch", {})
    user_agent = fetch_cfg.get("user_agent", "network-observability-news-bot")
    timeout = float(fetch_cfg.get("request_timeout_seconds", 20))
    delay = float(fetch_cfg.get("polite_delay_seconds", 2.0))

    robots_cache: dict[str, RobotFileParser | None] = {}
    last_fetch_at: dict[str, float] = {}

    with httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        for entry in targets:
            url = entry["link"]
            parts = urlsplit(url)
            host = parts.netloc

            if not _robots_allows(robots_cache, parts, user_agent, log=log):
                log(f"  robots.txt disallows full-text fetch: {url}")
                continue

            # Polite per-host delay.
            wait = delay - (time.monotonic() - last_fetch_at.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)

            try:
                resp = client.get(url)
                last_fetch_at[host] = time.monotonic()
                if resp.status_code != 200 or not resp.text:
                    continue
                extracted = trafilatura.extract(
                    resp.text,
                    url=url,
                    include_comments=False,
                    include_tables=False,
                    favor_precision=True,
                )
                if extracted:
                    # Transient only — consumed by enrich, never persisted.
                    entry["_fulltext"] = util.collapse_ws(extracted)
                    log(f"  full-text OK ({len(entry['_fulltext'])} chars): {url}")
            except Exception as exc:  # noqa: BLE001
                log(f"WARN: full-text fetch failed for {url}: {exc}")


def _robots_allows(cache, parts, user_agent: str, *, log=_noop) -> bool:
    host_key = f"{parts.scheme}://{parts.netloc}"
    rp = cache.get(host_key, "missing")
    if rp == "missing":
        rp = RobotFileParser()
        rp.set_url(f"{host_key}/robots.txt")
        try:
            rp.read()
        except Exception as exc:  # noqa: BLE001 - treat unreadable robots as allow
            log(f"INFO: could not read robots.txt for {host_key}: {exc}")
            rp = None
        cache[host_key] = rp
    if rp is None:
        return True
    try:
        return rp.can_fetch(user_agent, parts.geturl())
    except Exception:  # noqa: BLE001
        return True
