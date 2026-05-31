"""network-observability-news pipeline.

A static, GitHub-Pages-hosted news aggregator for Splunk, Cisco Data Fabric,
and Network Observability.

Two entrypoints (see ``python -m newsfeed``):

    run     fetch -> normalize -> dedupe -> classify -> enrich -> store data/
    build   render committed data/ into the deterministic dist/ site

"git is the database": normalized items + the LLM cache are committed back to
the repo each run. The build never makes network or LLM calls.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def get_version() -> str:
    """Read the repo-root VERSION file (single source of version truth)."""
    vf = _ROOT / "VERSION"
    try:
        return vf.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


__version__ = get_version()
