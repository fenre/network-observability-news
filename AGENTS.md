# AGENTS.md — working in this repository

Guidance for AI coding agents and contributors working on
**network-observability-news**. (The *published site* ships its own,
generated agent entrypoint at `dist/AGENTS.md` describing how to *consume* the
data — this file is about *developing* the project.)

**Claude Code (primary consumer):** see `CLAUDE.md` for live URLs and briefing
workflow. **Norway/Denmark:** `https://fenre.github.io/network-observability-news/api/briefing-nordics.json`.
Global: `https://fenre.github.io/network-observability-news/api/briefing.json`.

## What this project is

A static, GitHub-Pages-hosted **news aggregator** for Splunk, Cisco Data
Fabric, and network observability — tuned for **product releases and feature
updates** (partner/customer briefings), not cyber-incident or political news.
It fetches feeds, summarises with an LLM
(provider-agnostic, cost-guarded, with a deterministic extractive fallback),
and publishes AI-first surfaces. **Git is the database**: `data/*.json` is the
committed source of truth.

## Golden rules (do not break these)

1. **Never persist or republish source article bodies.** Only title, canonical
   link, metadata, and our own summary are stored. In-memory keys prefixed with
   `_` (e.g. `_fulltext`, `_snippet`) are transient and are stripped by
   `store.strip_transient` before anything is written. Keep it that way.
2. **`build` is deterministic and offline.** No network, no LLM, no clock-driven
   nondeterminism beyond `generatedAt` (which honours `SOURCE_DATE_EPOCH`). The
   site must be a pure function of committed `data/` + `config/` + `templates/`.
3. **Full-text fetch is opt-in + polite.** Only for sources with
   `fulltext: allow`, only after a `robots.txt` check, with the identifiable
   User-Agent and the configured delay. Default is `deny`.
4. **Cost guard stays intact.** No API key ⇒ extractive fallback. Respect
   `enrich.max_new_items_per_run`. The build never calls the LLM.
5. **Keep the item schema authoritative.** If you add a field, update
   `schemas/item.schema.json`, `normalize.py`, and the build surfaces together.
   Items carry `topics` (subject), `categories` (story type), and `tags` (product/feature hints).

## Pipeline shape (`newsfeed/`)

```
fetch.py      feeds (feedparser, ETag/Last-Modified) + robots-gated trafilatura
normalize.py  canonical URL, stable id, ISO timestamps, schema shape
dedupe.py     union-find clustering (canonical URL + title similarity)
classify.py   deterministic topic routing + product tags
enrich.py     provider-agnostic LLM summary/tags, content-hash cache, fallback
store.py      load/save data/*.json, merge, prune, strip transient, validate
build.py      render dist/ (all surfaces) from committed data only
__main__.py   `run` / `build` CLI
```

Run order in `run`: fetch → normalize → merge → blocklist → dedupe → classify →
**relevance** (technical-audience gate) → enrich → prune (age + **per-day cap**)
→ validate → save. Volume knobs: `fetch.max_items_per_feed`,
`retention.max_items_per_day` (importance-ranked; product releases exempt via
`must_keep.py`), `retention.priority_reserved_slots`, `retention.days`,
`retention.max_items`.

## Local checks

```bash
pip install -r requirements.txt
python -m newsfeed run --dry-run     # no writes, no LLM
python -m newsfeed build && open dist/index.html
```

There is no test suite yet; the dry-run + a successful build (schema validation
runs inside `run`) are the smoke test. If you add tests, prefer stdlib
`unittest` and keep them offline (feed fixtures, not live network).

## Conventions

- Stdlib + the pinned deps in `requirements.txt` only; import heavy/optional
  deps **lazily** so `run --dry-run` and `build` survive their absence.
- Source ids in `config/sources.yaml` are **stable forever** (they key item
  provenance). Add new sources; don't rename old ones.
- Keep SPL/JSON/YAML diffs reviewable; the JSON writers sort deterministically.
