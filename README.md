# Network Observability News

An AI-optimized, **static** news aggregator for **Splunk**, **Cisco Data
Fabric**, and **Network Observability**, hosted on GitHub Pages and refreshed on
a schedule by a single GitHub Action.

- **Hybrid sourcing** — RSS/Atom + Google News query feeds as the spine, plus
  optional, `robots.txt`-respecting full-text fetch for sources explicitly
  marked `fulltext: allow`.
- **Safe by default** — we publish and commit **only** title, canonical link,
  metadata, and our **own machine-generated summary**. Full article text is
  fetched transiently as input to summarisation and is **never** committed or
  republished.
- **LLM enrichment, cost-guarded** — neutral summaries + topic/product tags via
  a provider-agnostic client, cached by content hash, capped per run, with a
  deterministic **extractive fallback** so it runs in dev/CI with **zero spend**
  and **no key**.
- **AI-first surfaces** — `llms.txt`, `llms-full.txt`, JSON Feed, RSS, per-item
  HTML + Markdown + JSON, `manifest.json`, `sitemap.xml`, `ai.txt`, `AGENTS.md`.
- **Git is the database** — the normalized dataset and the LLM cache are
  committed back to the repo each run (free persistence + full history).

## How it works

```
GitHub Action (cron, every 6h)
  └─ python -m newsfeed run        # fetch → normalize → dedupe → classify → enrich → data/
       └─ commit data/ back to main
            └─ python -m newsfeed build   # deterministic dist/ from committed data/
                 └─ upload-pages-artifact → deploy-pages
```

The **run** step is the only part that touches the network/LLM. The **build**
step is a pure function of committed `data/` + config + templates, so the
deployed site always matches what was committed.

## Quick start (local)

Requires Python 3.12+ (CI pins 3.12). Install deps (a virtualenv is recommended):

```bash
pip install -r requirements.txt
```

Then:

```bash
# 1) Dry run — fetch + classify in memory, no writes, no LLM calls, no spend.
python -m newsfeed run --dry-run

# 2) Real run — writes data/items.json + caches. Uses the LLM only if
#    LLM_API_KEY is set; otherwise deterministic extractive summaries.
python -m newsfeed run            # add --no-llm to force extractive

# 3) Build the static site from committed data/, then open it.
python -m newsfeed build
open dist/index.html              # macOS (use xdg-open on Linux)
```

> The pipeline imports `feedparser`, `trafilatura`, and the LLM SDK lazily, so
> `run --dry-run` and `build` still work if some optional deps are missing —
> you just get fewer fetched items and/or extractive summaries.

## Configuration

| File | Purpose |
|---|---|
| `config/sources.yaml` | Feeds, per-source topic hints, `fulltext: allow\|deny`, license. **Default deny.** |
| `config/settings.yaml` | Site URL/title, fetch UA + polite delay, LLM model/caps, retention window, build sizes. |
| `config/blocklist.txt` | Takedown list — substrings matched against canonical URL / source id. Enforced at run **and** build. |
| `schemas/item.schema.json` | The item field contract (validated each run). |

### LLM provider (optional)

The client speaks the **OpenAI Chat Completions** wire format and honours a
custom `base_url`, so it works with OpenAI, Azure-style gateways, OpenRouter,
Together, Groq, local servers, etc. Configure via environment:

| Env var | Meaning | Default |
|---|---|---|
| `LLM_API_KEY` | API key. **Unset ⇒ extractive fallback (no spend).** | _(none)_ |
| `LLM_MODEL` | Model id | `gpt-4o-mini` |
| `LLM_BASE_URL` | Override endpoint (OpenAI-compatible) | OpenAI default |

In GitHub Actions, set `LLM_API_KEY` as a **repo secret**; `LLM_MODEL` /
`LLM_BASE_URL` as **repo variables** (optional).

## Deployment (GitHub Pages)

One workflow does everything: [`.github/workflows/update-and-deploy.yml`](.github/workflows/update-and-deploy.yml)
(`cron: 0 */6 * * *` + manual dispatch, singleton concurrency,
`contents/pages/id-token: write`).

**One-time setup:**

1. **Settings → Pages → Source: GitHub Actions.**
2. *(Optional)* **Settings → Secrets and variables → Actions → New repository
   secret:** `LLM_API_KEY`. Without it the site still builds using extractive
   summaries.

Then trigger **Actions → Update and deploy → Run workflow**, or wait for the
schedule.

## Legal / sourcing posture

- We store/republish only titles, links, metadata, and our **own** summaries —
  never source article bodies.
- Full-text fetch is **opt-in per source** (`fulltext: allow`), `robots.txt`-gated
  via `urllib.robotparser`, uses an **identifiable User-Agent with a contact
  URL**, and applies a **polite delay**.
- Broad coverage uses **Google News RSS query feeds** (link + snippet) so we
  don't scrape arbitrary sites.
- Takedown path: the [removal-request issue template](.github/ISSUE_TEMPLATE/removal-request.yml)
  → `config/blocklist.txt`. Policy is declared in [`ai.txt`](ai.txt).

## Layout

```
newsfeed/            # the pipeline package (python -m newsfeed run|build)
  fetch.py normalize.py dedupe.py classify.py enrich.py store.py build.py
  config.py util.py __main__.py
config/              # sources.yaml, settings.yaml, blocklist.txt
schemas/             # item.schema.json
data/                # committed source-of-truth: items.json, enrich-cache.json, feed-cache.json
templates/           # index.html, item.html, item.md
dist/                # built site (gitignored; produced by `build`)
.github/             # update-and-deploy.yml + removal-request template
```

## License

MIT (code + our summaries). Linked articles belong to their publishers. See
[`LICENSE`](LICENSE).
