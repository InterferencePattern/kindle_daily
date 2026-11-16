"""Tests for the daily cover renderer.

The roster-fitting logic is exercised directly with a character-based ``fits``
fake so the assertions are deterministic and font-independent. A guarded
integration test drives the full ``render_cover`` path (compositing included)
through a fake HTTP client when system fonts are available.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest
from PIL import Image

from kindle_daily import cover
from kindle_daily.fetcher import Article, ImageAsset


def _fits_max(limit: int):
    """A ``fits`` predicate that accepts strings up to ``limit`` characters."""
    return lambda text: len(text) <= limit


def test_roster_joins_all_categories_when_they_fit():
    text = cover._roster_text(["Photography", "Tech", "Misc"], fits=lambda _t: True)
    assert text == "PHOTOGRAPHY · TECH · MISC"


def test_roster_is_empty_for_no_categories():
    # No weekday fallback: an empty roster renders nothing.
    assert cover._roster_text([], fits=lambda _t: True) == ""


def test_roster_truncates_with_ellipsis_on_overflow():
    text = cover._roster_text(
        ["Photography", "Tech", "Politics", "Misc"],
        fits=_fits_max(20),
    )
    # Longest leading run that fits within 20 chars, plus the ellipsis.
    assert text == "PHOTOGRAPHY · TECH …"
    assert len(text) <= 20


def test_roster_keeps_at_least_the_first_category_truncated():
    # Even the first category overflows -> character-level truncation.
    text = cover._roster_text(["Photography"], fits=_fits_max(5))
    assert text.endswith("…")
    assert len(text) <= 5
    assert text.startswith("PHOT")


def test_roster_never_emits_a_weekday():
    # Whatever the input, the roster is built only from categories — a weekday
    # name can never appear (the old fallback is gone).
    for weekday in ("MONDAY", "TUESDAY", "WEDNESDAY"):
        assert weekday not in cover._roster_text([], fits=lambda _t: True)
    assert cover._roster_text(["Tech"], fits=lambda _t: True) == "TECH"


def _hero_article(width: int = 1200, height: int = 1600) -> Article:
    return Article(
        item_id="id-1",
        title="Hero",
        url="https://example.test/1",
        feed_title="Example",
        category="Photography",
        body_html="<p>x</p>",
        author="",
        word_count=1,
        images=(
            ImageAsset(
                local_name="images/h.jpg",
                media_type="image/jpeg",
                data=b"x",
                source_url="https://cdn.test/hero.jpg",
                orig_width=width,
                orig_height=height,
            ),
        ),
    )


def _serve_fake_hero(monkeypatch):
    """Serve a synthetic hero image instead of hitting the network."""
    hero = Image.new("RGB", (1200, 1600), color=(128, 128, 128))
    buf = io.BytesIO()
    hero.save(buf, format="JPEG")
    hero_bytes = buf.getvalue()

    class _FakeResp:
        content = hero_bytes

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, _url):
            return _FakeResp()

    monkeypatch.setattr(cover.httpx, "Client", _FakeClient)


def test_render_cover_returns_jpeg_with_roster(monkeypatch):
    """Full render path (with compositing) succeeds and yields JPEG bytes."""
    if cover._load_font(cover._SANS_BOLD, 10) is None:
        pytest.skip("no usable fonts on this system")
    _serve_fake_hero(monkeypatch)

    out = cover.render_cover(
        [_hero_article()],
        date=dt.date(2026, 5, 7),
        count=5,
        categories=["Photography", "Tech"],
    )

    assert out is not None
    assert out[:2] == b"\xff\xd8"  # JPEG start-of-image marker


def test_render_cover_returns_none_when_compose_raises(monkeypatch):
    """A compositing crash falls back to no cover instead of propagating."""
    if cover._load_font(cover._SANS_BOLD, 10) is None:
        pytest.skip("no usable fonts on this system")
    _serve_fake_hero(monkeypatch)

    def boom(*args, **kwargs):
        raise Image.DecompressionBombError("too big")

    monkeypatch.setattr(cover, "_compose", boom)

    out = cover.render_cover([_hero_article()], date=dt.date(2026, 5, 7), count=1)
    assert out is None
