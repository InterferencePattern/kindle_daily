"""Magazine-style EPUB 3 builder.

Layout (in spine order):
    1. Cover           — masthead, date, stats. Only included as an XHTML
                         page when no ``cover_jpeg`` was supplied; otherwise
                         the registered cover image is the title page and a
                         second typographic cover would just duplicate it.
    2. Contents        — clickable in-document TOC, grouped by section
    3. For each section:
         a. Section opener (full-page divider)
         b. Article XHTML pages
    4. Colophon

Both EPUB3 ``nav.xhtml`` and legacy NCX are populated, so the Kindle's
hamburger TOC works on every device generation.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from collections import OrderedDict
from importlib import resources
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from ebooklib import epub

from .fetcher import Article

log = logging.getLogger(__name__)

CSS_FILENAME = "styles.css"


def build(
    articles: list[Article],
    date: dt.date,
    out_dir: Path,
    cover_jpeg: bytes | None = None,
    part: tuple[int, int] | None = None,
) -> Path:
    """Build a single magazine-style EPUB from a list of articles.

    The book carries every article's Category as a TOC section, so one Run's
    combined Issue is a single EPUB regardless of how many Categories it spans.

    Parameters
    ----------
    articles : list[Article]
        Pre-fetched articles. Order is preserved within each section.
    date : datetime.date
        Issue date — used for the cover, filename, and EPUB metadata.
    out_dir : Path
        Directory to write the EPUB into. Created if missing.
    cover_jpeg : bytes, optional
        Pre-rendered cover image. If provided, registered as the EPUB's
        cover via ``book.set_cover()`` so the Kindle home screen shows
        it as the issue thumbnail. The typographic in-document
        ``cover.xhtml`` is unaffected.
    part : tuple[int, int], optional
        ``(i, n)`` for the ``i``-th of ``n`` Parts (1-indexed). When given,
        the title, identifier, and filename all gain a ``Part i`` / ``part
        i of n`` suffix so the Parts of one Run land as distinct items in
        the Kindle library.

    Returns
    -------
    Path
        Filesystem path to the newly-written ``.epub``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    book = epub.EpubBook()
    date_long = date.strftime("%A, %B %-d, %Y")
    part_title_suffix = f" Part {part[0]}" if part else ""
    part_id_suffix = f"-part{part[0]}of{part[1]}" if part else ""
    issue_id = f"kindle-daily-{date.isoformat()}{part_id_suffix}"
    title = f"Kindle Daily{part_title_suffix} — {date_long}"
    book.set_identifier(issue_id)
    book.set_title(title)
    book.set_language("en")
    book.add_author("kindle_daily")
    if cover_jpeg:
        # create_page=False: we register the image as cover metadata so the
        # Kindle home screen thumbnail picks it up; Kindle's reader still
        # renders it as the opened-book title page. We deliberately skip
        # the typographic cover.xhtml below in this case to avoid showing
        # the masthead/date/stats twice in a row.
        book.set_cover("cover.jpg", cover_jpeg, create_page=False)

    css_text = _load_css()
    css_item = epub.EpubItem(
        uid="styles",
        file_name=CSS_FILENAME,
        media_type="text/css",
        content=css_text,
    )
    book.add_item(css_item)

    _add_image_assets(book, articles)

    by_section = _group_by_section(articles)
    feed_count = len({a.feed_title for a in articles})

    contents = _make_contents(by_section)
    colophon = _make_colophon(date)
    spine: list = []
    if not cover_jpeg:
        cover = _make_cover(date, len(articles), feed_count)
        book.add_item(cover)
        spine.append(cover)
    for page in (contents, colophon):
        book.add_item(page)
    spine.append(contents)
    toc: list = []

    for section_name, section_articles in by_section.items():
        opener = _make_section_opener(section_name)
        book.add_item(opener)
        spine.append(opener)

        article_pages = []
        for art in section_articles:
            page = _make_article_page(art)
            book.add_item(page)
            spine.append(page)
            article_pages.append(page)

        toc.append((epub.Section(section_name, href=opener.file_name), article_pages))

    spine.append(colophon)

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    part_file_suffix = f"_part{part[0]}of{part[1]}" if part else ""
    out_path = out_dir / f"kindle_daily_{date.isoformat()}{part_file_suffix}.epub"
    epub.write_epub(str(out_path), book)
    log.info(
        "wrote %s (%d articles, %d sections, %d bytes)",
        out_path,
        len(articles),
        len(by_section),
        out_path.stat().st_size,
    )
    return out_path


# ---------------------------------------------------------------- internals


def _add_image_assets(book: epub.EpubBook, articles: list[Article]) -> None:
    """Register every unique :class:`~kindle_daily.fetcher.ImageAsset` once.

    Articles that share the same source image (via the ``local_name`` key)
    get deduped here so the EPUB doesn't carry the bytes twice.
    """
    seen: set[str] = set()
    for art in articles:
        for asset in art.images:
            if asset.local_name in seen:
                continue
            seen.add(asset.local_name)
            book.add_item(
                epub.EpubItem(
                    # local_name is "images/<hex>.jpg"; '/' isn't legal in an
                    # XML id, so key the manifest id off the hex stem.
                    uid=f"img-{PurePosixPath(asset.local_name).stem}",
                    file_name=asset.local_name,
                    media_type=asset.media_type,
                    content=asset.data,
                )
            )


def _load_css() -> str:
    """Load the bundled magazine stylesheet."""
    return resources.files("kindle_daily.assets").joinpath(CSS_FILENAME).read_text(encoding="utf-8")


def _group_by_section(articles: list[Article]) -> "OrderedDict[str, list[Article]]":
    """Group articles by section, preserving first-seen order."""
    groups: OrderedDict[str, list[Article]] = OrderedDict()
    for art in articles:
        groups.setdefault(art.category, []).append(art)
    return groups


def _slug(value: str) -> str:
    """Cheap slugifier for filenames and ids."""
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "x"


def _xhtml_page(file_name: str, title: str, body_html: str) -> epub.EpubHtml:
    """Build an XHTML chapter; ebooklib wraps it in <html>/<head>/<body>.

    The shared stylesheet is linked into the page's <head> via ``add_link``.
    """
    page = epub.EpubHtml(title=title, file_name=file_name, lang="en")
    page.content = body_html
    page.add_link(href=CSS_FILENAME, rel="stylesheet", type="text/css")
    return page


def _make_cover(date: dt.date, article_count: int, feed_count: int) -> epub.EpubHtml:
    body = (
        '<div class="cover">'
        '<div class="masthead">Kindle<br />Daily</div>'
        '<hr class="rule" />'
        f'<div class="date">{html.escape(date.strftime("%A, %B %-d, %Y"))}</div>'
        '<div class="ornament">· · ·</div>'
        f'<div class="stats">{article_count} articles · {feed_count} sources</div>'
        "</div>"
    )
    return _xhtml_page("cover.xhtml", "Cover", body)


def _make_contents(
    by_section: "OrderedDict[str, list[Article]]",
) -> epub.EpubHtml:
    parts = ['<div class="contents">', "<h1>Contents</h1>"]
    for section, articles in by_section.items():
        parts.append(f'<div class="section-name">{html.escape(section)}</div>')
        for art in articles:
            href = _article_href(art)
            parts.append(
                '<div class="toc-entry">'
                f'<span class="kicker">{html.escape(art.feed_title)}</span>'
                f'<a class="toc-title" href="{href}">{html.escape(art.title)}</a>'
                f' <span class="meta">· {art.reading_minutes} min</span>'
                "</div>"
            )
    parts.append("</div>")
    return _xhtml_page("contents.xhtml", "Contents", "".join(parts))


def _make_section_opener(section: str) -> epub.EpubHtml:
    body = '<div class="section-opener">' f"<h1>{html.escape(section)}</h1>" "</div>"
    return _xhtml_page(f"section-{_slug(section)}.xhtml", section, body)


def _make_article_page(art: Article) -> epub.EpubHtml:
    byline_bits = []
    if art.author:
        byline_bits.append(f"By {html.escape(art.author)}")
    byline_bits.append(f"{art.reading_minutes} min read")
    byline = " · ".join(byline_bits)

    domain = urlparse(art.url).netloc if art.url else ""
    source_html = ""
    if art.url:
        source_html = (
            '<div class="source">Source: '
            f'<a href="{html.escape(art.url)}">{html.escape(domain or art.url)}</a>'
            "</div>"
        )

    body = (
        '<div class="article">'
        f'<div class="kicker">{html.escape(art.feed_title)}</div>'
        f"<h1>{html.escape(art.title)}</h1>"
        f'<div class="byline">{byline}</div>'
        f'<div class="body">{art.body_html}</div>'
        f"{source_html}"
        "</div>"
    )
    return _xhtml_page(_article_href(art), art.title, body)


def _article_href(art: Article) -> str:
    """Stable filename for an article page, also used as the contents-page anchor."""
    return f"article-{_slug(art.feed_title)}-{_slug(art.title)[:60]}-{_slug(art.item_id)[-12:]}.xhtml"


def _make_colophon(date: dt.date) -> epub.EpubHtml:
    body = '<div class="colophon">' f"Generated {html.escape(date.isoformat())} · kindle_daily" "</div>"
    return _xhtml_page("colophon.xhtml", "Colophon", body)
