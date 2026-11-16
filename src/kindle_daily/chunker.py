"""Partition a Run's article list into EPUB-cap-sized Parts.

`split` has two modes, selected by whether ``category_order`` is supplied:

* **Size bisection** (``category_order=None``) — the legacy single-Category
  partitioner. Builds a candidate EPUB with the caller-supplied ``build_fn``,
  measures the file size, and bisects into equal-count slices until every
  slice fits under ``size_cap_bytes``. This is the fallback used when one
  Category alone overflows the cap.

* **Category-boundary packing** (``category_order`` supplied) — packs whole
  Categories, in the given order, into successive Parts so splits land on
  Category boundaries. A Category that on its own exceeds the cap is bisected
  intra-Category via the size-bisection mode above.

The caller owns all I/O — ``build_fn`` decides where on disk the measurement
EPUB lives — and the chunker performs no logging of its own.

Order is preserved end-to-end. In bisection mode, Part ``0`` carries the
caller's first slice of articles. In category-boundary mode, Parts follow
``category_order`` and each Category keeps its incoming (publish-time) order.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

from .fetcher import Article

BuildFn = Callable[[list[Article]], Path]


def split(
    articles: list[Article],
    build_fn: BuildFn,
    size_cap_bytes: int,
    category_order: list[str] | None = None,
) -> list[list[Article]]:
    """Partition ``articles`` into Parts that each build under the size cap.

    Parameters
    ----------
    articles : list[Article]
        Articles to partition. In bisection mode they are used in their given
        order; in category-boundary mode they are regrouped by Category.
    build_fn : Callable[[list[Article]], Path]
        Builds a candidate EPUB and returns its path so size can be measured
        via ``path.stat().st_size``.
    size_cap_bytes : int
        Per-Part EPUB size cap.
    category_order : list[str] | None, optional
        When ``None`` (default), partition purely by size (legacy
        single-Category behaviour). When supplied, pack whole Categories into
        Parts in this order; Categories present in ``articles`` but absent
        from this list are appended in alphabetical order.

    Returns
    -------
    list[list[Article]]
        One sub-list per Part, in delivery order. A single oversized article
        that can't be bisected (``len(articles) == 1``) is returned as its
        own Part — the per-article pre-flight skip upstream is what keeps such
        cases out of here.
    """
    if not articles:
        return []
    if category_order is None:
        return _bisect_by_size(articles, build_fn, size_cap_bytes)
    return _pack_by_category(articles, build_fn, size_cap_bytes, category_order)


def _bisect_by_size(
    articles: list[Article],
    build_fn: BuildFn,
    size_cap_bytes: int,
) -> list[list[Article]]:
    """Recursively bisect ``articles`` by count until each slice fits the cap.

    Parameters
    ----------
    articles : list[Article]
        Articles to partition, in their final intended order.
    build_fn : Callable[[list[Article]], Path]
        Candidate builder; called once per recursive node.
    size_cap_bytes : int
        Per-Part EPUB size cap.

    Returns
    -------
    list[list[Article]]
        One sub-list per Part, in input order.
    """
    if not articles:
        return []
    path = build_fn(articles)
    size = path.stat().st_size
    if size <= size_cap_bytes or len(articles) <= 1:
        return [articles]

    # Bisect into ``n_chunks`` equal-count contiguous slices, capped by the
    # number of articles so we never create empty slices.
    n_chunks = min(len(articles), math.ceil(size / size_cap_bytes))
    chunk_len = math.ceil(len(articles) / n_chunks)

    out: list[list[Article]] = []
    for start in range(0, len(articles), chunk_len):
        sub = articles[start : start + chunk_len]
        out.extend(_bisect_by_size(sub, build_fn, size_cap_bytes))
    return out


def _pack_by_category(
    articles: list[Article],
    build_fn: BuildFn,
    size_cap_bytes: int,
    category_order: list[str],
) -> list[list[Article]]:
    """Greedily pack whole Categories into Parts, in ``category_order``.

    A Category is appended to the current Part if the combined Part still fits
    the cap; otherwise the current Part is sealed and the Category starts a
    fresh one. A Category that exceeds the cap on its own is bisected
    intra-Category, and packing resumes from a fresh Part afterwards.

    Parameters
    ----------
    articles : list[Article]
        Articles to partition; regrouped by Category internally.
    build_fn : Callable[[list[Article]], Path]
        Candidate builder used to measure each candidate Part.
    size_cap_bytes : int
        Per-Part EPUB size cap.
    category_order : list[str]
        Order in which Categories are packed. Categories present in
        ``articles`` but absent here are appended alphabetically.

    Returns
    -------
    list[list[Article]]
        One sub-list per Part, in delivery order.
    """
    by_category = _group_by_category(articles)
    ordered = _ordered_categories(list(by_category), category_order)

    parts: list[list[Article]] = []
    current: list[Article] = []
    for category in ordered:
        cat_articles = by_category[category]

        if current:
            candidate = current + cat_articles
            if _fits(candidate, build_fn, size_cap_bytes):
                current = candidate
                continue
            # Doesn't fit alongside what's already staged; seal and reset.
            parts.append(current)
            current = []

        # ``current`` is empty here: place this Category on a fresh Part.
        if _fits(cat_articles, build_fn, size_cap_bytes):
            current = cat_articles
        else:
            # Single Category overflows on its own -> bisect intra-Category.
            parts.extend(_bisect_by_size(cat_articles, build_fn, size_cap_bytes))

    if current:
        parts.append(current)
    return parts


def _fits(articles: list[Article], build_fn: BuildFn, size_cap_bytes: int) -> bool:
    """Return whether ``articles`` build to an EPUB within the size cap."""
    return build_fn(articles).stat().st_size <= size_cap_bytes


def _group_by_category(articles: list[Article]) -> dict[str, list[Article]]:
    """Group ``articles`` by Category, preserving each Category's order."""
    groups: dict[str, list[Article]] = {}
    for article in articles:
        groups.setdefault(article.category, []).append(article)
    return groups


def _ordered_categories(present: list[str], category_order: list[str]) -> list[str]:
    """Order ``present`` Categories by ``category_order``, extras alphabetical.

    Parameters
    ----------
    present : list[str]
        Categories actually present in the Run.
    category_order : list[str]
        Desired ordering. Entries not present are ignored.

    Returns
    -------
    list[str]
        ``present`` Categories that appear in ``category_order`` first (in that
        order), followed by any remaining Categories sorted alphabetically.
    """
    present_set = set(present)
    ranked = [c for c in category_order if c in present_set]
    ranked_set = set(ranked)
    leftovers = sorted(c for c in present if c not in ranked_set)
    return ranked + leftovers
