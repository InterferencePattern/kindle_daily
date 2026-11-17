"""Tests for the full-text fetcher."""

from __future__ import annotations

from kindle_daily.fetcher import _fetch_one, fetch_all
from kindle_daily.freshrss import FeedItem


def _make_item(url: str = "", summary: str = "") -> FeedItem:
    return FeedItem(
        item_id="tag:item/x",
        title="t",
        url=url,
        feed_title="f",
        category="Misc",
        summary_html=summary,
        published_ms=0,
        author="",
    )


def test_fetcher_falls_back_to_summary_when_no_url():
    art = _fetch_one(_make_item(url="", summary="<p>summary body</p>"))
    assert art is not None
    assert art.body_html == "<p>summary body</p>"
    assert art.word_count == 2


def test_fetcher_skips_when_no_body_anywhere():
    assert _fetch_one(_make_item(url="", summary="")) is None


def test_fetcher_uses_trafilatura_when_summary_is_truncated(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    monkeypatch.setattr(
        fetcher_mod.trafilatura,
        "fetch_url",
        lambda url, **kw: "<html>...</html>",
    )
    monkeypatch.setattr(
        fetcher_mod.trafilatura,
        "extract",
        lambda *_a, **_kw: "<p>extracted full text body</p>",
    )

    summary = '<p>Teaser sentence. <a href="https://x/y">Read more</a></p>'
    art = _fetch_one(_make_item(url="https://x/y", summary=summary))
    assert art is not None
    assert art.body_html == "<p>extracted full text body</p>"


def _long_body(prefix: str = "", suffix: str = "") -> str:
    """Build a >=150-word HTML body so the length-floor heuristic treats it
    as full text. Length floor lives in html_cleanup._FULL_TEXT_MIN_WORDS."""
    words = " ".join(f"word{i}" for i in range(200))
    return f"<p>{prefix}{words}{suffix}</p>"


def test_fetcher_trusts_full_text_summary_and_skips_fetch(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    calls = {"fetch": 0}

    def _spy_fetch(url, **kw):
        calls["fetch"] += 1
        return "<html>...</html>"

    monkeypatch.setattr(fetcher_mod.trafilatura, "fetch_url", _spy_fetch)
    monkeypatch.setattr(
        fetcher_mod.trafilatura,
        "extract",
        lambda *_a, **_kw: "<p>should not be used</p>",
    )

    summary = _long_body()
    art = _fetch_one(_make_item(url="https://x/y", summary=summary))
    assert art is not None
    assert art.body_html == summary
    assert calls["fetch"] == 0


def test_fetcher_fetches_when_summary_is_short(monkeypatch):
    """Short summaries (e.g. ACLU's one-sentence dek) should trigger a fetch
    even when no "read more" marker is present."""
    import kindle_daily.fetcher as fetcher_mod

    monkeypatch.setattr(
        fetcher_mod.trafilatura,
        "fetch_url",
        lambda url, **kw: "<html>...</html>",
    )
    monkeypatch.setattr(
        fetcher_mod.trafilatura,
        "extract",
        lambda *_a, **_kw: "<p>extracted full text body</p>",
    )

    short_summary = "<p>One short sentence, no marker link.</p>"
    art = _fetch_one(_make_item(url="https://x/y", summary=short_summary))
    assert art is not None
    assert art.body_html == "<p>extracted full text body</p>"


def test_fetch_all_preserves_order_and_drops_failures(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    monkeypatch.setattr(fetcher_mod.trafilatura, "fetch_url", lambda url, **kw: None)
    monkeypatch.setattr(fetcher_mod.trafilatura, "extract", lambda *a, **k: None)

    items = [
        _make_item(url="https://a/", summary="<p>A body</p>"),
        _make_item(url="https://b/", summary=""),  # will be dropped
        _make_item(url="https://c/", summary="<p>C body</p>"),
    ]
    out = fetch_all(items)
    bodies = [a.body_html for a in out]
    assert bodies == ["<p>A body</p>", "<p>C body</p>"]


def test_fetch_all_drops_only_the_item_that_raises(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    real_fetch_one = fetcher_mod._fetch_one

    def flaky(item):
        if item.url == "https://b/":
            raise RuntimeError("boom")
        return real_fetch_one(item)

    monkeypatch.setattr(fetcher_mod, "_fetch_one", flaky)

    items = [
        _make_item(url="https://a/", summary="<p>A body</p>"),
        _make_item(url="https://b/", summary="<p>B body</p>"),
        _make_item(url="https://c/", summary="<p>C body</p>"),
    ]
    assert [a.body_html for a in fetch_all(items)] == ["<p>A body</p>", "<p>C body</p>"]


def test_fetcher_sanitizes_full_text_rss_body(monkeypatch):
    # The RSS-body path skips trafilatura; it must still be sanitized.
    body = "<p>" + "word " * 400 + "</p><audio controls/>"
    item = _make_item(url="https://x.test/a", summary=body)
    art = _fetch_one(item)
    assert art is not None
    assert "audio" not in art.body_html


# ----------------------------------------------- inline image rewriter
