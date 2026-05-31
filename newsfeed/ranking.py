"""Rank items for retention caps — keep the most important stories per day.

Product releases (must-keep) are outside the per-day numeric cap entirely.
"""

from __future__ import annotations

from . import editorial, relevance
from .must_keep import is_must_keep

# Feeds that get reserved daily-cap slots (Splunk/Cisco/operator engineering).
_PRIORITY_SOURCE_IDS = frozenset({
    "cisco-networking-blog",
    "cisco-datacenter-blog",
    "cisco-tag-splunk",
    "cisco-meraki-blog",
    "cisco-developer-blog",
    "ntop-blog",
    "opentelemetry-blog",
    "zeek-blog",
    "cloudflare-blog",
    "gnews-splunk-blog",
    "gnews-splunk-platform",
    "gnews-splunk-dev",
})


def _source_id(item: dict) -> str:
    return (item.get("source") or {}).get("id", "")


def is_priority_source(item: dict, settings: dict) -> bool:
    """Engineering-first feeds that should survive daily caps."""
    sid = _source_id(item)
    if sid in _PRIORITY_SOURCE_IDS:
        return True
    extra = (settings.get("retention") or {}).get("priority_source_ids") or []
    return sid in extra


def importance_score(item: dict, settings: dict) -> int:
    """Higher = more worth keeping when a calendar day is over the cap."""
    if is_must_keep(item, settings):
        return 10_000

    score = relevance.technical_score(item)

    sid = _source_id(item)
    if sid in _PRIORITY_SOURCE_IDS:
        score += 20
    elif relevance._publisher_trusted(item):
        score += 8
    elif sid.startswith("gnews-"):
        score -= 2

    topics = item.get("topics") or []
    score += min(len(topics) * 2, 6)
    if "splunk" in topics:
        score += 6
    if "cisco-data-fabric" in topics:
        score += 5
    if "network-observability" in topics:
        score += 3

    if not sid.startswith("gnews-"):
        score += 2

    if editorial.is_product_updates_focus(settings):
        score += editorial.category_rank_bonus(item)

    return score


def _rank_key(item: dict, settings: dict):
    return (
        importance_score(item, settings),
        item.get("publishedAt") or "",
        item.get("id") or "",
    )


def _select_capped(rest: list[dict], per_day: int, settings: dict) -> list[dict]:
    """Apply per_day limit to non-release items only."""
    if per_day <= 0 or len(rest) <= per_day:
        return list(rest)

    retention = settings.get("retention") or {}
    method = (retention.get("daily_cap_method") or "importance").strip().lower()

    if method == "published_at":
        rest.sort(
            key=lambda it: (it.get("publishedAt") or "", it.get("id") or ""),
            reverse=True,
        )
        return rest[:per_day]

    reserved = int(retention.get("priority_reserved_slots", 6))
    reserved = min(reserved, per_day)

    priority = [it for it in rest if is_priority_source(it, settings)]
    other = [it for it in rest if not is_priority_source(it, settings)]
    priority.sort(key=lambda it: _rank_key(it, settings), reverse=True)
    other.sort(key=lambda it: _rank_key(it, settings), reverse=True)

    kept: list[dict] = []
    seen: set[str] = set()

    def _add(candidates: list[dict], limit: int) -> None:
        for it in candidates:
            if limit <= 0:
                return
            iid = it.get("id")
            if not iid or iid in seen:
                continue
            seen.add(iid)
            kept.append(it)
            limit -= 1

    def _slots_left() -> int:
        return max(0, per_day - len(kept))

    _add(priority, min(reserved, _slots_left()))
    _add(other, _slots_left())
    _add(priority[reserved:], _slots_left())

    kept.sort(key=lambda it: _rank_key(it, settings), reverse=True)
    return kept


def select_for_day(group: list[dict], per_day: int, settings: dict) -> list[dict]:
    """Keep capped stories + all product releases (releases do not use cap slots)."""
    if per_day <= 0:
        return list(group)

    pinned = [it for it in group if is_must_keep(it, settings)]
    rest = [it for it in group if not is_must_keep(it, settings)]

    capped = _select_capped(rest, per_day, settings)
    kept = pinned + capped
    kept.sort(key=lambda it: _rank_key(it, settings), reverse=True)
    return kept
