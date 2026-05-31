"""Deterministic topic routing + product tagging.

Primary signal is keyword matching over title + snippet + feed tags, merged
with the per-source topic hints from config/sources.yaml. This runs with no
LLM and no network, so classification is reproducible. ``enrich.py`` may layer
optional LLM-derived tags on top, but topics always have a deterministic
floor.
"""

from __future__ import annotations

import re

from .categories import assign_categories
from .config import VALID_TOPICS
from .relevance import _TRUSTED_SOURCE_IDS

# Topic -> list of keyword/phrase signals (lower-case, matched as word-ish
# substrings). Order doesn't matter; any hit assigns the topic.
_TOPIC_SIGNALS: dict[str, list[str]] = {
    "splunk": [
        "splunk", "spl ", "search processing language", "splunk cloud",
        "splunk enterprise", "splunk observability", "splunk platform",
        "signalfx", "edge processor", "splunk itsi", "splunk soar", "splunk es",
        "splunk rum", "victoria experience", "federated search",
    ],
    "cisco-data-fabric": [
        "cisco data fabric", "nexus dashboard", "nexus hyperfabric",
        "cisco nexus", "cisco aci", "application centric infrastructure",
        "nexus dashboard data broker", "cisco networking cloud", "hyperfabric",
        "data center networking", "vxlan", "evpn", "sonic on cisco",
    ],
    "network-observability": [
        "network observability", "network monitoring", "network telemetry",
        "thousandeyes", "kentik", "catchpoint", "ntopng", "ntop",
        "netflow", "ipfix", "sflow", "network performance monitoring",
        "digital experience monitoring", "deep network visibility",
        "flow data", "flow analytics", "packet capture", "network path",
        "opentelemetry", "network visibility",
    ],
}

# Product / feature tags -> signals. These become item.tags (lower-kebab).
_TAG_SIGNALS: dict[str, list[str]] = {
    "thousandeyes": ["thousandeyes"],
    "edge-processor": ["edge processor"],
    "splunk-observability": ["splunk observability", "signalfx"],
    "splunk-itsi": ["splunk itsi", "itsi"],
    "splunk-soar": ["splunk soar"],
    "enterprise-security": ["enterprise security", "splunk es "],
    "nexus-dashboard": ["nexus dashboard"],
    "nexus-hyperfabric": ["hyperfabric"],
    "cisco-aci": ["cisco aci", "application centric infrastructure"],
    "netflow": ["netflow", "ipfix", "sflow"],
    "kentik": ["kentik"],
    "catchpoint": ["catchpoint"],
    "ntopng": ["ntopng", "ntop"],
    "grafana": ["grafana"],
    "opentelemetry": ["opentelemetry", "otel"],
    "ai-ops": ["aiops", "ai ops", "anomaly detection"],
}


def _haystack(item: dict) -> str:
    parts = [
        item.get("title", ""),
        item.get("_snippet", ""),
        " ".join(item.get("_feed_tags", []) or []),
    ]
    return " " + re.sub(r"\s+", " ", " ".join(parts)).lower() + " "


def classify(item: dict, settings: dict) -> dict:
    """Assign ``topics``, ``tags``, and ``categories`` in place."""
    hay = _haystack(item)

    topics: list[str] = []
    for topic, signals in _TOPIC_SIGNALS.items():
        if any(sig in hay for sig in signals):
            topics.append(topic)

    # Per-source topic hints only when keywords matched OR the feed is a
    # trusted engineering blog. Google News items must earn topics from text,
    # not from the broad query that fetched them.
    src_id = (item.get("source") or {}).get("id", "")
    if topics or src_id in _TRUSTED_SOURCE_IDS:
        for hint in item.get("_source_topics", []) or []:
            if hint in VALID_TOPICS and hint not in topics:
                topics.append(hint)

    # No default topic — non-matching items are dropped by relevance.py.
    item["topics"] = [t for t in VALID_TOPICS if t in topics]

    tags: list[str] = []
    for tag, signals in _TAG_SIGNALS.items():
        if any(sig in hay for sig in signals):
            tags.append(tag)
    # Preserve any LLM/feed tags already present, de-duplicated + sorted.
    existing = [t for t in item.get("tags", []) if t]
    item["tags"] = sorted(set(tags) | set(existing))

    assign_categories(item, settings)
    return item
