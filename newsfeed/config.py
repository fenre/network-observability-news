"""Configuration + path resolution.

Resolves repo-root-relative paths and loads config/*.yaml. PyYAML is a hard
dependency (lightweight, broad wheel coverage); everything heavier is imported
lazily where it is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
SCHEMA_DIR = ROOT / "schemas"
TEMPLATES_DIR = ROOT / "templates"

SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
SOURCES_PATH = CONFIG_DIR / "sources.yaml"
CURATED_PATH = CONFIG_DIR / "curated.yaml"
BLOCKLIST_PATH = CONFIG_DIR / "blocklist.txt"

ITEMS_PATH = DATA_DIR / "items.json"
ENRICH_CACHE_PATH = DATA_DIR / "enrich-cache.json"
FEED_CACHE_PATH = DATA_DIR / "feed-cache.json"

ITEM_SCHEMA_PATH = SCHEMA_DIR / "item.schema.json"

VALID_TOPICS = ("splunk", "cisco-data-fabric", "network-observability")

# Article type (orthogonal to topics). Used for filters and retention rules.
VALID_CATEGORIES = (
    "product-release",
    "security",
    "outage",
    "tutorial",
    "research",
    "standards",
    "news",
)


def _load_yaml(path: Path) -> Any:
    import yaml  # PyYAML

    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_settings() -> dict:
    data = _load_yaml(SETTINGS_PATH) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{SETTINGS_PATH} must be a mapping")
    return data


def load_sources(*, enabled_only: bool = True) -> list[dict]:
    doc = _load_yaml(SOURCES_PATH) or {}
    sources = doc.get("sources", []) if isinstance(doc, dict) else []
    out = []
    for src in sources:
        if not isinstance(src, dict) or not src.get("id") or not src.get("feed"):
            continue
        if enabled_only and not src.get("enabled", True):
            continue
        out.append(src)
    return out


def load_blocklist() -> list[str]:
    """Return lower-cased, comment-stripped blocklist patterns."""
    if not BLOCKLIST_PATH.exists():
        return []
    patterns = []
    for line in BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.lower())
    return patterns


def is_blocked(item: dict, patterns: list[str]) -> bool:
    """An item is blocked if any pattern is a substring of its canonical URL,
    its plain URL, or its source id (case-insensitive)."""
    if not patterns:
        return False
    haystacks = [
        str(item.get("canonicalUrl", "")).lower(),
        str(item.get("url", "")).lower(),
        str(item.get("source", {}).get("id", "")).lower(),
    ]
    for pat in patterns:
        for hay in haystacks:
            if pat and pat in hay:
                return True
    return False


def load_item_schema() -> dict | None:
    try:
        with ITEM_SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
