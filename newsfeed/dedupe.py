"""Near-duplicate clustering.

Two items join the same cluster when either:
  * their canonical URLs match exactly, OR
  * their normalized titles are highly similar (difflib ratio >= threshold)
    AND they were published within a bounded time window.

Clustering is a deterministic union-find over the full item set (existing +
new), so re-running the pipeline never reshuffles clusters. ``clusterId`` is
set to the lexicographically smallest member id.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from . import util

_TITLE_SIMILARITY_THRESHOLD = 0.84
_TIME_WINDOW_DAYS = 7


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Attach larger id under smaller id so the root is the smallest id.
        if ra < rb:
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb


def _published_dt(item):
    return util.parse_iso(item.get("publishedAt"))


def cluster(items: list[dict]) -> list[dict]:
    """Assign ``clusterId`` to every item in place; returns the same list."""
    if not items:
        return items

    # Deterministic processing order.
    ordered = sorted(items, key=lambda it: (it.get("publishedAt") or "", it["id"]))
    uf = _UnionFind([it["id"] for it in ordered])

    # 1) Exact canonical-URL matches.
    by_canon: dict[str, str] = {}
    for it in ordered:
        canon = it.get("canonicalUrl", "")
        if canon in by_canon:
            uf.union(by_canon[canon], it["id"])
        else:
            by_canon[canon] = it["id"]

    # 2) Title-similarity matches within the time window. O(n^2) over the
    #    rolling window is fine for our scale (a few thousand items).
    norm_titles = {it["id"]: util.normalize_title(it.get("title", "")) for it in ordered}
    dts = {it["id"]: _published_dt(it) for it in ordered}

    for i in range(len(ordered)):
        a = ordered[i]
        ta = norm_titles[a["id"]]
        if not ta:
            continue
        da = dts[a["id"]]
        for j in range(i + 1, len(ordered)):
            b = ordered[j]
            # ordered is ascending by publishedAt; bail once out of window.
            db = dts[b["id"]]
            if da and db and (db - da).days > _TIME_WINDOW_DAYS:
                break
            if uf.find(a["id"]) == uf.find(b["id"]):
                continue
            tb = norm_titles[b["id"]]
            if not tb:
                continue
            # Cheap length guard before the more expensive ratio.
            if abs(len(ta) - len(tb)) > max(len(ta), len(tb)) * 0.5:
                continue
            ratio = SequenceMatcher(None, ta, tb).ratio()
            if ratio >= _TITLE_SIMILARITY_THRESHOLD:
                uf.union(a["id"], b["id"])

    for it in items:
        it["clusterId"] = uf.find(it["id"])
    return items
