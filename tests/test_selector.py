"""Tests for the per-run item selector."""

from __future__ import annotations

import dataclasses
import time

from kindle_daily.freshrss import FeedItem
from kindle_daily.selector import select


def _item(i: int, *, category: str = "News", published_ms: int = 0) -> FeedItem:
    return FeedItem(
        item_id=f"tag:item/{i:04d}",
        title=f"Article {i}",
        url=f"https://example.test/{i}",
        feed_title="Example",
        category=category,
        summary_html="<p>x</p>",
        published_ms=published_ms,
        author="",
    )


def test_empty_input_returns_empty_result(config):
    result = select([], config)
    assert result.items == []
    assert result.dropped_excluded_category == 0
    assert result.dropped_too_old == 0
    assert result.no_timestamp == 0


def test_excluded_category_items_are_dropped(config):
    cfg = dataclasses.replace(config, excluded_categories=("Comics",))
    now_ms = int(time.time()) * 1000
    items = [
        _item(0, category="Tech", published_ms=now_ms),
        _item(1, category="Comics", published_ms=now_ms),
        _item(2, category="Comics", published_ms=now_ms),
    ]

    result = select(items, cfg)

    assert [it.item_id for it in result.items] == ["tag:item/0000"]
    assert result.dropped_excluded_category == 2
    assert result.dropped_too_old == 0


def test_age_filter_drops_old_keeps_new_and_no_timestamp(config):
    cfg = dataclasses.replace(config, excluded_categories=(), max_age_days=7)
    now_ms = int(time.time()) * 1000
    items = [
        _item(0, published_ms=now_ms - 1 * 86400 * 1000),  # 1 day old — kept
        _item(1, published_ms=now_ms - 30 * 86400 * 1000),  # 30 days old — dropped
        _item(2, published_ms=0),  # no timestamp — kept
    ]

    result = select(items, cfg)

    assert [it.item_id for it in result.items] == ["tag:item/0000", "tag:item/0002"]
    assert result.dropped_too_old == 1
    assert result.no_timestamp == 1
    assert result.dropped_excluded_category == 0


def test_excluded_category_takes_precedence_over_age_filter(config):
    """An item that's both in an excluded category AND too old counts only
    as 'excluded category' — exclusion runs first and short-circuits."""
    cfg = dataclasses.replace(config, excluded_categories=("Comics",), max_age_days=7)
    now_ms = int(time.time()) * 1000
    items = [
        _item(0, category="Comics", published_ms=now_ms - 30 * 86400 * 1000),
    ]

    result = select(items, cfg)

    assert result.items == []
    assert result.dropped_excluded_category == 1
    assert result.dropped_too_old == 0
