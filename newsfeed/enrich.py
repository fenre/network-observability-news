"""LLM enrichment (cost-guarded) with a deterministic extractive fallback.

Contract:
  * We produce our OWN neutral 2-3 sentence summary + product/topic tags.
  * The source article body (``_fulltext``) and feed snippet are INPUT only;
    they are never persisted (store.strip_transient drops all ``_`` keys).
  * LLM results are cached by content hash in data/enrich-cache.json, so a
    given article is only paid for once. Extractive summaries are recomputed
    each run (cheap + deterministic) so they auto-upgrade to LLM output once a
    key is configured.
  * ``max_new_items_per_run`` caps how many NEW items hit the LLM per run.
  * With no API key (local dev / CI without secrets), every item falls back to
    the deterministic extractive summary — the pipeline runs with zero spend.

Provider-agnostic: the client speaks the OpenAI Chat Completions wire format
and honours a custom ``base_url`` (env, configurable), so OpenAI, Azure-style
gateways, OpenRouter, Together, Groq and local servers all work.
"""

from __future__ import annotations

import json
import os

from . import util

_SUMMARY_PROMPT = (
    "You are a neutral technical news summariser for an aggregator covering "
    "Splunk, Cisco Data Fabric, and network observability.\n"
    "Write an ORIGINAL, neutral summary of the article below in {n} sentences "
    "or fewer. Do NOT copy sentences verbatim from the source. Be factual and "
    "vendor-neutral.{nordic_hint} Then list up to 5 short lower-case topic/product tags.\n\n"
    "Return ONLY minified JSON of the form:\n"
    '{{"summary": "<text>", "tags": ["tag1", "tag2"]}}\n\n'
    "TITLE: {title}\n"
    "SOURCE: {source}\n"
    "ARTICLE:\n{body}\n"
)


def _content_for_summary(item: dict) -> str:
    body = item.get("_fulltext") or item.get("_snippet") or ""
    return util.collapse_ws(body)


def _content_hash(item: dict) -> str:
    basis = "\n".join([
        item.get("canonicalUrl", ""),
        item.get("title", ""),
        _content_for_summary(item)[:4000],
    ])
    return util.sha256_hex(basis)


def extractive_summary(item: dict, sentences: int) -> str:
    """Deterministic fallback: first N sentences of the available text, else
    the title."""
    body = _content_for_summary(item)
    sents = util.split_sentences(body)
    if sents:
        summary = " ".join(sents[:sentences])
        return util.truncate_chars(summary, 600)
    title = util.collapse_ws(item.get("title", ""))
    return title or "(no summary available)"


class LLMClient:
    """Thin wrapper over an OpenAI-compatible Chat Completions endpoint.

    ``available`` is False when no API key is configured or the SDK is not
    installed; callers then use the extractive fallback.
    """

    def __init__(self, settings: dict, *, log=lambda *_: None):
        enrich_cfg = settings.get("enrich", {})
        self.log = log
        self.model = os.environ.get(
            enrich_cfg.get("model_env", "LLM_MODEL"),
            enrich_cfg.get("default_model", "gpt-4o-mini"),
        )
        self.api_key = os.environ.get(enrich_cfg.get("api_key_env", "LLM_API_KEY"))
        self.base_url = os.environ.get(enrich_cfg.get("base_url_env", "LLM_BASE_URL")) or None
        self.timeout = float(enrich_cfg.get("request_timeout_seconds", 40))
        self._client = None
        self.available = bool(self.api_key)
        if self.available:
            try:
                from openai import OpenAI

                kwargs = {"api_key": self.api_key, "timeout": self.timeout}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self._client = OpenAI(**kwargs)
            except Exception as exc:  # noqa: BLE001
                self.log(f"WARN: LLM SDK unavailable ({exc}); using extractive fallback.")
                self.available = False

    def summarize(self, item: dict, sentences: int) -> dict | None:
        """Return ``{'summary': str, 'tags': [str]}`` or None on failure."""
        if not self.available or self._client is None:
            return None
        body = _content_for_summary(item)[:8000]
        aud = set(item.get("audiences") or [])
        nordic_hint = ""
        if aud & {"nordics", "nordics-no", "nordics-dk"}:
            nordic_hint = (
                " If relevant, add one short sentence on why this matters for "
                "Splunk partners in Norway and Denmark (events, regulation, or "
                "local community)."
            )
        prompt = _SUMMARY_PROMPT.format(
            n=sentences,
            nordic_hint=nordic_hint,
            title=item.get("title", ""),
            source=item.get("source", {}).get("name", ""),
            body=body or item.get("title", ""),
        )
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            data = json.loads(content)
            summary = util.collapse_ws(str(data.get("summary", "")))
            if not summary:
                return None
            tags = []
            for t in data.get("tags", []) or []:
                t = str(t).strip().lower().replace(" ", "-")
                if t:
                    tags.append(t)
            return {"summary": util.truncate_chars(summary, 800), "tags": tags[:8]}
        except Exception as exc:  # noqa: BLE001
            self.log(f"WARN: LLM summarize failed for {item.get('id')}: {exc}")
            return None


def enrich_items(
    items: list[dict],
    settings: dict,
    *,
    cache: dict,
    use_llm: bool = True,
    log=lambda *_: None,
) -> dict:
    """Fill ``summary`` / ``summarySource`` / ``tags`` for every item.

    ``cache`` (content-hash -> result) is mutated in place and returned. Stats
    are logged. Items already carrying a cached LLM summary are reused for free.
    """
    enrich_cfg = settings.get("enrich", {})
    sentences = int(enrich_cfg.get("summary_sentences", 3))
    cap = int(enrich_cfg.get("max_new_items_per_run", 40))

    client = LLMClient(settings, log=log) if use_llm else None
    llm_on = bool(client and client.available)
    if use_llm and not llm_on:
        log("INFO: no LLM key/SDK; using deterministic extractive summaries.")

    stats = {"cached": 0, "llm": 0, "extractive": 0}
    new_llm = 0

    # Process oldest-first so the per-run cap favours catching up on backlog
    # deterministically.
    for item in sorted(items, key=lambda it: (it.get("publishedAt") or "", it["id"])):
        if item.get("summarySource") == "curated" and item.get("summary"):
            continue

        chash = _content_hash(item)
        cached = cache.get(chash)
        if cached and cached.get("summary"):
            item["summary"] = cached["summary"]
            item["summarySource"] = cached.get("summarySource", "llm")
            _merge_tags(item, cached.get("tags", []))
            stats["cached"] += 1
            continue

        produced = None
        if llm_on and new_llm < cap:
            produced = client.summarize(item, sentences)
            if produced:
                new_llm += 1

        if produced:
            item["summary"] = produced["summary"]
            item["summarySource"] = "llm"
            _merge_tags(item, produced["tags"])
            cache[chash] = {
                "summary": produced["summary"],
                "summarySource": "llm",
                "tags": produced["tags"],
                "model": client.model,
                "ts": util.now_utc_iso(),
            }
            stats["llm"] += 1
        else:
            item["summary"] = extractive_summary(item, sentences)
            item["summarySource"] = "extractive"
            stats["extractive"] += 1

    log(
        f"  enrich: {stats['cached']} cached, {stats['llm']} new LLM, "
        f"{stats['extractive']} extractive (cap {cap})"
    )
    return cache


def _merge_tags(item: dict, tags) -> None:
    existing = set(item.get("tags", []) or [])
    for t in tags or []:
        t = str(t).strip().lower().replace(" ", "-")
        if t:
            existing.add(t)
    item["tags"] = sorted(existing)
