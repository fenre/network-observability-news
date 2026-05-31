"""Deterministic static-site build.

Reads the committed dataset (data/items.json) and emits the full dist/ tree:
human dashboard + AI-first machine surfaces. Makes NO network or LLM calls —
output is a pure function of committed data + config + templates (+ the
generated-at timestamp, which honours SOURCE_DATE_EPOCH for reproducibility).
"""

from __future__ import annotations

import html
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config, store, util

TOPIC_LABELS = {
    "splunk": "Splunk",
    "cisco-data-fabric": "Cisco Data Fabric",
    "network-observability": "Network Observability",
}

# AI/LLM crawler allow-list (mirrors the convention in the sibling repo).
_AI_BOTS = [
    "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web",
    "anthropic-ai", "Google-Extended", "PerplexityBot", "Perplexity-User",
    "CCBot", "cohere-ai", "Bytespider", "Applebot-Extended",
    "Meta-ExternalAgent", "DuckAssistBot", "Diffbot", "FacebookBot",
]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _xml_esc(text) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _fill(template: str, mapping: dict) -> str:
    out = template
    for key, val in mapping.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def _read_template(name: str) -> str:
    return (config.TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _generated_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        return (
            datetime.fromtimestamp(int(epoch), tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    return util.now_utc_iso()


def _topic_badges(topics, *, cls="badge") -> str:
    return " ".join(
        f'<span class="{cls} t-{t}">{_esc(TOPIC_LABELS.get(t, t))}</span>'
        for t in topics
    )


def _public(item: dict) -> dict:
    return store.strip_transient(item)


# --------------------------------------------------------------------------
# main entrypoint
# --------------------------------------------------------------------------

def build_site(out_dir: str | Path, *, settings: dict, items: list[dict], log=print) -> dict:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "items").mkdir()
    (out / "api" / "items").mkdir(parents=True)
    (out / ".well-known").mkdir()
    (out / "schemas").mkdir()

    # Publish the item schema so the /schemas/item.schema.json URL referenced
    # from manifest.json + AGENTS.md actually resolves on Pages.
    if config.ITEM_SCHEMA_PATH.exists():
        shutil.copyfile(config.ITEM_SCHEMA_PATH, out / "schemas" / "item.schema.json")

    site = settings.get("site", {}) or {}
    base_url = str(site.get("base_url", "")).rstrip("/")
    repo_url = str(site.get("repository", "")).rstrip("/")
    build_cfg = settings.get("build", {}) or {}
    generated_at = _generated_at()

    # Enforce the takedown blocklist at build time too, so a freshly-blocked
    # item disappears from the published site on the very next build even
    # before the next pipeline run prunes it from data/items.json.
    blocklist = config.load_blocklist()
    blocked = sum(1 for it in items if config.is_blocked(it, blocklist))
    if blocked:
        log(f"  build: blocklist suppressed {blocked} item(s)")
        items = [it for it in items if not config.is_blocked(it, blocklist)]

    # Public, sorted dataset (newest first), transient keys stripped.
    pub = [_public(it) for it in items]
    pub.sort(key=lambda it: (it.get("publishedAt") or "", it.get("id") or ""), reverse=True)

    # Derived structures for filters / clusters.
    present_topics = [t for t in config.VALID_TOPICS if any(t in it.get("topics", []) for it in pub)]
    source_counts: dict[str, dict] = {}
    for it in pub:
        s = it.get("source", {})
        rec = source_counts.setdefault(s.get("id", "?"), {"id": s.get("id", "?"), "name": s.get("name", "?"), "count": 0})
        rec["count"] += 1
    sources_list = sorted(source_counts.values(), key=lambda r: r["name"].lower())

    clusters: dict[str, list[dict]] = {}
    for it in pub:
        clusters.setdefault(it.get("clusterId", it["id"]), []).append(it)

    # --- machine dataset surfaces ----------------------------------------
    _write(out / "api" / "items.json", json.dumps(pub, ensure_ascii=False, indent=2))
    for it in pub:
        _write(out / "api" / "items" / f"{it['id']}.json", json.dumps(it, ensure_ascii=False, indent=2))

    # data.js powers index.html under file:// without a server (no fetch/CORS).
    data_js = {
        "generatedAt": generated_at,
        "title": site.get("title", ""),
        "topics": present_topics,
        "sources": sources_list,
        "items": pub,
    }
    _write(out / "data.js", "window.NEWS_DATA = " + json.dumps(data_js, ensure_ascii=False) + ";\n")

    # --- index.html ------------------------------------------------------
    index_tpl = _read_template("index.html")
    _write(out / "index.html", _fill(index_tpl, {
        "SITE_TITLE": _esc(site.get("title", "")),
        "SITE_DESCRIPTION": _esc(util.collapse_ws(site.get("description", ""))),
        "BASE_URL": _esc(base_url),
        "REPO_URL": _esc(repo_url),
        "ITEM_COUNT": str(len(pub)),
        "GENERATED_AT": _esc(generated_at),
        "VERSION": _esc(__version__),
    }))

    # --- per-item html + md ----------------------------------------------
    item_html_tpl = _read_template("item.html")
    item_md_tpl = _read_template("item.md")
    by_id = {it["id"]: it for it in pub}
    for it in pub:
        siblings = [s for s in clusters.get(it.get("clusterId", it["id"]), []) if s["id"] != it["id"]]
        _write(out / "items" / f"{it['id']}.html",
               _render_item_html(item_html_tpl, it, siblings, site, base_url, repo_url))
        _write(out / "items" / f"{it['id']}.md",
               _render_item_md(item_md_tpl, it, siblings, base_url, repo_url))

    # --- feeds -----------------------------------------------------------
    feed_n = int(build_cfg.get("feed_items", 80))
    _write(out / "feed.json", _render_json_feed(pub[:feed_n], site, base_url))
    _write(out / "rss.xml", _render_rss(pub[:feed_n], site, base_url, generated_at))

    # --- LLM surfaces ----------------------------------------------------
    _write(out / "llms.txt", _render_llms(pub, site, base_url, generated_at))
    full_n = int(build_cfg.get("recent_items_for_llms_full", 120))
    _write(out / "llms-full.txt", _render_llms_full(pub[:full_n], site, base_url, generated_at))

    # --- discovery / policy ----------------------------------------------
    _write(out / "manifest.json", _render_manifest(pub, present_topics, sources_list, site, base_url, generated_at))
    _write(out / "sitemap.xml", _render_sitemap(pub, base_url, generated_at))
    _write(out / "robots.txt", _render_robots(base_url))
    ai_txt = _render_ai_txt(site, base_url, repo_url)
    _write(out / "ai.txt", ai_txt)
    _write(out / ".well-known" / "ai.txt", ai_txt)
    _write(out / "AGENTS.md", _render_agents_md(pub, present_topics, sources_list, site, base_url, repo_url, generated_at))

    log(f"  build: {len(pub)} items, {len(clusters)} clusters, "
        f"{len(sources_list)} sources -> {out}")
    return {"items": len(pub), "clusters": len(clusters), "sources": len(sources_list)}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# per-item renderers
# --------------------------------------------------------------------------

def _render_item_html(tpl, it, siblings, site, base_url, repo_url) -> str:
    published = it.get("publishedAt")
    published_html = (
        f'<span>&middot; <time datetime="{_esc(published)}">{_esc(util.iso_date(published) or published)}</time></span>'
        if published else ""
    )
    cluster_html = ""
    if siblings:
        links = ", ".join(
            f'<a href="{_esc(s["url"])}" target="_blank" rel="noopener nofollow">{_esc(s["source"]["name"])}</a>'
            for s in siblings
        )
        cluster_html = (
            f'<div class="cta" style="background:#0f141b">Also covered by '
            f'{len(siblings)} other source{"s" if len(siblings) > 1 else ""}: {links}</div>'
        )
    tags = it.get("tags", [])
    tags_html = ("Tags: " + ", ".join(_esc(t) for t in tags)) if tags else ""

    json_ld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": it.get("title", ""),
        "url": it.get("url", ""),
        "datePublished": it.get("publishedAt", ""),
        "inLanguage": it.get("lang", "en"),
        "abstract": it.get("summary", ""),
        "isBasedOn": it.get("url", ""),
        "publisher": {"@type": "Organization", "name": it.get("source", {}).get("name", "")},
        "mainEntityOfPage": f"{base_url}/items/{it['id']}.html",
    }
    json_ld_str = json.dumps(json_ld, ensure_ascii=False).replace("</", "<\\/")

    return _fill(tpl, {
        "LANG": _esc(it.get("lang", "en")),
        "TITLE": _esc(it.get("title", "")),
        "TITLE_ATTR": _esc(it.get("title", "")),
        "SITE_TITLE": _esc(site.get("title", "")),
        "SUMMARY_ATTR": _esc(util.truncate_chars(it.get("summary", ""), 200)),
        "SUMMARY_HTML": _esc(it.get("summary", "")),
        "SUMMARY_SOURCE": _esc(it.get("summarySource", "")),
        "BASE_URL": _esc(base_url),
        "REPO_URL": _esc(repo_url),
        "ID": _esc(it["id"]),
        "URL": _esc(it.get("url", "")),
        "SOURCE_NAME": _esc(it.get("source", {}).get("name", "")),
        "ATTRIBUTION": _esc(it.get("attribution", "")),
        "PUBLISHED_HTML": published_html,
        "TOPICS_HTML": _topic_badges(it.get("topics", [])),
        "TAGS_HTML": tags_html,
        "CLUSTER_HTML": cluster_html,
        "JSON_LD": json_ld_str,
    })


def _render_item_md(tpl, it, siblings, base_url, repo_url) -> str:
    homepage = it.get("source", {}).get("homepage")
    homepage_md = f" — homepage: <{homepage}>" if homepage else ""
    cluster_md = ""
    if siblings:
        lines = [f"## Also covered by", ""]
        for s in siblings:
            lines.append(f"- [{s['source']['name']}]({s['url']})")
        lines.append("")
        cluster_md = "\n".join(lines)
    return _fill(tpl, {
        "TITLE": it.get("title", ""),
        "SOURCE_NAME": it.get("source", {}).get("name", ""),
        "URL": it.get("url", ""),
        "HOMEPAGE_MD": homepage_md,
        "PUBLISHED": it.get("publishedAt", "") or "unknown",
        "TOPICS_CSV": ", ".join(it.get("topics", [])) or "—",
        "TAGS_CSV": ", ".join(it.get("tags", [])) or "—",
        "ATTRIBUTION": it.get("attribution", ""),
        "BASE_URL": base_url,
        "ID": it["id"],
        "SUMMARY_SOURCE": it.get("summarySource", ""),
        "SUMMARY": it.get("summary", ""),
        "CLUSTER_MD": cluster_md,
        "SELF_MD": f" · [Markdown]({it['id']}.md)",
        "REPO_URL": repo_url,
    })


# --------------------------------------------------------------------------
# feed renderers
# --------------------------------------------------------------------------

def _render_json_feed(items, site, base_url) -> str:
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": site.get("title", ""),
        "home_page_url": f"{base_url}/",
        "feed_url": f"{base_url}/feed.json",
        "description": util.collapse_ws(site.get("description", "")),
        "language": site.get("language", "en"),
        "authors": [{"name": site.get("maintainer", "")}],
        "items": [],
    }
    for it in items:
        feed["items"].append({
            "id": f"{base_url}/items/{it['id']}.html",
            "url": it.get("url", ""),
            "external_url": it.get("url", ""),
            "title": it.get("title", ""),
            "content_text": it.get("summary", ""),
            "summary": it.get("summary", ""),
            "date_published": it.get("publishedAt", ""),
            "authors": [{"name": it.get("author") or it.get("source", {}).get("name", "")}],
            "tags": it.get("topics", []) + it.get("tags", []),
            "_attribution": it.get("attribution", ""),
            "_source": it.get("source", {}),
        })
    return json.dumps(feed, ensure_ascii=False, indent=2)


def _render_rss(items, site, base_url, generated_at) -> str:
    title = _xml_esc(site.get("title", ""))
    desc = _xml_esc(util.collapse_ws(site.get("description", "")))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{title}</title>",
        f"    <link>{_xml_esc(base_url)}/</link>",
        f"    <description>{desc}</description>",
        f"    <language>{_xml_esc(site.get('language', 'en'))}</language>",
        f"    <lastBuildDate>{_xml_esc(util.to_rfc822(generated_at) or '')}</lastBuildDate>",
        f"    <generator>network-observability-news {__version__}</generator>",
        f'    <atom:link href="{_xml_esc(base_url)}/rss.xml" rel="self" type="application/rss+xml" />',
    ]
    for it in items:
        pub = util.to_rfc822(it.get("publishedAt"))
        cats = "".join(f"      <category>{_xml_esc(t)}</category>\n" for t in it.get("topics", []))
        desc_txt = _xml_esc(f"{it.get('summary', '')} ({it.get('attribution', '')})")
        parts += [
            "    <item>",
            f"      <title>{_xml_esc(it.get('title', ''))}</title>",
            f"      <link>{_xml_esc(it.get('url', ''))}</link>",
            f'      <guid isPermaLink="false">{_xml_esc(base_url)}/items/{_xml_esc(it["id"])}.html</guid>',
            (f"      <pubDate>{_xml_esc(pub)}</pubDate>" if pub else ""),
            f"      <source url=\"{_xml_esc(base_url)}/rss.xml\">{_xml_esc(it.get('source', {}).get('name', ''))}</source>",
            cats.rstrip("\n"),
            f"      <description>{desc_txt}</description>",
            "    </item>",
        ]
    parts += ["  </channel>", "</rss>"]
    return "\n".join(p for p in parts if p != "")


# --------------------------------------------------------------------------
# LLM surfaces
# --------------------------------------------------------------------------

def _render_llms(items, site, base_url, generated_at) -> str:
    lines = [
        f"# {site.get('title', '')}",
        "",
        f"> {util.collapse_ws(site.get('description', ''))} "
        f"Generated {generated_at}. Each link points to the original publisher; "
        f"summaries are original and machine-generated (never the source body).",
        "",
    ]
    for topic in config.VALID_TOPICS:
        topic_items = [it for it in items if topic in it.get("topics", [])]
        if not topic_items:
            continue
        lines.append(f"## {TOPIC_LABELS.get(topic, topic)}")
        for it in topic_items[:60]:
            summ = util.truncate_chars(it.get("summary", ""), 200)
            lines.append(f"- [{it.get('title', '')}]({it.get('url', '')}): {summ} ({it.get('attribution', '')})")
        lines.append("")
    lines += [
        "## Machine surfaces",
        f"- [Full dataset (JSON)]({base_url}/api/items.json)",
        f"- [JSON Feed 1.1]({base_url}/feed.json)",
        f"- [RSS 2.0]({base_url}/rss.xml)",
        f"- [Concatenated summaries]({base_url}/llms-full.txt)",
        f"- [Agent entrypoint]({base_url}/AGENTS.md)",
        f"- [AI usage policy]({base_url}/ai.txt)",
        "",
    ]
    return "\n".join(lines)


def _render_llms_full(items, site, base_url, generated_at) -> str:
    lines = [
        f"# {site.get('title', '')} — full recent summaries",
        "",
        f"Generated {generated_at}. {len(items)} most-recent items. Summaries are "
        f"original, machine-generated text (not the source article body). Always "
        f"verify against the linked original.",
        "",
    ]
    for it in items:
        lines += [
            f"## {it.get('title', '')}",
            f"- id: {it['id']}",
            f"- url: {it.get('url', '')}",
            f"- source: {it.get('source', {}).get('name', '')}",
            f"- published: {it.get('publishedAt', '')}",
            f"- topics: {', '.join(it.get('topics', []))}",
            f"- tags: {', '.join(it.get('tags', []))}",
            f"- attribution: {it.get('attribution', '')}",
            "",
            it.get("summary", ""),
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# discovery / policy surfaces
# --------------------------------------------------------------------------

def _render_manifest(items, topics, sources, site, base_url, generated_at) -> str:
    manifest = {
        "name": site.get("title", ""),
        "description": util.collapse_ws(site.get("description", "")),
        "version": __version__,
        "generatedAt": generated_at,
        "baseUrl": base_url,
        "repository": site.get("repository", ""),
        "license": "MIT (code + summaries); linked articles belong to their publishers",
        "counts": {"items": len(items), "topics": len(topics), "sources": len(sources)},
        "topics": topics,
        "sources": sources,
        "surfaces": {
            "human": "/index.html",
            "items_json": "/api/items.json",
            "item_json": "/api/items/{id}.json",
            "item_html": "/items/{id}.html",
            "item_md": "/items/{id}.md",
            "json_feed": "/feed.json",
            "rss": "/rss.xml",
            "llms": "/llms.txt",
            "llms_full": "/llms-full.txt",
            "sitemap": "/sitemap.xml",
            "ai_policy": "/ai.txt",
            "agents": "/AGENTS.md",
            "item_schema": "/schemas/item.schema.json",
        },
        "policy": {
            "storesArticleBody": False,
            "summaries": "original, machine-generated",
            "attribution": "requested",
        },
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2)


def _render_sitemap(items, base_url, generated_at) -> str:
    today = util.iso_date(generated_at) or generated_at[:10]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']

    def url(loc, lastmod):
        return (f"  <url><loc>{_xml_esc(loc)}</loc>"
                f"<lastmod>{_xml_esc(lastmod)}</lastmod></url>")

    parts.append(url(f"{base_url}/", today))
    parts.append(url(f"{base_url}/llms.txt", today))
    parts.append(url(f"{base_url}/AGENTS.md", today))
    for it in items:
        lm = util.iso_date(it.get("publishedAt")) or today
        parts.append(url(f"{base_url}/items/{it['id']}.html", lm))
    parts.append("</urlset>")
    return "\n".join(parts)


def _render_robots(base_url) -> str:
    lines = [
        "# Network Observability News — open aggregator. AI/LLM crawling welcome.",
        f"# AI usage policy: {base_url}/.well-known/ai.txt",
        f"# Agent entrypoint:  {base_url}/AGENTS.md",
        f"# LLM index:         {base_url}/llms.txt",
        "",
        "User-agent: *",
        "Allow: /",
        "",
        "# Explicit allow for known AI/LLM crawlers and agents.",
    ]
    for bot in _AI_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    lines.append(f"Sitemap: {base_url}/sitemap.xml")
    return "\n".join(lines)


def _render_ai_txt(site, base_url, repo_url) -> str:
    """Prefer the authored repo-root ai.txt; fall back to a generated default."""
    root_ai = config.ROOT / "ai.txt"
    if root_ai.exists():
        return root_ai.read_text(encoding="utf-8")
    return (
        f"# {site.get('title', '')} — AI Usage Policy\n"
        f"# {base_url}/.well-known/ai.txt\n\n"
        "[Site]\n"
        f"Name: {site.get('title', '')}\n"
        f"URL: {base_url}/\n"
        f"Repository: {repo_url}\n"
        f"Contact: {site.get('contact', '')}\n"
        "License: MIT (code + our summaries). Linked articles belong to their publishers.\n\n"
        "[Permissions]\n"
        "Crawl: yes — search engines and AI/LLM crawlers\n"
        "Index: yes\nTrain: yes (our summaries + code, MIT)\n"
        "Cite-in-chat-responses: yes\nUse-in-RAG-pipelines: yes\n\n"
        "[Content]\n"
        "This site is a news AGGREGATOR. It stores and republishes ONLY: titles,\n"
        "canonical links, metadata, and our own original machine-generated summaries.\n"
        "It NEVER stores or republishes the full text of linked articles. Treat the\n"
        "summaries as derivative, possibly-inaccurate text; the authoritative content\n"
        "is the linked original, whose rights belong to its publisher.\n\n"
        "[Preferences]\n"
        "Attribution: requested. Cite as: \"<title>\" via <publisher> "
        f"({site.get('title', '')}, {base_url}).\n"
        "Accuracy: summaries may contain errors — verify against the linked source.\n\n"
        "[Resources]\n"
        f"Agent entrypoint: {base_url}/AGENTS.md\n"
        f"LLM index:        {base_url}/llms.txt\n"
        f"Full summaries:   {base_url}/llms-full.txt\n"
        f"Dataset (JSON):   {base_url}/api/items.json\n"
        f"JSON Feed:        {base_url}/feed.json\n"
    )


def _render_agents_md(items, topics, sources, site, base_url, repo_url, generated_at) -> str:
    topic_lines = "\n".join(f"- `{t}` — {TOPIC_LABELS.get(t, t)}" for t in topics) or "- (none yet)"
    return f"""# AGENTS.md — {site.get('title', '')}

> Machine-readable entrypoint for AI agents and LLMs. This is a **news
> aggregator**: it publishes titles, canonical links, metadata, and **original
> machine-generated summaries** — never the source article body.

- Site: {base_url}/
- Repository: {repo_url}
- Generated: {generated_at}
- Items: {len(items)} · Topics: {len(topics)} · Sources: {len(sources)}

## How to consume this site

Prefer the machine surfaces over scraping the HTML:

| Surface | Path | Use |
|---|---|---|
| Full dataset | `{base_url}/api/items.json` | All items as a JSON array |
| Single item | `{base_url}/api/items/{{id}}.json` | One item by id |
| JSON Feed 1.1 | `{base_url}/feed.json` | Subscribe / sync |
| RSS 2.0 | `{base_url}/rss.xml` | Subscribe |
| LLM index | `{base_url}/llms.txt` | Curated link index |
| LLM full text | `{base_url}/llms-full.txt` | Recent summaries concatenated |
| Item schema | `{base_url}/schemas/item.schema.json` | Field contract |
| AI policy | `{base_url}/ai.txt` | Permissions + preferences |

## Topics

{topic_lines}

## Rules for agents

1. **Attribution requested** — cite as: `"<title>" via <publisher> ({site.get('title', '')})`.
2. **Summaries are derivative** and may contain errors. The authoritative content is
   the linked original; link users there.
3. **Do not present this as a live monitoring service.** It is a periodically-rebuilt
   static index.
4. **Removal / correction:** open an issue at {repo_url}/issues (see the removal-request
   template) and the item is suppressed via `config/blocklist.txt` on the next run.

## Field contract (per item)

`id, title, url, canonicalUrl, source{{id,name,homepage}}, author, publishedAt,
fetchedAt, topics[], tags[], summary, summarySource(llm|extractive|publisher|none),
clusterId, lang, license, attribution`
"""
