"""Editorial focus — partner/customer briefing vs broad technical news.

When ``editorial.focus`` is ``product_updates`` (default), drop cyber-incident
and political stories unless they carry a clear product-release angle.
"""

from __future__ import annotations

import re

from . import util

# Product / feature angle — keep even if title smells like security press.
_RE_FLAGS = re.IGNORECASE

_PRODUCT_ANGLE_RE = re.compile(
    r"|".join([
        r"\bannounc(?:ing|es|ed)\b",
        r"\b(release notes|what'?s new|known issues)\b",
        r"\b(new feature|new capabilities|enhancement|improvement)s?\b",
        r"\b(generally available|now available|public beta|private preview)\b",
        r"\b(introduc(?:ing|es)|launch(?:es|ed)?)\b.{0,40}\b(splunk|cisco|thousandeyes|meraki|nexus|itsi)\b",
        r"\b(splunk enterprise|splunk cloud|splunk platform|splunk observability)\b",
        r"\b(cisco data fabric|nexus dashboard|hyperfabric|agenticops)\b",
        r"\bversion\s+\d+\.\d+",
        r"\bsplunk\b.{0,30}\b\d+\.\d+",
    ]),
    _RE_FLAGS,
)

# Incident / threat-intel / politics — out of scope for partner briefings.
_INCIDENT_DROP_RE = re.compile(
    r"|".join([
        r"\bcyber\s*attack",
        r"\bransomware\s+attack",
        r"\bdata\s+breach\b",
        r"\b(massive|major|huge)\s+breach\b",
        r"\bhack(?:ed|er|ing)\b.{0,40}\b(data|network|company|hospital|government)\b",
        r"\b(stolen|leaked)\s+(data|records|credentials|passwords)\b",
        r"\bnation[- ]state\b",
        r"\bstate[- ]sponsored\b",
        r"\bthreat actor",
        r"\bmalware campaign\b",
        r"\bzero[- ]day exploit\b",
        r"\b(active exploitation|exploited in the wild)\b",
        r"\b(victim of|targets?)\s+(ransomware|attack)\b",
        r"\bwar in\b",
        r"\b(election|presidential|congress|parliament|white house)\b",
        r"\b(geopolitic|sanctions against)\b",
        r"\bukraine\b.{0,30}\b(war|invasion|missile)\b",
        r"\bgaza\b",
        r"\bpolitic(?:al|s)\b.{0,30}\b(cyber|hack|attack)\b",
    ]),
    _RE_FLAGS,
)

# Sources that are almost exclusively threat research / incident write-ups.
_INCIDENT_SOURCE_IDS = frozenset({
    "cisco-talos-blog",
    "cisco-security-blog",
})


def focus(settings: dict) -> str:
    return ((settings.get("editorial") or {}).get("focus") or "product_updates").strip()


def is_product_updates_focus(settings: dict) -> bool:
    return focus(settings) == "product_updates"


def _haystack(item: dict) -> str:
    return util.collapse_ws(
        " ".join([
            item.get("title", ""),
            item.get("summary", ""),
            item.get("_snippet", ""),
        ])
    )


def has_product_update_angle(item: dict) -> bool:
    return bool(_PRODUCT_ANGLE_RE.search(_haystack(item)))


def is_incident_or_politics(item: dict, settings: dict) -> bool:
    """True if the story should be dropped under product_updates focus."""
    if not is_product_updates_focus(settings):
        return False

    ed = settings.get("editorial") or {}
    if not ed.get("drop_incidents", True) and not ed.get("drop_politics", True):
        return False

    sid = (item.get("source") or {}).get("id", "")
    if sid in _INCIDENT_SOURCE_IDS:
        return not has_product_update_angle(item)

    hay = _haystack(item)
    if not _INCIDENT_DROP_RE.search(hay):
        return False

    return not has_product_update_angle(item)


def category_rank_bonus(item: dict) -> int:
    """Importance modifier from categories (product_updates focus)."""
    cats = set(item.get("categories") or [])
    bonus = 0
    if "product-release" in cats:
        bonus += 25
    if "tutorial" in cats:
        bonus += 10
    if "standards" in cats:
        bonus += 4
    if "security" in cats:
        bonus -= 30
    if "research" in cats:
        bonus -= 12
    if "news" in cats and len(cats) == 1:
        bonus -= 6
    return bonus
