"""Technical-audience relevance filter.

Keeps stories useful to practitioners (engineers, architects, operators).
Drops financial press, stock picks, executive profiles, and brand mentions
with no substantive technical angle.

Scoring is deterministic (no LLM). Trusted engineering blogs get a lower bar;
Google News and general press need stronger technical signals.
"""

from __future__ import annotations

import re

from . import editorial, util

# Lazy import avoids cycle: must_keep does not import relevance.
def _is_must_keep(item: dict, settings: dict) -> bool:
    from .must_keep import is_must_keep
    return is_must_keep(item, settings)

# Sources that publish primarily engineering/product content.
_TRUSTED_SOURCE_IDS = frozenset({
    "cisco-networking-blog",
    "cisco-datacenter-blog",
    "cisco-tag-splunk",
    "cisco-meraki-blog",
    "cisco-developer-blog",
    "ntop-blog",
    "opentelemetry-blog",
    "prometheus-blog",
    "zeek-blog",
    "cloudflare-blog",
})

# Google News <source> names that usually carry engineering content (lower bar).
_TRUSTED_PUBLISHER_PATTERNS = (
    "cisco blogs",
    "cisco meraki",
    "thousandeyes",
    "ntop",
    "splunk",
    "cloudflare",
    "opentelemetry",
    "prometheus",
    "zeek",
    "ripe labs",
    "apnic",
    "packet pushers",
    "ietf",
    "network world",
    "computer weekly",
    "sdxcentral",
    "fierce network",
    "techtarget",
    "data center dynamics",
)

# Publisher names (from Google News <source>) that are almost never technical.
_UNTRUSTED_PUBLISHER_PATTERNS = (
    "yahoo finance",
    "tradingview",
    "motley fool",
    "seeking alpha",
    "benzinga",
    "zacks",
    "stock titan",
    "marketwatch",
    "investor",
    "nasdaq",
    "tipranks",
    "fool.com",
    "gotrade",
    "simply wall st",
    "gurufocus",
    "pr newswire",  # often pure PR; scored separately
    "business wire",
)

# Hard drop: title or haystack matches → exclude regardless of score.
_HARD_DROP_RE = re.compile(
    r"|".join([
        r"\bstock price\b",
        r"\bshare price\b",
        r"\bprice target\b",
        r"\bearnings (call|report|beat|miss)\b",
        r"\bquarterly (earnings|results|revenue)\b",
        r"\brevenue growth\b",
        r"\bwhat'?s ahead\?\b",
        r"\bbuy rating\b",
        r"\b(sell|hold) rating\b",
        r"\bwall street\b",
        r"\binvestors?\b",
        r"\bipo\b",
        r"\bmarket cap\b",
        r"\btrading (at|near|above)\b",
        r"\b(analyst|analysts) (say|expect|upgrade|downgrade)\b",
        r"\b\d+% (stock|shares)\b",
        r"\bvs splunk\b.*\b20\d{2}\b",  # stock comparison pieces
        r"\bobservability war 20\d{2}\b",
        r"\b(reduces|saves) .*(\$|million|billion).*(annually|yearly|per year)\b",
        r"\bwake-?up call\b.*\b(billion|million|\$)\b",
        r"\b\d+\s*billion\b.*\b(drag|loss|downtime)\b",
        r"\bnamed a leader\b.*\b(gartner|gigaom|forrester|idc)\b",
        r"\b(fast mover|outperformer)\b.*\b(radar|quadrant|report)\b",
        r"\bpartner innovation challenge\b",
        r"\bjoin us for (the|our) .*webinar\b",
        r"\bvisibility is table stakes\b",
        r"\bconfidence wins\b",
        r" - cisco newsroom$",  # executive profile stubs
        r"^[\w\s.'-]+ - cisco newsroom$",
    ]),
    re.I,
)

# Weak title-only brand mention with no technical noun in title.
_BRAND_ONLY_TITLE_RE = re.compile(
    r"^(?:splunk|cisco|thousandeyes|kentik|catchpoint|grafana)\b",
    re.I,
)

# Strong technical signals (+2 each, capped contribution).
_STRONG_TECH = (
    "splunk enterprise", "splunk cloud platform", "splunk platform",
    "federated search", "release notes", "generally available",
    "new feature", "new features", "what's new", "whats new",
    "public beta", "private preview", "enhancement", "capability",
    "search processing language", "sourcetype", "index=", "hec ", "splunk cloud",
    "edge processor", "data model", "tstats", "netflow", "ipfix", "sflow",
    "bgp", "ospf", "vxlan", "evpn", "kubernetes", " k8s", "opentelemetry",
    " prometheus", " snmp", "syslog", "packet capture", "dpi ", "telemetry",
    "ingest", "parser", "props.conf", "transforms.conf", "dashboard studio",
    "saved search", "correlation search", "itsi", "kpi base", "glass table",
    "from spl", " spl ", "to pcap", "pcap", "soar", "splunk es",
    "enterprise security", "notable event", "cim ",
    "application centric infrastructure", " cisco aci", "nexus dashboard",
    "hyperfabric", "sd-wan", "sdwan", "wan optimization",
    "path visualization", "synthetic monitoring", "digital experience monitoring",
    "network observability platform", "flow data", "flow analytics",
    "release notes", "version ",
    "troubleshoot", "runbook", "deployment guide", "integration guide",
    "api ", " rest api", "sdk", "cli ", "terraform", "ansible",
    "openconfig", "gpb", "streaming telemetry", "modbus", "opc-ua",
    "benchmark", "latency", "packet loss", "jitter", "mtu ",
    "configuring ", "how to ", "step-by-step", "deep dive",
    "architecture", "reference architecture", "best practice",
    "outage analysis", "outage report", "service disruption",
    "internet outage", "network outage", "root cause",
    "mcp server", " mcp ", "model context protocol",
    "fedramp", "path vis", "synthetic test",
)

# Medium technical signals (+1 each).
_MEDIUM_TECH = (
    "monitoring", "observability", "analytics", "dashboard", "alert",
    "incident", "on-call", "sre", "devops", "secops", "netops",
    "firewall", "router", "switch", "load balancer", "dns ",
    "tls ", "ssl ", "encryption", "authentication", "siem",
    "threat detection", "log analysis", "metrics", "traces",
    "automation", "orchestration", "fabric", "data center",
    "hybrid cloud", "multi-cloud", "network performance",
)

# Marketing fluff without implementation detail (-2 each, max -4).
_FLUFF = (
    "thought leader", "market leader", "industry leader",
    "unprecedented", "game-changer", "game changer",
    "excited to announce", "proud to announce",
    "innovation challenge", "partner summit",
    "table stakes", "confidence wins",
)


def _haystack(item: dict) -> str:
    parts = [
        item.get("title", ""),
        item.get("summary", ""),
        item.get("_snippet", ""),
        " ".join(item.get("_feed_tags", []) or []),
        item.get("source", {}).get("name", ""),
    ]
    return " " + util.collapse_ws(" ".join(parts)).lower() + " "


def _source_id(item: dict) -> str:
    return (item.get("source") or {}).get("id", "")


def _publisher_name(item: dict) -> str:
    return (item.get("source") or {}).get("name", "").lower()


def _publisher_untrusted(item: dict) -> bool:
    name = _publisher_name(item)
    return any(p in name for p in _UNTRUSTED_PUBLISHER_PATTERNS)


def _publisher_trusted(item: dict) -> bool:
    name = _publisher_name(item)
    return any(p in name for p in _TRUSTED_PUBLISHER_PATTERNS)


def technical_score(item: dict) -> int:
    """Higher = more clearly technical. Negative = likely junk."""
    hay = _haystack(item)
    title = util.collapse_ws(item.get("title", "")).lower()
    score = 0

    for sig in _STRONG_TECH:
        if sig in hay:
            score += 2
    for sig in _MEDIUM_TECH:
        if sig in hay:
            score += 1
    for sig in _FLUFF:
        if sig in hay:
            score -= 2

    # Title-only weak brand mention (summary may mention observability from feed).
    if _BRAND_ONLY_TITLE_RE.match(title):
        has_title_tech = any(s in title for s in _STRONG_TECH + _MEDIUM_TECH)
        if not has_title_tech:
            score -= 3

    # Very short title + no strong signals → likely PR stub.
    if len(title) < 40 and score < 2:
        score -= 1

    return score


def is_technical_audience(item: dict, settings: dict) -> bool:
    """Return True if the item should be kept for a technical readership."""
    rel = (settings.get("relevance") or {})
    if not rel.get("enabled", True):
        return True

    if _is_must_keep(item, settings):
        return True

    if editorial.is_incident_or_politics(item, settings):
        return False

    hay = _haystack(item)
    title = item.get("title", "")

    if _HARD_DROP_RE.search(title) or _HARD_DROP_RE.search(hay):
        return False

    if _publisher_untrusted(item):
        return False

    sid = _source_id(item)

    # Engineering blogs: keep unless hard-excluded above (no score bar).
    if sid in _TRUSTED_SOURCE_IDS:
        return True

    score = technical_score(item)
    if editorial.is_product_updates_focus(settings) and editorial.has_product_update_angle(item):
        score += 4

    is_gnews = sid.startswith("gnews-") or (item.get("_source") or {}).get("type") == "googlenews"
    if is_gnews:
        if _publisher_trusted(item):
            min_score = int(rel.get("min_score_gnews_trusted_publisher", 1))
        else:
            min_score = int(rel.get("min_score_googlenews", 2))
    else:
        min_score = int(rel.get("min_score_default", 1))

    return score >= min_score


def filter_technical(items: list[dict], settings: dict, *, log=print) -> list[dict]:
    """Keep only items that pass the technical-audience gate."""
    kept = []
    dropped = 0
    for it in items:
        if is_technical_audience(it, settings):
            kept.append(it)
        else:
            dropped += 1
    if dropped:
        log(f"  relevance: dropped {dropped} non-technical item(s), kept {len(kept)}")
    else:
        log(f"  relevance: all {len(kept)} item(s) passed technical filter")
    return kept
