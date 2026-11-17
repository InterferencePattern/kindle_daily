"""Tests for article-body HTML cleanup."""

from __future__ import annotations

from kindle_daily.html_cleanup import (
    html_from_trafilatura_xml,
    sanitize_html,
    strip_subscription_cta,
    summary_appears_truncated,
    word_count,
)


def _long_body(prefix: str = "", suffix: str = "") -> str:
    """Build a >=150-word HTML body so the length-floor heuristic treats it
    as full text. Length floor lives in html_cleanup._FULL_TEXT_MIN_WORDS."""
    words = " ".join(f"word{i}" for i in range(200))
    return f"<p>{prefix}{words}{suffix}</p>"


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
        assert summary_appears_truncated(html), html

    cases_full = [
        long_body,
        _long_body(suffix=' <a href="x">an inline citation link</a>'),
    ]
    for html in cases_full:
        assert not summary_appears_truncated(html), html


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
        assert summary_appears_truncated(html), html[:120]

    # Plain ellipsis at the end (no brackets) is intentionally NOT flagged
    # — it's too common in normal article prose to be a reliable signal.
    assert not summary_appears_truncated(_long_body(suffix=" …"))
    assert not summary_appears_truncated(long_body)


def test_word_count_strips_tags():
    assert word_count("<p>One <b>two</b> three</p>") == 3
    assert word_count("") == 0


def test_strip_subscription_cta_removes_promo_and_lead_in():
    # Mirrors SciAm's post-trafilatura shape: a short lead-in <h2> + CTA <p>,
    # wedged between real article paragraphs and a real section heading.
    body = (
        "<p>Real paragraph one with actual content.</p>"
        "<h2>On supporting science journalism</h2>"
        "<p>If you're enjoying this article, consider supporting our "
        'award-winning journalism by <a href="/sub/">subscribing</a>. By '
        "purchasing a subscription you are helping to ensure the future of "
        "impactful stories.</p>"
        "<h2>The Real Next Section</h2>"
        "<p>Real paragraph two.</p>"
    )
    out = strip_subscription_cta(body)
    assert "award-winning" not in out
    assert "supporting science journalism" not in out.lower()
    # real content untouched
    assert "Real paragraph one" in out
    assert "The Real Next Section" in out
    assert "Real paragraph two" in out


def test_strip_subscription_cta_keeps_prose_about_subscriptions():
    # A real paragraph that merely mentions subscribing must survive — the
    # regex targets solicitation constructs, not the word "subscribe".
    body = (
        "<p>The newspaper lost subscribers last year as readers cancelled "
        "their print subscriptions in favour of streaming.</p>"
    )
    assert strip_subscription_cta(body) == body


# ----------------------------------------------- trafilatura XML translator


def test_html_from_xml_passes_through_plain_html():
    assert html_from_trafilatura_xml("<p>hi</p>") == "<p>hi</p>"


def test_html_from_xml_empty_input():
    assert html_from_trafilatura_xml("") == ""
    assert html_from_trafilatura_xml("   ") == ""


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
    out = sanitize_html(html_from_trafilatura_xml(xml), "")
    # <main> unwrapped by the sanitizer; <head rend="h1"> -> <h2>
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


# ----------------------------------------------------------- sanitizer


def test_sanitize_unwraps_unknown_tags_but_keeps_text():
    out = sanitize_html("<weirdtag>kept</weirdtag><p>body</p>", "")
    assert "weirdtag" not in out
    assert "kept" in out
    assert "<p>body</p>" in out


def test_sanitize_drops_media_and_script_with_content():
    # The Stratechery podcast player that triggered Kindle's E016 fallback.
    body = (
        '<div><audio controls="controls" preload="none"/></div>'
        "<p>a</p><script>var x = 1;</script>"
        '<iframe src="https://yt.test/embed">fallback</iframe><p>b</p>'
    )
    out = sanitize_html(body, "")
    for gone in ("audio", "script", "var x", "iframe", "fallback"):
        assert gone not in out
    assert "<p>a</p>" in out and "<p>b</p>" in out


def test_sanitize_makes_links_absolute_and_drops_unusable_hrefs():
    body = (
        '<p><a href="/getsciam/">sub</a> <a href="javascript:void(0)">js</a> '
        '<a href="mailto:x@y.test">mail</a></p>'
    )
    out = sanitize_html(body, "https://www.sciam.test/article/x/")
    assert 'href="https://www.sciam.test/getsciam/"' in out
    assert "javascript" not in out
    assert "<a>js</a>" in out
    assert 'href="mailto:x@y.test"' in out


def test_sanitize_empty_input():
    assert sanitize_html("", "") == ""
    assert sanitize_html("   ", "") == ""
