"""CLI: ``python -m newsfeed run|build``.

    run    fetch -> normalize -> merge -> dedupe -> classify -> enrich -> store
    build  render committed data/ into dist/

Examples:
    python -m newsfeed run --dry-run     # no writes, no LLM (safe local check)
    python -m newsfeed run               # full run; writes data/ (LLM if key set)
    python -m newsfeed run --no-llm      # full run, force extractive summaries
    python -m newsfeed build             # build dist/ from committed data/
    python -m newsfeed build --out dist  # explicit output dir
"""

from __future__ import annotations

import argparse
import sys

from . import (
    __version__,
    build as build_mod,
    classify as classify_mod,
    config,
    curated as curated_mod,
    dedupe as dedupe_mod,
    enrich as enrich_mod,
    fetch as fetch_mod,
    relevance as relevance_mod,
    normalize as normalize_mod,
    store,
)


def _make_logger(quiet: bool):
    def log(*args):
        if not quiet:
            print(*args, flush=True)
    return log


def cmd_run(args) -> int:
    log = _make_logger(args.quiet)
    dry = args.dry_run
    use_llm = not dry and not args.no_llm

    settings = config.load_settings()
    sources = config.load_sources()
    enabled_source_ids = {s["id"] for s in sources}
    enabled_source_ids.add("curated-splunk-platform")
    blocklist = config.load_blocklist()
    log(f"newsfeed {__version__} — run ({'DRY-RUN' if dry else 'live'}, "
        f"LLM {'on' if use_llm else 'off'}) — {len(sources)} sources")

    existing = store.load_items()
    enrich_cache = store.load_enrich_cache()
    feed_cache = store.load_feed_cache()
    log(f"  loaded {len(existing)} existing items, {len(enrich_cache)} cached summaries")

    # 1) fetch
    raw_entries, feed_cache = fetch_mod.fetch_sources(
        sources, settings, allow_fulltext=True, feed_cache=feed_cache, log=log,
    )
    curated_raw = curated_mod.load_curated_entries(settings, log=log)
    raw_entries = curated_raw + raw_entries
    if args.limit:
        raw_entries = raw_entries[: args.limit]
    log(f"  fetched {len(raw_entries)} raw entries (incl. curated)")

    # 2) normalize
    incoming = []
    curated_norm: list[tuple[dict, dict]] = []
    for raw in raw_entries:
        item = normalize_mod.normalize(raw, settings)
        if item is not None:
            incoming.append(item)
            if (raw.get("_source") or {}).get("type") == "curated":
                curated_norm.append((item, raw))

    existing_ids = {it["id"] for it in existing}
    new_ids = {it["id"] for it in incoming} - existing_ids

    # 3) merge + blocklist
    merged = store.merge_items(existing, incoming)
    before_block = len(merged)
    merged = [it for it in merged if not config.is_blocked(it, blocklist)]
    blocked = before_block - len(merged)
    if blocked:
        log(f"  blocklist removed {blocked} item(s)")

    before_disabled = len(merged)
    merged = [
        it for it in merged
        if (it.get("source") or {}).get("id") in enabled_source_ids
    ]
    dropped_disabled = before_disabled - len(merged)
    if dropped_disabled:
        log(f"  disabled sources removed {dropped_disabled} item(s)")

    # 4) dedupe -> 5) classify -> curated metadata -> upgrade gnews URLs
    dedupe_mod.cluster(merged)
    for it in merged:
        classify_mod.classify(it, settings)
    curated_by_id = {item["id"]: raw for item, raw in curated_norm}
    for it in merged:
        raw = curated_by_id.get(it["id"])
        if raw:
            curated_mod.normalize_curated_extras(it, raw)
    curated_mod.apply_curated_overrides(merged, curated_raw, log=log)
    dedupe_mod.cluster(merged)
    merged = relevance_mod.filter_technical(merged, settings, log=log)

    # 6) enrich (new + any still-extractive get a chance to upgrade)
    enrich_mod.enrich_items(merged, settings, cache=enrich_cache, use_llm=use_llm, log=log)

    # 7) prune (age window, per-day cap, global max)
    pruned = store.prune(merged, settings, log=log)

    # validate (advisory)
    store.validate_items(pruned, log=log)

    clusters = {it["clusterId"] for it in pruned}
    log("  ----")
    log(f"  new this run: {len(new_ids)} | total: {len(pruned)} | clusters: {len(clusters)}")
    topic_counts: dict[str, int] = {}
    for it in pruned:
        for t in it.get("topics", []):
            topic_counts[t] = topic_counts.get(t, 0) + 1
    for t in config.VALID_TOPICS:
        log(f"    {t}: {topic_counts.get(t, 0)}")

    if dry:
        log("  DRY-RUN: no files written (data/ unchanged, no commit, no LLM spend).")
        return 0

    store.save_items(pruned)
    store.save_enrich_cache(enrich_cache)
    store.save_feed_cache(feed_cache)
    log(f"  wrote {config.ITEMS_PATH.relative_to(config.ROOT)}, "
        f"{config.ENRICH_CACHE_PATH.relative_to(config.ROOT)}, "
        f"{config.FEED_CACHE_PATH.relative_to(config.ROOT)}")
    return 0


def cmd_build(args) -> int:
    log = _make_logger(args.quiet)
    settings = config.load_settings()
    items = store.load_items()
    log(f"newsfeed {__version__} — build from {len(items)} committed items")
    build_mod.build_site(args.out, settings=settings, items=items, log=log)
    log(f"  open {args.out}/index.html")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="newsfeed", description=__doc__)
    parser.add_argument("--version", action="version", version=f"newsfeed {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="fetch + enrich + store data/")
    p_run.add_argument("--dry-run", action="store_true",
                       help="do everything in memory; write nothing; no LLM calls")
    p_run.add_argument("--no-llm", action="store_true",
                       help="force deterministic extractive summaries (no LLM)")
    p_run.add_argument("--limit", type=int, default=0,
                       help="cap number of raw entries processed (debug)")
    p_run.add_argument("--quiet", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_build = sub.add_parser("build", help="render dist/ from committed data/")
    p_build.add_argument("--out", default="dist", help="output directory (default: dist)")
    p_build.add_argument("--quiet", action="store_true")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
