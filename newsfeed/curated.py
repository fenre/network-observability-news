"""Curated canonical URLs — direct Splunk/docs links for partner briefings."""

from __future__ import annotations

from difflib import SequenceMatcher

from . import config, util
from .normalize import canonical_url

_CURATED_SOURCE = {
    "id": "curated-splunk-platform",
    "name": "Splunk (canonical)",
    "homepage": "https://www.splunk.com/en_us/blog/platform",
    "type": "curated",
    "topics": ["splunk"],
    "fulltext": "allow",
    "license": None,
    "enabled": True,
}


def load_curated_entries(settings: dict, *, log=print) -> list[dict]:
    """Return raw entries compatible with normalize()."""
    path = config.CURATED_PATH
    if not path.is_file():
        return []

    ed = (settings.get("editorial") or {})
    if not ed.get("curated_enabled", True):
        return []

    data = config._load_yaml(path) or {}
    rows = data.get("curated") or []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get("url") or "").strip()
        title = util.collapse_ws(row.get("title") or "")
        if not url or not title:
            continue
        out.append({
            "title": title,
            "link": url,
            "author": None,
            "published_iso": row.get("publishedAt") or util.now_utc_iso(),
            "publisher": "Splunk",
            "feed_tags": row.get("tags") or [],
            "_snippet": row.get("summary") or "",
            "_fulltext": "",
            "_source": dict(_CURATED_SOURCE),
            "_curated_id": row.get("id") or "",
            "_curated_topics": row.get("topics") or ["splunk"],
            "_curated_categories": row.get("categories") or ["product-release"],
            "_curated_summary": row.get("summary") or "",
            "_curated_tags": row.get("tags") or [],
        })
    if out:
        log(f"  curated: {len(out)} canonical Splunk platform page(s)")
    return out


def _title_ratio(a: str, b: str) -> float:
    na, nb = util.normalize_title(a), util.normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def apply_curated_overrides(items: list[dict], curated_raw: list[dict], *, log=print) -> None:
    """Replace Google News stubs with splunk.com URLs when titles match."""
    if not curated_raw:
        return

    canon_by_title: list[tuple[str, dict]] = []
    for raw in curated_raw:
        canon = canonical_url(raw.get("link") or "")
        canon_by_title.append((raw.get("title", ""), {
            "url": raw.get("link"),
            "canonicalUrl": canon,
            "summary": raw.get("_curated_summary") or raw.get("_snippet") or "",
            "summarySource": "curated",
            "source": {
                "id": _CURATED_SOURCE["id"],
                "name": _CURATED_SOURCE["name"],
                "homepage": _CURATED_SOURCE["homepage"],
            },
            "attribution": "via Splunk (canonical)",
            "tags": list(raw.get("_curated_tags") or []),
            "categories": list(raw.get("_curated_categories") or ["product-release"]),
        }))

    upgraded = 0
    for it in items:
        if (it.get("source") or {}).get("id") == _CURATED_SOURCE["id"]:
            continue
        title = it.get("title", "")
        for ct, patch in canon_by_title:
            if _title_ratio(title, ct) < 0.72:
                continue
            if "splunk" not in (it.get("topics") or []):
                continue
            it.update(patch)
            if "product-release" not in (it.get("categories") or []):
                it.setdefault("categories", []).append("product-release")
            upgraded += 1
            break

    if upgraded:
        log(f"  curated: upgraded {upgraded} feed item(s) to splunk.com URLs")


def normalize_curated_extras(item: dict, raw: dict) -> None:
    """After classify — lock in curated topics/categories/summary."""
    if (raw.get("_source") or {}).get("type") != "curated":
        return
    topics = raw.get("_curated_topics") or ["splunk"]
    item["topics"] = [t for t in topics if t in config.VALID_TOPICS] or ["splunk"]
    cats = raw.get("_curated_categories") or ["product-release"]
    item["categories"] = [c for c in cats if c in config.VALID_CATEGORIES] or ["product-release"]
    summary = raw.get("_curated_summary") or ""
    if summary:
        item["summary"] = util.collapse_ws(summary)
        item["summarySource"] = "curated"
    tags = raw.get("_curated_tags") or []
    if tags:
        item["tags"] = sorted({*(item.get("tags") or []), *tags})
