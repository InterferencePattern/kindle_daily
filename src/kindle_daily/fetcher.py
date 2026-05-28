"""Full-text extraction for FreshRSS items.

Uses :mod:`trafilatura` to pull clean article HTML from each item's URL. If
extraction fails, falls back to whatever HTML the feed itself published.

Trafilatura's HTML output drops ``<img>`` tags even with
``include_images=True``, so we ask it for XML (which preserves images
inline as ``<graphic>``) and translate that to HTML ourselves. Every
``<img>`` in the resulting body is then downloaded, optimized for the
Kindle (resized, grayscale, JPEG), and rewritten to point at the local
asset — preserving the author's image placement.
"""

from __future__ import annotations

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from lxml import html as lxml_html
from PIL import Image, UnidentifiedImageError
from trafilatura.settings import use_config

from .freshrss import FeedItem

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 15
FETCH_WORKERS = 5
WORDS_PER_MINUTE = 220

# Browser User-Agent for trafilatura fetches. Without this, many publishers
# (Verge, Wired, Ars, etc.) 403 us as a bot. Doesn't help with strict paywalls
# (NYT/WSJ/FT), which gate on cookies as well — those still fall back to the
# FreshRSS-stored summary.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)
_TRAFILATURA_CONFIG = use_config()
_TRAFILATURA_CONFIG.set("DEFAULT", "USER_AGENTS", _BROWSER_UA)

# Kindle Paperwhite 7 screen is 758x1024 px, greyscale e-ink. Anything bigger is
# wasted bytes; JPEG Q75 + 8-bit greyscale is about as small as we can go
# without visibly degrading photographs.
IMG_MAX_WIDTH = 758
IMG_MAX_HEIGHT = 1024
IMG_JPEG_QUALITY = 75
IMG_MIN_DIM = 50  # skip likely tracking pixels / icons
IMG_DOWNLOAD_TIMEOUT_S = 10
IMG_MAX_BYTES_RAW = 8 * 1024 * 1024  # cap raw download to keep memory bounded


@dataclass(frozen=True)
class ImageAsset:
    """One inline image, already optimized and ready to embed in the EPUB.

    Attributes
    ----------
    local_name : str
        Relative filename used inside the EPUB (e.g. ``images/abc123.jpg``).
        Also the value written into the corresponding ``<img src=...>``.
    media_type : str
        MIME type, e.g. ``image/jpeg``.
    data : bytes
        Optimized image bytes.
    source_url : str
        Original absolute URL the image was fetched from. Used by the cover
        renderer to re-download a high-resolution version of the chosen hero.
    orig_width : int
        Source image width in pixels, before our resize. Used by the cover
        picker to score candidates without re-downloading.
    orig_height : int
        Source image height in pixels, before our resize.
    """

    local_name: str
    media_type: str
    data: bytes
    source_url: str = ""
    orig_width: int = 0
    orig_height: int = 0


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
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(_fetch_one, items))
    return [a for a in results if a is not None]


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

    if item.summary_html and not _summary_appears_truncated(item.summary_html):
        body_html = item.summary_html
        log.info(
            "using RSS body for %s (no truncation marker; len=%d words=%d preview=%r)",
            item.url or item.title,
            len(item.summary_html),
            _word_count(item.summary_html),
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
                body_html = _html_from_trafilatura_xml(xml_body or "")
        except Exception as exc:  # pragma: no cover - network paths
            log.warning("trafilatura failed for %s: %s", item.url, exc)

    if not body_html:
        body_html = item.summary_html
    if not body_html:
        log.warning("no body for %s; skipping", item.url or item.title)
        return None

    body_html = _strip_duplicate_title(body_html, item.title)
    with httpx.Client(timeout=IMG_DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client:
        body_html, images = _rewrite_images_inline(body_html, item.url, client)
    word_count = _word_count(body_html)
    return Article(
        item_id=item.item_id,
        title=item.title,
        url=item.url,
        feed_title=item.feed_title,
        category=item.category or "Misc",
        body_html=body_html,
        author=item.author,
        word_count=word_count,
        images=images,
    )


def _word_count(html: str) -> int:
    """Rough word count of an HTML fragment.

    Strips tags by character class — good enough for a reading-time estimate.
    """
    in_tag = False
    chars: list[str] = []
    for ch in html:
        if ch == "<":
            in_tag = True
            chars.append(" ")
        elif ch == ">":
            in_tag = False
            chars.append(" ")
        elif not in_tag:
            chars.append(ch)
    return len("".join(chars).split())


# --------------------------------------------------- truncation detection

# Anchor text that signals "this RSS body is just an excerpt." Matched
# case-insensitively against the collapsed text of each <a> in the summary.
# Trailing arrows / ellipses / punctuation are tolerated via ``\W*$``.
_TRUNCATION_MARKER_RE = re.compile(
    r"^(read\s+(more|on|(the\s+)?(full\s+)?(article|story|post))|continue\s+reading)\W*$",
    re.IGNORECASE,
)

# Summaries shorter than this are treated as teasers even if they contain no
# marker link. Catches publishers (e.g. ACLU) who emit only a one-sentence
# dek with no "read more" anchor — without this we'd ship the dek as the
# article body. 150 was chosen as comfortably below the shortest plausible
# full post in the feeds we read.
_FULL_TEXT_MIN_WORDS = 150

# WordPress's the_excerpt() and many other CMSes (Drupal, Joomla, Ghost,
# Squarespace) end truncated excerpts with a bracketed ellipsis. The body
# is sometimes long enough to pass the word-count floor but is still a
# teaser. Detecting this marker is essentially free and avoids per-feed
# allowlists. Matches: [...], [ ... ], [&hellip;], […], with optional
# whitespace inside the brackets.
_BRACKET_ELLIPSIS_RE = re.compile(
    r"\[\s*(?:&hellip;|…|\.\s*\.\s*\.+)\s*\]",
    re.IGNORECASE,
)


def _summary_appears_truncated(summary_html: str) -> bool:
    """Return True if the RSS summary looks like a teaser rather than full text.

    Three signals are checked:

    1. Total word count below :data:`_FULL_TEXT_MIN_WORDS`.
    2. A bracketed-ellipsis marker (``[...]``, ``[&hellip;]``, ``[…]``) —
       WordPress's excerpt truncation glyph, also used by most other CMSes.
    3. Anchor text matching :data:`_TRUNCATION_MARKER_RE` ("read more", etc.).

    Any one is sufficient. Used to decide whether the FreshRSS-stored body
    can be trusted as-is or whether we should fetch the live page.
    """
    if not summary_html:
        return False
    if _word_count(summary_html) < _FULL_TEXT_MIN_WORDS:
        return True
    if _BRACKET_ELLIPSIS_RE.search(summary_html):
        return True
    try:
        root = lxml_html.fragment_fromstring(summary_html, create_parent="div")
    except (lxml_html.etree.ParserError, ValueError):
        return False
    for a in root.xpath(".//a"):
        text = " ".join("".join(a.itertext()).split())
        if text and _TRUNCATION_MARKER_RE.match(text):
            return True
    return False


# --------------------------------------------------------- title dedupe


def _strip_duplicate_title(body_html: str, title: str) -> str:
    """Drop a body ``<h1>`` whose text equals ``title``.

    Some publishers (notably The Verge full-RSS feed) include the article
    headline inline in their HTML, which trafilatura preserves. The chapter
    page already renders the title as its own ``<h1>``, so without this we
    get the title twice. Subheadings (``<h2>``/``<h3>``) are left alone.
    """
    if not title or "<h1" not in body_html.lower():
        return body_html

    try:
        root = lxml_html.fragment_fromstring(body_html, create_parent="div")
    except (lxml_html.etree.ParserError, ValueError):
        return body_html

    target = _normalize(title)
    removed = False
    for h1 in root.xpath(".//h1"):
        text = "".join(h1.itertext())
        if _normalize(text) == target:
            _drop(h1)
            removed = True

    if not removed:
        return body_html

    out = lxml_html.tostring(root, encoding="unicode", method="html")
    if out.startswith("<div>") and out.endswith("</div>"):
        out = out[len("<div>") : -len("</div>")]
    return out


def _normalize(s: str) -> str:
    """Lowercase, replace nbsp with space, collapse internal whitespace."""
    return " ".join(s.replace("\xa0", " ").lower().split())


def _drop(element) -> None:
    """Remove an element while preserving its tail text."""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail or ""
    if tail:
        prev = element.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


# ----------------------------------------------------------------- images

# Per article, cap how many distinct images we embed. Hero counts toward this.
MAX_IMAGES_PER_ARTICLE = 5

# Lazy-load attribute names that often hold the real image URL on news sites.
_LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-hi-res-src")

# Likely-irrelevant images by URL substring (logos, share buttons, ads, …).
_NOISE_SUBSTRINGS = (
    "/avatar",
    "/avatars",
    "gravatar.com",
    "/logo",
    "/sprites",
    "/share",
    "tracking",
    "pixel",
    "/ads/",
    "doubleclick",
)


# Trafilatura emits images as <graphic src="..." alt="..."/>. The translator
# below converts trafilatura's TEI-ish XML to HTML the EPUB renderer understands.
_TRAFILATURA_TAG_RENAMES = {
    "p": "p",
    "quote": "blockquote",
    "code": "code",
    "item": "li",
    "row": "tr",
    "cell": "td",
    "table": "table",
    "lb": "br",
}

_HEAD_LEVEL_BY_REND = {"h1": "h2", "h2": "h2", "h3": "h3", "h4": "h4"}


def _html_from_trafilatura_xml(xml_body: str) -> str:
    """Translate trafilatura's XML output to clean HTML.

    Trafilatura's XML preserves images inline as ``<graphic src=...>``
    along with TEI-ish tags (``<head>``, ``<p>``, ``<list>``, ``<item>``,
    ``<hi>``, ``<quote>``, ``<row>``, ``<cell>``, ``<ref>``, ``<lb/>``)
    that we map to plain HTML equivalents. Unknown tags are unwrapped —
    their text content survives but the tag itself is dropped.

    Returns an empty string if the input is empty or unparseable.
    """
    if not xml_body or not xml_body.strip():
        return ""
    # Parse as XML, not HTML — lxml.html treats <head> as the HTML head
    # element and reshapes the tree. We wrap in a dummy root so multiple
    # top-level tags are allowed.
    try:
        root = lxml_html.etree.fromstring(f"<kdroot>{xml_body}</kdroot>")
    except lxml_html.etree.XMLSyntaxError:
        return xml_body  # fall back: emit as-is, downstream still works

    # Translate in two passes: first rename/rewrite, then unwrap unknowns.
    # Walking with iter() while mutating is fine because we never add new
    # elements — we only rename tags and rewrite attributes.
    for el in list(root.iter()):
        tag = el.tag if isinstance(el.tag, str) else None
        if tag is None:
            continue
        if tag == "graphic":
            src = el.get("src", "")
            alt = el.get("alt", "")
            el.tag = "img"
            el.attrib.clear()
            if src:
                el.set("src", src)
            el.set("alt", alt)
            continue
        if tag == "ref":
            target = el.get("target", "")
            el.tag = "a"
            el.attrib.clear()
            if target:
                el.set("href", target)
            continue
        if tag == "head":
            level = _HEAD_LEVEL_BY_REND.get(el.get("rend", ""), "h2")
            el.tag = level
            el.attrib.clear()
            continue
        if tag == "list":
            el.tag = "ol" if el.get("rend") == "ol" else "ul"
            el.attrib.clear()
            continue
        if tag == "hi":
            el.tag = "strong" if el.get("rend") == "#b" else "em"
            el.attrib.clear()
            continue
        if tag in _TRAFILATURA_TAG_RENAMES:
            el.tag = _TRAFILATURA_TAG_RENAMES[tag]
            el.attrib.clear()
            continue
        # Drop empty <graphic/> with no src already handled above; any
        # other unknown tag gets unwrapped after the rename pass.

    # Unwrap any remaining tags that aren't valid HTML (e.g. <main>, <doc>,
    # plus stray TEI tags we didn't enumerate). We do this after renames so
    # renamed-but-still-existing parents aren't mistakenly unwrapped.
    _ALLOWED_HTML_TAGS = {
        "a",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "figure",
        "figcaption",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if el is root:
            continue
        if el.tag not in _ALLOWED_HTML_TAGS:
            _unwrap(el)

    out = lxml_html.tostring(root, encoding="unicode", method="html")
    if out.startswith("<kdroot>") and out.endswith("</kdroot>"):
        out = out[len("<kdroot>") : -len("</kdroot>")]
    return out


def _unwrap(element) -> None:
    """Replace ``element`` with its children, preserving text and tails.

    Used to drop a tag we don't want in the HTML output without losing its
    content. ``<foo>hi <b>bold</b> bye</foo>`` becomes ``hi <b>bold</b> bye``.
    """
    parent = element.getparent()
    if parent is None:
        return
    index = list(parent).index(element)
    # Promote element.text onto the previous sibling's tail / parent's text
    head_text = element.text or ""
    if head_text:
        if index == 0:
            parent.text = (parent.text or "") + head_text
        else:
            prev = parent[index - 1]
            prev.tail = (prev.tail or "") + head_text
    # Move children up, in order, into element's slot
    for offset, child in enumerate(list(element)):
        parent.insert(index + offset, child)
    # element.tail goes after the last promoted child (or onto prev/parent.text
    # if element had no children)
    tail_text = element.tail or ""
    if tail_text:
        last_promoted = parent[index + len(element) - 1] if len(element) else None
        if last_promoted is not None:
            last_promoted.tail = (last_promoted.tail or "") + tail_text
        elif index == 0:
            parent.text = (parent.text or "") + tail_text
        else:
            prev = parent[index - 1]
            prev.tail = (prev.tail or "") + tail_text
    parent.remove(element)


def _rewrite_images_inline(
    body_html: str, base_url: str, client: httpx.Client
) -> tuple[str, tuple[ImageAsset, ...]]:
    """Download every ``<img>`` in ``body_html`` and rewrite ``src`` to local.

    Walks the body in document order. For each ``<img>``:

    1. Resolves the real source URL from ``src`` / ``data-src`` / ``srcset``
       / other lazy-load attributes, against ``base_url``.
    2. Skips obvious noise (data URLs, logos, tracking pixels) and
       already-embedded assets.
    3. Stops after :data:`MAX_IMAGES_PER_ARTICLE` successful embeds; any
       remaining ``<img>`` tags are removed so they don't render as
       broken-image placeholders on the Kindle.
    4. On download/decode failure, the ``<img>`` is also removed.

    Returns the rewritten HTML and the embedded :class:`ImageAsset` tuple,
    in document order. Assets dedupe within an article (same source URL
    hashes to the same ``local_name``).
    """
    if not body_html or "<img" not in body_html.lower():
        return body_html, ()

    try:
        root = lxml_html.fragment_fromstring(body_html, create_parent="div")
    except (lxml_html.etree.ParserError, ValueError):
        return body_html, ()

    assets: list[ImageAsset] = []
    seen_local: set[str] = set()
    seen_source: dict[str, str] = {}  # source URL -> local_name (for dedupe)

    for img in list(root.iter("img")):
        absolute = _resolve_img_src(img, base_url)
        if not absolute:
            _drop(img)
            continue
        if len(assets) >= MAX_IMAGES_PER_ARTICLE and absolute not in seen_source:
            _drop(img)
            continue

        local_name = seen_source.get(absolute)
        if local_name is None:
            asset = _download_and_optimize(client, absolute)
            if asset is None:
                _drop(img)
                continue
            local_name = asset.local_name
            seen_source[absolute] = local_name
            if local_name not in seen_local:
                seen_local.add(local_name)
                assets.append(asset)

        # Rewrite this <img> to point at the local asset; strip noisy attrs
        # (srcset, lazy-load, sizes) so the EPUB renderer doesn't argue.
        alt = img.get("alt", "")
        img.attrib.clear()
        img.set("src", local_name)
        img.set("alt", alt)

    out = lxml_html.tostring(root, encoding="unicode", method="html")
    if out.startswith("<div>") and out.endswith("</div>"):
        out = out[len("<div>") : -len("</div>")]
    return out, tuple(assets)


def _resolve_img_src(img, base_url: str) -> str:
    """Return the absolute source URL for an ``<img>`` or '' if unusable.

    Tries ``src`` first, then lazy-load attributes (``data-src``,
    ``data-original``, …), then ``srcset`` (first URL). Skips ``data:``
    URLs, relative URLs that can't be resolved, and known noise paths
    (logos, tracking pixels, etc.).
    """
    candidates = [img.get("src")]
    for attr in _LAZY_ATTRS:
        candidates.append(img.get(attr))
    srcset = img.get("srcset")
    if srcset:
        candidates.append(srcset.strip().split()[0])

    for raw in candidates:
        if not raw:
            continue
        candidate = raw.strip().split()[0]
        if not candidate or candidate.startswith("data:"):
            continue
        absolute = urljoin(base_url, candidate) if base_url else candidate
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if any(noise in absolute.lower() for noise in _NOISE_SUBSTRINGS):
            continue
        return absolute
    return ""


def _download_and_optimize(client: httpx.Client, url: str) -> ImageAsset | None:
    """Download an image, optimize it for Kindle, return an :class:`ImageAsset`.

    Returns ``None`` for tracking pixels, decode failures, or network errors.
    """
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        log.info("image download failed for %s: %s", url, exc)
        return None

    raw = resp.content
    if not raw or len(raw) > IMG_MAX_BYTES_RAW:
        log.info("image %s skipped (size=%d)", url, len(raw) if raw else 0)
        return None

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            orig_w, orig_h = im.width, im.height
            if orig_w < IMG_MIN_DIM or orig_h < IMG_MIN_DIM:
                return None
            im = im.convert("L")  # 8-bit greyscale
            im.thumbnail((IMG_MAX_WIDTH, IMG_MAX_HEIGHT), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=IMG_JPEG_QUALITY, optimize=True)
            data = buf.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        log.info("image decode failed for %s: %s", url, exc)
        return None

    # Stable filename keyed off the source URL — dedupes when the same image
    # appears in multiple articles (rare, but cheap).
    digest = _stable_hash(url)
    return ImageAsset(
        local_name=f"images/{digest}.jpg",
        media_type="image/jpeg",
        data=data,
        source_url=url,
        orig_width=orig_w,
        orig_height=orig_h,
    )


def _stable_hash(value: str) -> str:
    """Short stable hex digest of ``value`` for use in a filename."""
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
