"""Full-text fetching for FreshRSS items.

Uses :mod:`trafilatura` to pull clean article HTML from each item's URL. If
extraction fails, falls back to whatever HTML the feed itself published.

Trafilatura's HTML output drops ``<img>`` tags even with
``include_images=True``, so we ask it for XML (which preserves images
inline as ``<graphic>``) and translate that to HTML. Cleanup lives in
:mod:`~kindle_daily.html_cleanup`; image download and optimization in
:mod:`~kindle_daily.images`.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx
import trafilatura
from trafilatura.settings import use_config

from . import html_cleanup, images
from .freshrss import FeedItem
from .images import ImageAsset

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 15
FETCH_WORKERS = 5
WORDS_PER_MINUTE = 220

# Browser User-Agent for trafilatura fetches. Without this, many publishers
# (Verge, Wired, Ars, etc.) 403 us as a bot. Doesn't help with strict paywalls
# (NYT/WSJ/FT), which gate on cookies as well — those still fall back to the
# FreshRSS-stored summary. Override with USER_AGENT in .env when it ages.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36"
)
_TRAFILATURA_CONFIG = use_config()


@dataclass(frozen=True)
class Article:
    """A fetched article ready to render into the EPUB.

    Attributes
    ----------
    item_id : str
        Pass-through from :class:`~kindle_daily.freshrss.FeedItem`.
    title : str
        Article headline.
    url : str
        Canonical source URL.
    feed_title : str
        Source publication name (e.g. "The Atlantic").
    category : str
        FreshRSS category, used to group articles into magazine sections.
        Empty string means "uncategorized" — surfaced as ``"Misc"``.
    body_html : str
        Sanitized inner HTML for the article body, no ``<html>``/``<body>``
        wrapper. Safe to embed inside an EPUB XHTML chapter.
    author : str
        Best-known author. May be empty.
    word_count : int
        Used to compute reading-time on the contents page.
    images : tuple[ImageAsset, ...]
        Inline images referenced from ``body_html``. The builder is
        responsible for adding them to the EPUB package.
    """

    item_id: str
    title: str
    url: str
    feed_title: str
    category: str
    body_html: str
    author: str
    word_count: int
    images: tuple[ImageAsset, ...] = field(default_factory=tuple)

    @property
    def reading_minutes(self) -> int:
        """Estimated reading time, rounded up to at least 1 minute."""
        return max(1, round(self.word_count / WORDS_PER_MINUTE))


def user_agent() -> str:
    """Browser User-Agent for article and image fetches.

    Read at call time, because ``load_config`` loads ``.env`` into
    ``os.environ`` after this module is imported.
    """
    return os.environ.get("USER_AGENT") or _BROWSER_UA


def fetch_all(items: list[FeedItem]) -> list[Article]:
    """Fetch full text for every item, in parallel.

    Items whose extraction fails AND whose feed summary is empty are dropped
    with a warning. Order is preserved.

    Parameters
    ----------
    items : list[FeedItem]
        Unread items from FreshRSS.

    Returns
    -------
    list[Article]
        Successfully-fetched articles, in the same order as ``items``.
    """
    if not items:
        return []
    _TRAFILATURA_CONFIG.set("DEFAULT", "USER_AGENTS", user_agent())
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(_fetch_one_safe, items))
    return [a for a in results if a is not None]


def _fetch_one_safe(item: FeedItem) -> Article | None:
    """``_fetch_one``, but any exception drops just this article.

    An uncaught error would otherwise abort the run, and the article (still
    unread) would abort every run after it until it ages out.
    """
    try:
        return _fetch_one(item)
    except Exception:
        log.exception("fetch failed for %s; dropping it", item.url or item.title)
        return None


def _fetch_one(item: FeedItem) -> Article | None:
    """Fetch and clean a single article. Returns ``None`` on total failure.

    If the FreshRSS-stored summary already looks like full text (i.e. it
    contains no "read more"-style truncation marker link), we trust it and
    skip the live fetch entirely. This avoids picking up duplicated DOM
    fragments that some publishers render twice on the live page (e.g. The
    Verge's dek). When the marker is present, or the summary is empty, we
    fall through to trafilatura against the live URL.
    """
    body_html = ""

    if item.summary_html and not html_cleanup.summary_appears_truncated(
        item.summary_html
    ):
        body_html = item.summary_html
        log.debug(
            "using RSS body for %s (no truncation marker; len=%d words=%d preview=%r)",
            item.url or item.title,
            len(item.summary_html),
            html_cleanup.word_count(item.summary_html),
            item.summary_html[:300],
        )
    elif item.url:
        try:
            downloaded = trafilatura.fetch_url(item.url, config=_TRAFILATURA_CONFIG)
            if downloaded:
                xml_body = trafilatura.extract(
                    downloaded,
                    output_format="xml",
                    include_images=True,
                    include_links=True,
                    include_formatting=True,
                    include_tables=True,
                    favor_recall=True,
                )
                body_html = html_cleanup.html_from_trafilatura_xml(xml_body or "")
        except Exception as exc:  # pragma: no cover - network paths
            log.warning("trafilatura failed for %s: %s", item.url, exc)

    if not body_html:
        body_html = item.summary_html
    if not body_html:
        log.warning("no body for %s; skipping", item.url or item.title)
        return None

    body_html = html_cleanup.strip_duplicate_title(body_html, item.title)
    body_html = html_cleanup.strip_subscription_cta(body_html)
    body_html = html_cleanup.sanitize_html(body_html, item.url)
    if not body_html:
        log.warning("body empty after sanitizing %s; skipping", item.url or item.title)
        return None
    with httpx.Client(
        timeout=images.IMG_DOWNLOAD_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": user_agent()},
    ) as client:
        body_html, assets = images.rewrite_images_inline(body_html, item.url, client)
    word_count = html_cleanup.word_count(body_html)
    return Article(
        item_id=item.item_id,
        title=item.title,
        url=item.url,
        feed_title=item.feed_title,
        category=item.category or "Misc",
        body_html=body_html,
        author=item.author,
        word_count=word_count,
        images=assets,
    )
