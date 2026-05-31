"""Must-keep detection — product releases and other non-negotiable stories.

Matched items bypass the technical-audience filter and the per-day cap.
They still respect the age window (retention.days) and global max_items.
"""

from __future__ import annotations

import re

from . import util

# Title/summary patterns for Splunk & Cisco platform releases (case-insensitive).
_DEFAULT_PATTERNS: tuple[str, ...] = (
    # Splunk platform / suite releases
    r"(?i)\bannounc(?:ing|es|ed)\b.{0,80}\bsplunk\b",
    r"(?i)\bwelcome\s+to\s+splunk\b.{0,40}\b\d+\.\d+(?:\.\d+)?\b",
    r"(?i)\b(splunk\s+enterprise|splunk\s+cloud\s+platform|splunk\s+platform)\b"
    r".{0,40}\b\d+\.\d+(?:\.\d+)?\b",
    r"(?i)\bsplunk\b.{0,80}\b(release[sd]?|released|launch(?:ed|es)?|generally available|"
    r"now available|is available|shipping|introduc(?:es|ing|ed)|announc(?:ing|es|ed))\b",
    r"(?i)\b(release notes|what'?s new|known issues)\b.{0,60}\b(splunk|itsi|"
    r"observability cloud|enterprise security|soar|edge processor|signalfx|signal fx|"
    r"data fabric|splunk enterprise|splunk cloud)\b",
    r"(?i)\b(splunk|itsi|observability cloud|enterprise security|soar|edge processor|"
    r"signalfx)\b.{0,60}\b(version\s+\d|v\d+\.\d+|\d+\.\d+(?:\.\d+)?(?:\s+release)?)\b",
    r"(?i)\bsplunk\b.{0,50}\b\d+\.\d+(?:\.\d+)?\b",
    r"(?i)\bwhat'?s new\b.{0,60}\b(splunk|cisco|itsi|observability|thousandeyes)\b",
    r"(?i)\b(new feature|new capabilities|enhancement)s?\b.{0,50}\b(splunk|cisco|itsi|"
    r"thousandeyes|meraki|nexus|edge processor)\b",
    r"(?i)\b(splunk|cisco|itsi|thousandeyes)\b.{0,50}\b(new feature|new capabilities|"
    r"enhancement|public beta|private preview)\b",
    # Cisco networking / data center / observability product versions
    r"(?i)\bcisco\b.{0,80}\b(release[sd]?|released|launch(?:ed|es)?|generally available|"
    r"now available|introduc(?:es|ing|ed))\b.{0,40}\b(nexus|aci|sd-?wan|thousandeyes|"
    r"meraki|hyperfabric|data fabric|splunk)\b",
    r"(?i)\b(nexus|aci|sd-?wan|thousandeyes|meraki)\b.{0,60}\b(version\s+\d|v\d+\.\d+|"
    r"\d+\.\d+(?:\.\d+)?)\b",
    r"(?i)\bthousandeyes\b.{0,50}\b(release[sd]?|generally available|new features)\b",
    r"(?i)\b(cisco data fabric|machine data fabric)\b.{0,60}\b(launch|available|release)\b",
)

_COMPILED_DEFAULT: list[re.Pattern[str]] | None = None


def _compiled_patterns(settings: dict) -> list[re.Pattern[str]]:
    global _COMPILED_DEFAULT
    if _COMPILED_DEFAULT is None:
        _COMPILED_DEFAULT = [re.compile(p) for p in _DEFAULT_PATTERNS]

    mk = (settings.get("retention") or {}).get("must_keep") or {}
    if not mk.get("enabled", True):
        return []

    extra = mk.get("patterns") or []
    extra_compiled = []
    for raw in extra:
        if isinstance(raw, str) and raw.strip():
            try:
                extra_compiled.append(re.compile(raw.strip()))
            except re.error:
                continue
    return _COMPILED_DEFAULT + extra_compiled


def _haystack(item: dict) -> str:
    return util.collapse_ws(
        " ".join([
            item.get("title", ""),
            item.get("summary", ""),
            item.get("_snippet", ""),
        ])
    )


def is_must_keep(item: dict, settings: dict) -> bool:
    """True if this story should never be dropped for daily-cap ranking."""
    mk = (settings.get("retention") or {}).get("must_keep") or {}
    if not mk.get("enabled", True):
        return False

    hay = _haystack(item)
    if not hay:
        return False

    for pat in _compiled_patterns(settings):
        if pat.search(hay):
            return True
    return False
