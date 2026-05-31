"""Partner briefing highlights — pinned Splunk platform stories."""

from __future__ import annotations

import re

from .must_keep import is_must_keep

# Must match at least one — keeps the strip focused on platform drops, not generic pages.
_BRIEFING_KEYWORDS = (
    "10.4",
    "dashboard studio",
    "federated search",
    "splunk platform 10",
    "announcing splunk cloud",
    "announcing splunk enterprise",
    "splunk enterprise 10.4",
    "splunk cloud platform 10.4",
)


def _score(item: dict) -> int:
    hay = " ".join([
        item.get("title", ""),
        item.get("summary", ""),
        " ".join(item.get("tags") or []),
    ]).lower()
    score = 0
    if "product-release" in (item.get("categories") or []):
        score += 100
    if "splunk" in (item.get("topics") or []):
        score += 50
    if is_must_keep(item, {"retention": {"must_keep": {"enabled": True}}}):
        score += 40
    canon = item.get("canonicalUrl") or item.get("url") or ""
    if "splunk.com" in canon or "docs.splunk.com" in canon:
        score += 30
    for kw in _BRIEFING_KEYWORDS:
        if kw in hay:
            score += 25
    if item.get("summarySource") == "curated":
        score += 20
    return score


def pick_highlights(items: list[dict], settings: dict, *, limit: int = 12) -> list[str]:
    """Return item ids for the briefing highlights strip (newest among top scores)."""
    ed = (settings.get("editorial") or {})
    if not ed.get("highlights_enabled", True):
        return []

    limit = int(ed.get("highlights_limit", limit))
    candidates = [
        it for it in items
        if "splunk" in (it.get("topics") or [])
        and (
            "product-release" in (it.get("categories") or [])
            or "tutorial" in (it.get("categories") or [])
        )
    ]
    ranked = sorted(
        candidates,
        key=lambda it: (_score(it), it.get("publishedAt") or "", it.get("id") or ""),
        reverse=True,
    )

    seen_ids: set[str] = set()
    seen_canon: set[str] = set()
    ids: list[str] = []
    for it in ranked:
        iid = it.get("id")
        if not iid or iid in seen_ids:
            continue
        hay = (it.get("title", "") + " " + it.get("summary", "")).lower()
        canon = (it.get("canonicalUrl") or "").rstrip("/").lower()
        if canon in seen_canon:
            continue
        has_kw = any(kw in hay for kw in _BRIEFING_KEYWORDS)
        if not (has_kw or it.get("summarySource") == "curated"):
            continue
        seen_ids.add(iid)
        if canon:
            seen_canon.add(canon)
        ids.append(iid)
        if len(ids) >= limit:
            break
    return ids
