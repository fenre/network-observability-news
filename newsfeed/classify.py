"""Deterministic topic routing + product tagging.

Primary signal is keyword matching over title + snippet + feed tags, merged
with the per-source topic hints from config/sources.yaml. This runs with no
LLM and no network, so classification is reproducible. ``enrich.py`` may layer
optional LLM-derived tags on top, but topics always have a deterministic
floor.
"""

from __future__ import annotations

import re

from .config import VALID_TOPICS

# Topic -> list of keyword/phrase signals (lower-case, matched as word-ish
# substrings). Order doesn't matter; any hit assigns the topic.
_TOPIC_SIGNALS: dict[str, list[str]] = {
    "splunk": [
        "splunk", "spl ", "search processing language", "splunk cloud",
        "splunk enterprise", "splunk observability", "signalfx", "edge processor",
        "splunk itsi", "splunk soar", "splunk es", "splunk rum", "victoria experience",
    ],
    "cisco-data-fabric": [
        "cisco data fabric", "data fabric", "nexus dashboard", "nexus hyperfabric",
        "cisco nexus", "cisco aci", "application centric infrastructure",
        "nexus dashboard data broker", "cisco networking cloud", "hyperfabric",
        "data center networking", "cisco data center",
    ],
    "network-observability": [
        "network observability", "observability", "thousandeyes", "kentik",
        "catchpoint", "ntopng", "ntop", "netflow", "ipfix", "sflow",
        "network performance monitoring", "npm ", "digital experience monitoring",
        "deep network visibility", "flow data", "telemetry", "opentelemetry",
        "grafana", "network monitoring", "packet capture", "snmp",
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
    """Assign ``topics`` and ``tags`` in place; returns the same item."""
    hay = _haystack(item)

    topics: list[str] = []
    for topic, signals in _TOPIC_SIGNALS.items():
        if any(sig in hay for sig in signals):
            topics.append(topic)

    # Merge per-source hints (always valid topics from sources.yaml).
    for hint in item.get("_source_topics", []) or []:
        if hint in VALID_TOPICS and hint not in topics:
            topics.append(hint)

    if not topics:
        default = (settings.get("classification", {}) or {}).get(
            "default_topic", "network-observability"
        )
        topics = [default]

    # Stable order matching schema enum order.
    item["topics"] = [t for t in VALID_TOPICS if t in topics]

    tags: list[str] = []
    for tag, signals in _TAG_SIGNALS.items():
        if any(sig in hay for sig in signals):
            tags.append(tag)
    # Preserve any LLM/feed tags already present, de-duplicated + sorted.
    existing = [t for t in item.get("tags", []) if t]
    item["tags"] = sorted(set(tags) | set(existing))
    return item
