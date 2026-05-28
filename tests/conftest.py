"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from kindle_daily.config import Config
from kindle_daily.fetcher import Article


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """A test config with realistic-shaped values."""
    return Config(
        freshrss_url="http://freshrss.test",
        freshrss_user="alice",
        freshrss_api_password="api-pw",
        gmail_user="alice@gmail.com",
        gmail_app_password="x" * 16,
        kindle_email="alice_abc123@kindle.com",
        max_epub_bytes=15 * 1024 * 1024,
        max_age_days=7,
        excluded_categories=("Comics",),
        out_dir=tmp_path / "out",
    )


@pytest.fixture
def articles() -> list[Article]:
    """Two articles spread across two sections — enough to exercise grouping."""
    return [
        Article(
            item_id="tag:google.com,2005:reader/item/0001",
            title="Why the polls moved",
            url="https://www.theatlantic.com/x/y",
            feed_title="The Atlantic",
            category="Politics",
            body_html=(
                "<p>The first paragraph sets the scene with a reasonable amount "
                "of words to look like a real article.</p>"
                "<p>A second paragraph extends the argument.</p>"
                "<blockquote>And a punchy quote.</blockquote>"
            ),
            author="Anne Applebaum",
            word_count=42,
        ),
        Article(
            item_id="tag:google.com,2005:reader/item/0002",
            title="The aggregation thesis",
            url="https://stratechery.com/x",
            feed_title="Stratechery",
            category="Tech",
            body_html="<p>An essay about platforms and aggregation theory.</p>",
            author="Ben Thompson",
            word_count=8,
        ),
    ]
