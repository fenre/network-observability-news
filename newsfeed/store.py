"""Persistence layer — "git is the database".

Reads/writes the committed source-of-truth JSON files in data/:
  * items.json        normalized + enriched items (the dataset)
  * enrich-cache.json content-hash -> LLM result (cost guard)
  * feed-cache.json   source id -> {etag, modified} (conditional GET)

Also: merge new items into the existing set, prune to the rolling window,
strip transient (``_``-prefixed) keys before writing, and validate against
schemas/item.schema.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config, ranking, util


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


# --- items -----------------------------------------------------------------

def load_items() -> list[dict]:
    data = _read_json(config.ITEMS_PATH, [])
    return data if isinstance(data, list) else []


def save_items(items: list[dict]) -> None:
    cleaned = [strip_transient(it) for it in items]
    cleaned.sort(key=_sort_key, reverse=True)
    _write_json(config.ITEMS_PATH, cleaned)


def strip_transient(item: dict) -> dict:
    """Drop all transient keys (``_fulltext``, ``_snippet`` ...). This is the
    guarantee that fetched article bodies are never persisted/republished."""
    return {
        k: v
        for k, v in item.items()
        if not k.startswith(util.TRANSIENT_PREFIX)
    }


def _sort_key(item: dict):
    return (item.get("publishedAt") or "", item.get("id") or "")


def _calendar_day(item: dict) -> str:
    """UTC calendar day for per-day caps (YYYY-MM-DD)."""
    for field in ("publishedAt", "fetchedAt"):
        raw = item.get(field) or ""
        if len(raw) >= 10:
            return raw[:10]
    return "_unknown"


def merge_items(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Union by id. Existing items win on stable fields (id, first fetchedAt,
    persisted summary) but are refreshed with the latest transient snippet/
    full-text from this run so re-enrichment can upgrade them."""
    by_id: dict[str, dict] = {it["id"]: it for it in existing}
    for inc in incoming:
        iid = inc["id"]
        if iid in by_id:
            cur = by_id[iid]
            # Refresh transient inputs from this run (for re-enrich/classify).
            for k in ("_snippet", "_fulltext", "_feed_tags", "_source_topics"):
                if k in inc:
                    cur[k] = inc[k]
            # Keep earliest fetchedAt; refresh title/url if publisher changed.
            cur["title"] = inc.get("title", cur.get("title"))
            cur["url"] = inc.get("url", cur.get("url"))
            cur["source"] = inc.get("source", cur.get("source"))
            cur["attribution"] = inc.get("attribution", cur.get("attribution"))
            if inc.get("publishedAt"):
                cur["publishedAt"] = inc["publishedAt"]
            if inc.get("canonicalUrl"):
                cur["canonicalUrl"] = inc["canonicalUrl"]
            if inc.get("categories"):
                cur["categories"] = inc["categories"]
            if inc.get("topics"):
                cur["topics"] = inc["topics"]
            if inc.get("summary") and inc.get("summarySource") == "curated":
                cur["summary"] = inc["summary"]
                cur["summarySource"] = "curated"
            if inc.get("tags"):
                cur["tags"] = inc["tags"]
            if inc.get("audiences"):
                cur["audiences"] = inc["audiences"]
            if inc.get("agentNote"):
                cur["agentNote"] = inc["agentNote"]
        else:
            by_id[iid] = inc
    return list(by_id.values())


def _cap_per_calendar_day(items: list[dict], per_day: int, settings: dict) -> list[dict]:
    """Keep at most *per_day* items per UTC day (importance-ranked, not random)."""
    if per_day <= 0:
        return items
    buckets: dict[str, list[dict]] = {}
    for it in items:
        buckets.setdefault(_calendar_day(it), []).append(it)
    kept: list[dict] = []
    for group in buckets.values():
        kept.extend(ranking.select_for_day(group, per_day, settings))
    kept.sort(key=_sort_key, reverse=True)
    return kept


def prune(items: list[dict], settings: dict, *, log=None) -> list[dict]:
    """Apply retention window, per-day cap, then global max_items."""
    retention = settings.get("retention", {}) or {}
    days = int(retention.get("days", 365))
    max_items = int(retention.get("max_items", 4000))
    per_day = int(retention.get("max_items_per_day", 0))

    cutoff = util.now_utc().timestamp() - days * 86400
    kept = []
    for it in items:
        sid = (it.get("source") or {}).get("id", "")
        if sid.startswith("curated-"):
            kept.append(it)
            continue
        dt = util.parse_iso(it.get("publishedAt"))
        if dt is None or dt.timestamp() >= cutoff:
            kept.append(it)
    age_dropped = len(items) - len(kept)

    before_daily = len(kept)
    kept = _cap_per_calendar_day(kept, per_day, settings)
    daily_dropped = before_daily - len(kept)

    kept.sort(key=_sort_key, reverse=True)
    before_max = len(kept)
    if max_items > 0:
        kept = kept[:max_items]
    max_dropped = before_max - len(kept)

    cap_method = (retention.get("daily_cap_method") or "importance").strip().lower()

    if log:
        if age_dropped:
            log(f"  retention: dropped {age_dropped} older than {days} day(s)")
        if daily_dropped and per_day > 0:
            log(
                f"  retention: capped {daily_dropped} over {per_day}/day "
                f"({cap_method} ranking; releases outside cap)"
            )
        if max_dropped:
            log(f"  retention: dropped {max_dropped} over global max ({max_items})")

    return kept


# --- caches ----------------------------------------------------------------

def load_enrich_cache() -> dict:
    data = _read_json(config.ENRICH_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def save_enrich_cache(cache: dict) -> None:
    _write_json(config.ENRICH_CACHE_PATH, cache)


def load_feed_cache() -> dict:
    data = _read_json(config.FEED_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def save_feed_cache(cache: dict) -> None:
    _write_json(config.FEED_CACHE_PATH, cache)


# --- validation ------------------------------------------------------------

def validate_items(items: list[dict], *, log=lambda *_: None) -> int:
    """Validate persisted (transient-stripped) items against the schema.

    Returns the number of invalid items (0 == all valid). Missing jsonschema
    or schema file is a soft skip.
    """
    schema = config.load_item_schema()
    if schema is None:
        log("INFO: item schema not found; skipping validation.")
        return 0
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        log("INFO: jsonschema not installed; skipping validation.")
        return 0

    validator = Draft202012Validator(schema)
    invalid = 0
    for it in items:
        clean = strip_transient(it)
        errors = sorted(validator.iter_errors(clean), key=lambda e: e.path)
        if errors:
            invalid += 1
            first = errors[0]
            loc = "/".join(str(p) for p in first.path) or "(root)"
            log(f"WARN: item {it.get('id')} invalid at {loc}: {first.message}")
    if not invalid:
        log(f"  schema: all {len(items)} items valid")
    else:
        log(f"WARN: {invalid}/{len(items)} items failed schema validation")
    return invalid
