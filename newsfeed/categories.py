"""Article categories (type of story) — separate from topics (subject area).

Topics: splunk | cisco-data-fabric | network-observability
Categories: product-release | security | outage | tutorial | research | standards | news
"""

from __future__ import annotations

from .config import VALID_CATEGORIES
from .editorial import has_product_update_angle
from .must_keep import is_must_keep

# (category_id, keyword signals in title + summary + snippet)
_CATEGORY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("product-release", ()),  # filled via is_must_keep only
    ("outage", (
        "outage analysis", "service disruption", "internet outage",
        "network outage", "root cause", "downtime", "service degradation",
        "went down", "partial outage", "global outage",
    )),
    ("security", (
        "cve-", "vulnerability", "threat research", "malware", "ransomware",
        "intrusion", "zero-day", "zero day", "talos", "siem", "detection rule",
        "splunk es", "enterprise security", "splunk soar", "incident response",
    )),
    ("tutorial", (
        "how to ", "step-by-step", "step by step", "tutorial", "deep dive",
        "walkthrough", "getting started", "best practice", "deployment guide",
        "integration guide", "configure ", "configuring ",
    )),
    ("research", (
        "research report", "magic quadrant", "survey finds", "study finds",
        "benchmark", "total cost", "wake-up call", "report shows",
        "analyst report", "market report",
    )),
    ("standards", (
        "ietf", "rfc ", "openconfig", "yang model", "semantic convention",
        "internet protocol", "bgp ", "ripe ", "apnic ", "routing policy",
    )),
    ("event", (
        "user group", "usergroup", "meetup", "conference", "splunkug",
        "splunk user conference", "in-person event", "virtual splunk user group",
        "splunk community champions",
    )),
)

# Source ids that default to engineering-blog category when nothing else matches.
_ENGINEERING_SOURCES = frozenset({
    "cisco-networking-blog", "cisco-datacenter-blog", "cisco-tag-splunk",
    "cisco-meraki-blog", "cisco-developer-blog", "ntop-blog",
    "opentelemetry-blog", "zeek-blog", "cloudflare-blog",
})


def _haystack(item: dict) -> str:
    from . import util
    return util.collapse_ws(
        " ".join([
            item.get("title", ""),
            item.get("summary", ""),
            item.get("_snippet", ""),
        ])
    ).lower()


def assign_categories(item: dict, settings: dict) -> dict:
    """Set ``categories`` (sorted, schema-valid). Returns the same item."""
    hay = _haystack(item)
    found: list[str] = []

    if is_must_keep(item, settings) or has_product_update_angle(item):
        found.append("product-release")

    for cat, signals in _CATEGORY_SIGNALS:
        if cat == "product-release":
            continue
        if any(sig in hay for sig in signals):
            found.append(cat)

    sid = (item.get("source") or {}).get("id", "")
    canon = (item.get("canonicalUrl") or item.get("url") or "").lower()
    if "usergroups.splunk.com" in canon:
        found.append("event")

    if not found and sid in _ENGINEERING_SOURCES:
        found.append("tutorial")

    if not found:
        found.append("news")

    item["categories"] = [c for c in VALID_CATEGORIES if c in found]
    return item
