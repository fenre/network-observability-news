"""Small, dependency-free helpers shared across the pipeline.

Everything here is stdlib-only and deterministic so the build stays
reproducible and the core pipeline runs without the heavy optional deps.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

# Keys in an in-memory item dict that start with this prefix are transient
# (e.g. the fetched article body, feed snippet). They are NEVER written to
# data/items.json and never published. store.strip_transient enforces this.
TRANSIENT_PREFIX = "_"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_id(text: str, length: int = 16) -> str:
    """Stable item id: first ``length`` hex chars of sha1(text)."""
    return sha1_hex(text)[:length]


def strip_html(text: str | None) -> str:
    """Strip tags + unescape entities + collapse whitespace."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def collapse_ws(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def split_sentences(text: str) -> list[str]:
    text = collapse_ws(text)
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def truncate_chars(text: str, limit: int) -> str:
    text = collapse_ws(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    # Avoid cutting mid-word where possible.
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut + "\u2026"  # ellipsis


def normalize_title(title: str) -> str:
    """Aggressively normalize a title for near-duplicate comparison.

    Lowercases, drops a trailing ' - Publisher' / ' | Publisher' suffix (as
    Google News appends), removes punctuation, and collapses whitespace.
    """
    t = strip_html(title).lower()
    # Drop trailing source attribution that Google News / many CMSs append.
    for sep in (" - ", " | ", " \u2014 ", " \u2013 "):
        idx = t.rfind(sep)
        if idx > len(t) * 0.5:  # only if it's near the end (a suffix)
            t = t[:idx]
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def struct_time_to_iso(struct_time) -> str | None:
    """feedparser gives time.struct_time (UTC); convert to ISO-8601 string."""
    if not struct_time:
        return None
    try:
        import calendar

        ts = calendar.timegm(struct_time)
        return to_iso(datetime.fromtimestamp(ts, tz=timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        # Try RFC-822 (RSS pubDate) as a fallback.
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


def to_rfc822(value: str | None) -> str | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def iso_date(value: str | None) -> str | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return dt.date().isoformat()
