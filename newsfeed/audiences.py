"""Audience tagging for agent-first regional briefings (Nordics NO/DK)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from . import config, util

_DEFAULT_AUDIENCES = ["global"]

# Compiled at runtime from config/audiences.yaml
_nordics_re: re.Pattern | None = None
_nordics_no_re: re.Pattern | None = None
_nordics_dk_re: re.Pattern | None = None
_regulatory_re: re.Pattern | None = None
_nordic_source_ids: frozenset[str] = frozenset()
_talking_points: list[str] = []


def _compile_patterns(settings: dict) -> None:
    global _nordics_re, _nordics_no_re, _nordics_dk_re, _regulatory_re
    global _nordic_source_ids, _talking_points

    doc = config.load_audiences_config()
    signals = doc.get("signals") or {}

    def _pat(key: str) -> re.Pattern:
        parts = signals.get(key) or []
        if not parts:
            return re.compile(r"(?!)")
        return re.compile("|".join(f"(?:{p})" for p in parts), re.IGNORECASE)

    _nordics_re = _pat("nordics")
    _nordics_no_re = _pat("nordics_no")
    _nordics_dk_re = _pat("nordics_dk")
    reg = signals.get("regulatory") or []
    _regulatory_re = (
        re.compile("|".join(f"(?:{p})" for p in reg), re.IGNORECASE)
        if reg else re.compile(r"(?!)")
    )

    src = doc.get("nordic_source_ids") or []
    _nordic_source_ids = frozenset(str(s) for s in src)

    _talking_points = [
        str(p).strip() for p in (doc.get("talkingPoints") or []) if str(p).strip()
    ]

    # Merge boost ids into settings retention if present (optional hook).
    boost = doc.get("boost_source_ids") or []
    if boost:
        retention = settings.setdefault("retention", {})
        pri = list(retention.get("priority_source_ids") or [])
        for sid in boost:
            if sid not in pri:
                pri.append(sid)
        retention["priority_source_ids"] = pri


def talking_points() -> list[str]:
    return list(_talking_points)


def _haystack(item: dict) -> str:
    return util.collapse_ws(
        " ".join([
            item.get("title", ""),
            item.get("summary", ""),
            item.get("_snippet", ""),
            item.get("canonicalUrl", ""),
            item.get("url", ""),
        ])
    ).lower()


def _host_tld(item: dict) -> str:
    for field in ("canonicalUrl", "url"):
        raw = item.get(field) or ""
        try:
            host = (urlsplit(raw).hostname or "").lower()
        except ValueError:
            continue
        if host.endswith(".no"):
            return "no"
        if host.endswith(".dk"):
            return "dk"
    return ""


def _has_splunk_cisco_context(hay: str) -> bool:
    return bool(re.search(
        r"\b(splunk|cisco|observability|itsi|thousandeyes|nexus|meraki|signalfx)\b",
        hay,
        re.IGNORECASE,
    ))


def ensure_default_audiences(item: dict) -> None:
    """Guarantee schema-valid audiences on every item (incl. legacy rows)."""
    aud = item.get("audiences")
    if not aud or not isinstance(aud, list):
        item["audiences"] = list(_DEFAULT_AUDIENCES)
        return
    cleaned = [a for a in aud if a in config.VALID_AUDIENCES]
    if "global" not in cleaned:
        cleaned.insert(0, "global")
    item["audiences"] = cleaned or list(_DEFAULT_AUDIENCES)


def assign_audiences(items: list[dict], settings: dict, *, log=print) -> None:
    """Tag items with nordics / nordics-no / nordics-dk after classify."""
    _compile_patterns(settings)
    tagged = 0
    for it in items:
        ensure_default_audiences(it)
        aud = set(it["audiences"])
        hay = _haystack(it)
        sid = (it.get("source") or {}).get("id", "")

        if sid in _nordic_source_ids:
            aud.add("nordics")

        if _nordics_re and _nordics_re.search(hay):
            aud.add("nordics")

        tld = _host_tld(it)
        if tld == "no":
            aud.add("nordics-no")
            aud.add("nordics")
        elif tld == "dk":
            aud.add("nordics-dk")
            aud.add("nordics")

        if sid.endswith("-no") or sid.endswith("-nordics-no"):
            aud.add("nordics-no")
            aud.add("nordics")
        if sid.endswith("-dk") or sid.endswith("-nordics-dk"):
            aud.add("nordics-dk")
            aud.add("nordics")

        if _nordics_no_re and _nordics_no_re.search(hay):
            aud.add("nordics-no")
            aud.add("nordics")
        if _nordics_dk_re and _nordics_dk_re.search(hay):
            aud.add("nordics-dk")
            aud.add("nordics")

        if _regulatory_re and _regulatory_re.search(hay) and _has_splunk_cisco_context(hay):
            aud.add("nordics")

        it["audiences"] = [a for a in config.VALID_AUDIENCES if a in aud]
        if "nordics" in it["audiences"] or "nordics-no" in it["audiences"] or "nordics-dk" in it["audiences"]:
            tagged += 1

    if log:
        log(f"  audiences: {tagged} item(s) tagged for Nordics (NO/DK)")


def is_nordics_item(item: dict) -> bool:
    aud = set(item.get("audiences") or [])
    return bool(aud & {"nordics", "nordics-no", "nordics-dk"})
