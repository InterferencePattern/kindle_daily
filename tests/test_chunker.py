"""Tests for the per-issue article chunker."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from kindle_daily.chunker import split
from kindle_daily.fetcher import Article


def _article(i: int, category: str = "Tech") -> Article:
    return Article(
        item_id=f"id-{i:04d}",
        title=f"Article {i}",
        url=f"https://example.test/{i}",
        feed_title="Example",
        category=category,
        body_html=f"<p>{i}</p>",
        author="",
        word_count=1,
    )


def _make_build_fn(
    tmp_path: Path, size_for: Callable[[list[Article]], int]
) -> tuple[Callable[[list[Article]], Path], list[list[Article]]]:
    """Build closure that writes a placeholder file of the size dictated by
    ``size_for(articles)`` and records every call."""
    calls: list[list[Article]] = []

    def build_fn(articles: list[Article]) -> Path:
        calls.append(list(articles))
        size = size_for(articles)
        # Distinct path per call so successive measurements don't clobber.
        path = tmp_path / f"measure_{len(calls):04d}.bin"
        path.write_bytes(b"\0" * size)
        return path

    return build_fn, calls


def test_empty_input_returns_empty_list(tmp_path):
    build_fn, calls = _make_build_fn(tmp_path, lambda arts: 0)
    assert split([], build_fn, size_cap_bytes=1024) == []
    assert calls == []  # never built anything


def test_fits_in_one_chunk(tmp_path):
    arts = [_article(i) for i in range(4)]
    build_fn, calls = _make_build_fn(tmp_path, lambda arts: 500)
    result = split(arts, build_fn, size_cap_bytes=1024)
    assert result == [arts]
    assert len(calls) == 1
    assert calls[0] == arts  # single measurement of the whole list


def test_splits_exactly_in_half_when_size_is_two_times_cap(tmp_path):
    """Size = cap × (n_articles / 2): chunker recurses, each half fits."""
    arts = [_article(i) for i in range(4)]
    cap = 1024

    def size_for(a: list[Article]) -> int:
        # Whole list is 2x cap; any proper sub-slice fits in one cap.
        return 2 * cap if len(a) == 4 else cap // 2

    build_fn, calls = _make_build_fn(tmp_path, size_for)
    result = split(arts, build_fn, size_cap_bytes=cap)

    assert len(result) == 2
    assert result[0] == arts[:2]
    assert result[1] == arts[2:]
    # 1 root measurement + 2 leaf measurements = 3 builds total.
    assert len(calls) == 3


def test_splits_into_three_when_size_is_three_times_cap(tmp_path):
    arts = [_article(i) for i in range(6)]
    cap = 1024

    def size_for(a: list[Article]) -> int:
        return 3 * cap if len(a) == 6 else cap // 2

    build_fn, _ = _make_build_fn(tmp_path, size_for)
    result = split(arts, build_fn, size_cap_bytes=cap)

    assert len(result) == 3
    # Order preserved across chunks.
    assert result[0] == arts[:2]
    assert result[1] == arts[2:4]
    assert result[2] == arts[4:]


def test_recursive_resplit_for_uneven_sizes(tmp_path):
    """A first split that still leaves one chunk over cap is recursed on."""
    arts = [_article(i) for i in range(4)]
    cap = 1024

    def size_for(a: list[Article]) -> int:
        # Root: 4 articles -> 3x cap (needs 3 chunks, capped at len=4 -> 3).
        # ceil(4/3) = 2, so slices are [0,1], [2,3], [] -> two slices.
        # First sub-slice (2 articles) still over cap -> recurses.
        # Singletons return as-is.
        if len(a) == 4:
            return 3 * cap
        if len(a) == 2 and a[0].item_id == "id-0000":
            return 2 * cap  # forces recursion on this slice
        return cap // 2

    build_fn, _ = _make_build_fn(tmp_path, size_for)
    result = split(arts, build_fn, size_cap_bytes=cap)

    # Concatenated result should still be all 4 articles in original order.
    assert [a for chunk in result for a in chunk] == arts
    # And every chunk should be ≤ cap-sized per the size_for fn — the only
    # way that's true is that the [0,1] slice was further split into [0]
    # and [1] singletons.
    assert any(chunk == [arts[0]] for chunk in result)
    assert any(chunk == [arts[1]] for chunk in result)


def test_single_oversized_article_returned_as_its_own_chunk(tmp_path):
    """When bisection bottoms out at len==1 but still exceeds cap, the chunker
    returns the singleton chunk anyway and lets the caller deal with it."""
    arts = [_article(0)]
    cap = 1024
    build_fn, calls = _make_build_fn(tmp_path, lambda a: 10 * cap)
    result = split(arts, build_fn, size_cap_bytes=cap)
    assert result == [arts]
    # Only one measurement — there's nothing left to bisect.
    assert len(calls) == 1


def test_order_preserved_across_chunks(tmp_path):
    """Concatenating all chunks yields the original input order."""
    arts = [_article(i) for i in range(8)]
    cap = 1024
    # Force 4 final chunks (size = 4x cap, even split).
    build_fn, _ = _make_build_fn(
        tmp_path, lambda a: 4 * cap if len(a) == 8 else cap // 2
    )
    result = split(arts, build_fn, size_cap_bytes=cap)
    assert [a for chunk in result for a in chunk] == arts


# --------------------------------------------------------------- category mode


def _sized_by_category(weights: dict[str, int]) -> Callable[[list[Article]], int]:
    """Return a ``size_for`` that sums a per-Category weight over the articles."""

    def size_for(arts: list[Article]) -> int:
        return sum(weights[a.category] for a in arts)

    return size_for


def test_category_mode_single_part_when_all_fit(tmp_path):
    """Every Category fits together -> one Part in category_order."""
    cap = 1000
    arts = (
        [_article(0, "Tech"), _article(1, "Tech")]
        + [_article(2, "Photography")]
        + [_article(3, "Misc")]
    )
    # 100 each, 4 articles = 400 total, well under cap.
    build_fn, calls = _make_build_fn(
        tmp_path, _sized_by_category({"Tech": 100, "Photography": 100, "Misc": 100})
    )
    result = split(
        arts,
        build_fn,
        size_cap_bytes=cap,
        category_order=["Photography", "Tech", "Misc"],
    )

    assert len(result) == 1
    # Articles regrouped into category_order: Photography, Tech, Misc.
    assert [a.category for a in result[0]] == [
        "Photography",
        "Tech",
        "Tech",
        "Misc",
    ]


def test_category_mode_splits_on_boundaries(tmp_path):
    """Each Category fits alone but no two fit together -> one Part each."""
    cap = 1000
    arts = (
        [_article(0, "Tech"), _article(1, "Tech")]
        + [_article(2, "Photography"), _article(3, "Photography")]
        + [_article(4, "Misc"), _article(5, "Misc")]
    )
    # Each Category = 2 x 300 = 600 (fits); any pair = 1200 (over cap).
    weights = {"Tech": 300, "Photography": 300, "Misc": 300}
    build_fn, _ = _make_build_fn(tmp_path, _sized_by_category(weights))
    result = split(
        arts,
        build_fn,
        size_cap_bytes=cap,
        category_order=["Photography", "Tech", "Misc"],
    )

    assert len(result) == 3
    assert {a.category for a in result[0]} == {"Photography"}
    assert {a.category for a in result[1]} == {"Tech"}
    assert {a.category for a in result[2]} == {"Misc"}
    # No Category was split: each Part holds both of its Category's articles.
    assert all(len(part) == 2 for part in result)


def test_category_mode_packs_multiple_categories_per_part(tmp_path):
    """Categories combine into a Part until the next one would overflow."""
    cap = 1000
    arts = [_article(0, "Photography")] + [_article(1, "Tech")] + [_article(2, "Misc")]
    # Photography 400 + Tech 400 = 800 (fits); + Misc 400 = 1200 (over).
    weights = {"Photography": 400, "Tech": 400, "Misc": 400}
    build_fn, _ = _make_build_fn(tmp_path, _sized_by_category(weights))
    result = split(
        arts,
        build_fn,
        size_cap_bytes=cap,
        category_order=["Photography", "Tech", "Misc"],
    )

    assert len(result) == 2
    assert [a.category for a in result[0]] == ["Photography", "Tech"]
    assert [a.category for a in result[1]] == ["Misc"]


def test_category_mode_bisects_category_that_overflows_alone(tmp_path):
    """A Category bigger than the cap on its own is bisected intra-Category."""
    cap = 1000
    big = [_article(i, "Photography") for i in range(4)]  # 4 x 400 = 1600
    small = [_article(10, "Misc")]  # 300
    arts = big + small
    weights = {"Photography": 400, "Misc": 300}
    build_fn, _ = _make_build_fn(tmp_path, _sized_by_category(weights))
    result = split(
        arts,
        build_fn,
        size_cap_bytes=cap,
        category_order=["Photography", "Misc"],
    )

    # Photography (1600) bisects into 2 Parts of 2 (800 each); Misc its own.
    flat = [a for part in result for a in part]
    assert flat == big + small  # order preserved end-to-end
    assert all(sum(weights[a.category] for a in part) <= cap for part in result)
    # The overflowing Category really was split across more than one Part.
    photo_parts = [p for p in result if any(a.category == "Photography" for a in p)]
    assert len(photo_parts) == 2


def test_category_mode_unranked_categories_sort_alphabetically(tmp_path):
    """Categories absent from category_order trail in alphabetical order."""
    cap = 1000
    arts = [_article(0, "Zebra"), _article(1, "Apple"), _article(2, "Tech")]
    build_fn, _ = _make_build_fn(
        tmp_path, _sized_by_category({"Zebra": 50, "Apple": 50, "Tech": 50})
    )
    result = split(
        arts,
        build_fn,
        size_cap_bytes=cap,
        category_order=["Tech"],  # only Tech ranked; Apple/Zebra are extras
    )

    assert len(result) == 1
    # Tech first (ranked), then Apple, Zebra (alphabetical leftovers).
    assert [a.category for a in result[0]] == ["Tech", "Apple", "Zebra"]


def test_category_mode_empty_input(tmp_path):
    build_fn, calls = _make_build_fn(tmp_path, lambda a: 0)
    assert split([], build_fn, size_cap_bytes=1000, category_order=["Tech"]) == []
    assert calls == []
