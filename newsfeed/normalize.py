"""Normalize raw feed entries into schema-shaped item dicts.

Responsibilities: canonicalize the URL (drop tracking params), derive the
stable id, coerce timestamps to ISO-8601, and map source metadata + transient
snippet/full-text onto the item shape defined in schemas/item.schema.json.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import util

# Query-string keys stripped during canonicalization.
_TRACKING_EXACT = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
    "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "referrer", "cmpid",
    "cmp", "spm", "_hsenc", "_hsmi", "hsctatracking", "vero_id", "oly_anon_id",
    "oly_enc_id", "ck_subscriber_id", "s_cid", "elqtrackid",
}
_TRACKING_PREFIXES = ("utm_",)
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def canonical_url(url: str) -> str:
    """Return a stable canonical form: https-normalized scheme, lowercased
    host, default ports dropped, tracking params removed, fragment removed,
    trailing slash trimmed (except root)."""
    if not url:
        return url
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()

    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"

    netloc = parts.netloc
    host = parts.hostname or ""
    host = host.lower()
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        userinfo += "@"
    port = parts.port
    if port and _DEFAULT_PORTS.get(scheme) == str(port):
        port = None
    netloc = userinfo + host + (f":{port}" if port else "")

    kept = [
        (k, v)
        for (k, v) in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_EXACT
        and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    kept.sort()
    query = urlencode(kept)

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize(raw: dict, settings: dict) -> dict | None:
    """Map one raw entry (from fetch._raw_entry) to a schema-shaped item.

    Returns None for entries we can't key (no usable URL).
    """
    url = (raw.get("link") or "").strip()
    if not url:
        return None
    canon = canonical_url(url)
    if not canon:
        return None

    source = raw.get("_source", {})
    src_id = source.get("id", "unknown")
    src_name = source.get("name", src_id)
    homepage = source.get("homepage")

    # For Google News query feeds, prefer the real publisher name for display
    # and attribution; the link still points at the Google redirect.
    publisher = raw.get("publisher")
    is_googlenews = str(source.get("type", "")).lower() == "googlenews"
    if is_googlenews and publisher:
        attribution = f"via {publisher} (Google News)"
        display_source_name = publisher
    else:
        attribution = f"via {src_name}"
        display_source_name = src_name

    fetched = util.now_utc_iso()
    published = raw.get("published_iso") or fetched

    lang = (settings.get("site", {}) or {}).get("language", "en")

    item = {
        "id": util.short_id(canon),
        "title": util.strip_html(raw.get("title")) or "(untitled)",
        "url": url,
        "canonicalUrl": canon,
        "source": {
            "id": src_id,
            "name": display_source_name,
            "homepage": homepage,
        },
        "author": raw.get("author"),
        "publishedAt": published,
        "fetchedAt": fetched,
        "topics": [],
        "tags": [],
        "categories": [],
        "audiences": ["global"],
        "summary": "",
        "summarySource": "none",
        "clusterId": util.short_id(canon),
        "lang": lang,
        "license": source.get("license"),
        "attribution": attribution,
        # --- transient (never persisted; stripped by store.strip_transient) --
        "_snippet": util.strip_html(raw.get("_snippet")),
        "_fulltext": raw.get("_fulltext", ""),
        "_feed_tags": raw.get("feed_tags", []),
        "_source_topics": list(source.get("topics", []) or []),
    }
    return item
