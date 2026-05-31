# CLAUDE.md — consuming Network Observability News

Primary audience: **Claude Code** preparing Splunk / Cisco partner briefings in **Norway and Denmark**.

## Live site (fetch once per session)

Base: `https://fenre.github.io/network-observability-news`

| What | URL |
|------|-----|
| **Nordics briefing (NO/DK — start here)** | `/api/briefing-nordics.json` |
| Global partner briefing | `/api/briefing.json` |
| Full dataset | `/api/items.json` |
| One story | `/api/items/{id}.json` |
| Agent contract | `/AGENTS.md` |
| Field schema | `/schemas/item.schema.json` |

```bash
curl -sS 'https://fenre.github.io/network-observability-news/api/briefing-nordics.json'
```

**`briefing-nordics.json` sections:**

- `platformSpine.highlights` / `platformSpine.productReleases` — global Splunk/Cisco GA (10.4, Dashboard Studio, etc.)
- `local` — stories tagged `audiences: nordics` (events, regional news)
- `events` — user groups and conferences
- `talkingPoints` — static partner context (EU residency, NIS2, local UG)
- Per-item `agentNote` on curated rows — maintainer hints for agents

Use `canonicalUrl` for links. Summaries are English; verify NO/DK sources at the linked page.

## Working in this repository

- **Do not scrape HTML** — use `data/items.json` or `python -m newsfeed build` → `dist/api/briefing-nordics.json`.
- Nordic curated URLs: [`config/curated-nordics.yaml`](config/curated-nordics.yaml). Signals: [`config/audiences.yaml`](config/audiences.yaml).
- Golden rules: root [`AGENTS.md`](AGENTS.md).

## Suggested hook in Splunk customer/partner repos

> Before drafting Nordic partner or customer content, fetch  
> `https://fenre.github.io/network-observability-news/api/briefing-nordics.json`  
> and cite `canonicalUrl` for each claim.
