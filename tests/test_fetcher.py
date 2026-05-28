"""Tests for the full-text fetcher."""

from __future__ import annotations

from kindle_daily.fetcher import (
    ImageAsset,
    _fetch_one,
    _html_from_trafilatura_xml,
    _rewrite_images_inline,
    _summary_appears_truncated,
    _word_count,
    fetch_all,
)
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
    as full text. Length floor lives in fetcher._FULL_TEXT_MIN_WORDS."""
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


def test_summary_appears_truncated_signals():
    long_body = _long_body()

    # Marker links flag truncation even in an otherwise long body.
    cases_truncated = [
        _long_body(suffix=' <a href="x">Read more</a>'),
        _long_body(suffix=' <a href="x">Read more →</a>'),
        _long_body(suffix=' <a href="x">read the full article</a>'),
        _long_body(suffix=' <a href="x">Read on</a>'),
        _long_body(suffix=' <a href="x">Read the story</a>'),
        _long_body(suffix=' <a href="x">Read full article</a>'),
        _long_body(suffix=' <a href="x">Read article</a>'),
        _long_body(suffix=' <a href="x">Continue reading…</a>'),
        # Short body with no marker — flagged by the length floor.
        "<p>Just a one-sentence dek with no link.</p>",
    ]
    for html in cases_truncated:
        assert _summary_appears_truncated(html), html

    cases_full = [
        long_body,
        _long_body(suffix=' <a href="x">an inline citation link</a>'),
    ]
    for html in cases_full:
        assert not _summary_appears_truncated(html), html

    # Empty summary returns False — caller handles emptiness separately.
    assert not _summary_appears_truncated("")


def test_summary_appears_truncated_via_bracket_ellipsis():
    """WordPress / Drupal / Joomla / Ghost / Squarespace excerpts typically
    end with a bracketed ellipsis even when the body is long enough to
    pass the word-count floor. All variants must be detected."""
    long_body = _long_body()
    variants = [
        _long_body(suffix=" [...]"),
        _long_body(suffix=" [ ... ]"),
        _long_body(suffix=" [&hellip;]"),
        _long_body(suffix=" […]"),
        _long_body(suffix=" [&HELLIP;]"),  # entity is case-insensitive
    ]
    for html in variants:
        assert _summary_appears_truncated(html), html[:120]

    # Plain ellipsis at the end (no brackets) is intentionally NOT flagged
    # — it's too common in normal article prose to be a reliable signal.
    assert not _summary_appears_truncated(_long_body(suffix=" …"))
    assert not _summary_appears_truncated(long_body)


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


def test_word_count_strips_tags():
    assert _word_count("<p>One <b>two</b> three</p>") == 3
    assert _word_count("") == 0


# ----------------------------------------------- trafilatura XML translator


def test_html_from_xml_passes_through_plain_html():
    assert _html_from_trafilatura_xml("<p>hi</p>") == "<p>hi</p>"


def test_html_from_xml_empty_input():
    assert _html_from_trafilatura_xml("") == ""
    assert _html_from_trafilatura_xml("   ") == ""


def test_html_from_xml_maps_trafilatura_tags():
    xml = (
        "<main>"
        '<head rend="h1">Title</head>'
        '<p>Para one with <hi rend="#i">italic</hi> and '
        '<hi rend="#b">bold</hi>.</p>'
        '<graphic src="https://x/y.jpg" alt="a cat"/>'
        '<list rend="ol"><item>one</item><item>two</item></list>'
        "<quote>he said so</quote>"
        '<p>see <ref target="https://x/y">here</ref><lb/>line two</p>'
        "</main>"
    )
    out = _html_from_trafilatura_xml(xml)
    # <main> dropped; <head rend="h1"> -> <h2>
    assert "<main" not in out
    assert "<h2>Title</h2>" in out
    # <hi> -> <em>/<strong>
    assert "<em>italic</em>" in out
    assert "<strong>bold</strong>" in out
    # <graphic> -> <img>
    assert '<img src="https://x/y.jpg" alt="a cat">' in out
    # <list rend="ol"> -> <ol> with <li>s
    assert "<ol><li>one</li><li>two</li></ol>" in out
    # <quote> -> <blockquote>
    assert "<blockquote>he said so</blockquote>" in out
    # <ref target=...> -> <a href=...>
    assert '<a href="https://x/y">here</a>' in out
    # <lb/> -> <br>
    assert "<br>" in out


def test_html_from_xml_drops_unknown_tags_but_keeps_text():
    xml = "<weirdtag>kept</weirdtag><p>body</p>"
    out = _html_from_trafilatura_xml(xml)
    assert "weirdtag" not in out
    assert "kept" in out
    assert "<p>body</p>" in out


# ----------------------------------------------- inline image rewriter


def _fake_asset(url: str) -> ImageAsset:
    return ImageAsset(
        local_name=f"images/{abs(hash(url)) % 10**8:08d}.jpg",
        media_type="image/jpeg",
        data=b"\xff\xd8\xff",
        source_url=url,
        orig_width=800,
        orig_height=600,
    )


def test_rewrite_images_inline_rewrites_src_and_drops_failures(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    def fake_download(client, url):
        return _fake_asset(url) if "good" in url else None

    monkeypatch.setattr(fetcher_mod, "_download_and_optimize", fake_download)

    body = (
        "<p>before</p>"
        '<img src="https://x.test/good-1.jpg" alt="ok"/>'
        '<img src="https://x.test/bad.jpg" alt="broken"/>'
        '<img src="https://x.test/good-2.jpg" alt="ok2"/>'
        "<p>after</p>"
    )
    out, assets = _rewrite_images_inline(body, "https://x.test/article", client=None)
    assert len(assets) == 2
    # Both surviving images point at local assets, in document order
    assert assets[0].source_url.endswith("good-1.jpg")
    assert assets[1].source_url.endswith("good-2.jpg")
    assert assets[0].local_name in out
    assert assets[1].local_name in out
    # The failed image's tag was dropped entirely
    assert "bad.jpg" not in out
    # Surrounding content preserved
    assert "before" in out and "after" in out


def test_rewrite_images_inline_caps_at_max_per_article(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    monkeypatch.setattr(fetcher_mod, "_download_and_optimize", lambda _c, u: _fake_asset(u))

    imgs = "".join(f'<img src="https://x.test/img-{i}.jpg"/>' for i in range(fetcher_mod.MAX_IMAGES_PER_ARTICLE + 3))
    out, assets = _rewrite_images_inline(imgs, "https://x.test/", client=None)
    assert len(assets) == fetcher_mod.MAX_IMAGES_PER_ARTICLE
    # Over-cap images are dropped from the HTML so Kindle doesn't show
    # broken-image placeholders for them.
    assert out.count("<img") == fetcher_mod.MAX_IMAGES_PER_ARTICLE


def test_rewrite_images_inline_dedupes_repeated_urls(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    call_count = {"n": 0}

    def fake_download(client, url):
        call_count["n"] += 1
        return _fake_asset(url)

    monkeypatch.setattr(fetcher_mod, "_download_and_optimize", fake_download)

    body = '<img src="https://x.test/same.jpg"/>' '<img src="https://x.test/same.jpg"/>'
    out, assets = _rewrite_images_inline(body, "https://x.test/", client=None)
    assert len(assets) == 1
    assert call_count["n"] == 1
    # Both <img> tags remain, pointing at the same local file
    assert out.count(assets[0].local_name) == 2


def test_rewrite_images_inline_resolves_relative_and_lazy(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    captured: list[str] = []

    def fake_download(client, url):
        captured.append(url)
        return _fake_asset(url)

    monkeypatch.setattr(fetcher_mod, "_download_and_optimize", fake_download)

    body = '<img src="data:image/gif;base64,xxx" data-src="/lazy.jpg"/>' '<img src="../up.jpg"/>'
    _out, assets = _rewrite_images_inline(body, "https://x.test/section/article", client=None)
    # data: URL ignored; lazy-load attribute picked up
    assert captured[0] == "https://x.test/lazy.jpg"
    # relative URL resolved against base
    assert captured[1] == "https://x.test/up.jpg"
    assert len(assets) == 2


def test_rewrite_images_inline_skips_noise_substrings(monkeypatch):
    import kindle_daily.fetcher as fetcher_mod

    monkeypatch.setattr(fetcher_mod, "_download_and_optimize", lambda _c, u: _fake_asset(u))

    body = '<img src="https://x.test/logo.png"/>' '<img src="https://x.test/photo.jpg"/>'
    out, assets = _rewrite_images_inline(body, "https://x.test/", client=None)
    assert len(assets) == 1
    assert assets[0].source_url.endswith("photo.jpg")
    assert "logo.png" not in out
