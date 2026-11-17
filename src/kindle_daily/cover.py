"""Auto-generated EPUB cover for the daily issue.

Picks the largest hero image among the day's articles, re-downloads it at
full resolution, center-crops to portrait, converts to greyscale, and
overlays the masthead, category roster, date, and article count.

The cover is registered with ``book.set_cover()`` so the Kindle home screen
shows it as the issue's thumbnail and Kindle's reader renders it as the
opened-book title page. When this image is present the epub builder skips
the typographic ``cover.xhtml`` fallback so the masthead/date/stats don't
appear twice in a row.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from .fetcher import Article

log = logging.getLogger(__name__)

COVER_W, COVER_H = 1600, 2560
COVER_ASPECT = COVER_W / COVER_H  # ~0.625
COVER_JPEG_QUALITY = 82
COVER_DOWNLOAD_TIMEOUT_S = 15
COVER_MIN_RAW_DIM = 600  # reject too-small candidates outright

# Category roster (line two under the masthead).
ROSTER_SEP = " · "
ROSTER_ELLIPSIS = " …"
ROSTER_MARGIN = 80  # px of breathing room on each side before truncating

# Font candidates: list of (path, ttc-face-index) tuples, tried in order.
# macOS first (dev), then Raspberry Pi OS DejaVu (production).
_SERIF_BOLD = [
    ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0),
]
_SANS_BOLD = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),  # Helvetica Neue Bold
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
]
_SANS_REGULAR = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
]


def render_cover(
    articles: list[Article],
    date: dt.date,
    count: int,
    categories: list[str] | None = None,
) -> bytes | None:
    """Render a JPEG cover image for the issue. Returns ``None`` on failure.

    Parameters
    ----------
    articles : list[Article]
        Day's articles. Each article's ``images[0]``, if present, is treated
        as the hero candidate.
    date : datetime.date
        Issue date — drawn on the cover.
    count : int
        Article count for the issue's stats line. Caller decides whether to
        pass total articles, total feeds, or something else.
    categories : list[str], optional
        Categories present in this Part, in display order. Rendered as the
        roster line under the masthead (e.g. "PHOTOGRAPHY · TECH"), truncated
        with an ellipsis if it overruns the cover width. ``None`` or empty
        draws no roster line.

    Returns
    -------
    bytes | None
        JPEG bytes ready for ``book.set_cover()``. ``None`` if no usable
        candidate image is found, no fonts are available, or compositing
        fails for any other reason.
    """
    # The cover is decorative: no failure here may cancel the day's delivery.
    try:
        return _render_cover(articles, date, count, categories)
    except Exception:
        log.exception("cover: rendering failed; using the typographic cover")
        return None


def _render_cover(
    articles: list[Article],
    date: dt.date,
    count: int,
    categories: list[str] | None,
) -> bytes | None:
    """Body of ``render_cover``; may raise."""
    masthead_font = _load_font(_SERIF_BOLD, size=110)
    date_font = _load_font(_SANS_BOLD, size=72)
    issue_font = _load_font(_SANS_BOLD, size=130)
    sub_font = _load_font(_SANS_REGULAR, size=54)
    if None in (masthead_font, date_font, issue_font, sub_font):
        log.warning("cover: no usable fonts on this system; skipping")
        return None

    hero_url = _pick_hero_url(articles)
    if not hero_url:
        log.info("cover: no hero candidate; skipping")
        return None

    try:
        with httpx.Client(
            timeout=COVER_DOWNLOAD_TIMEOUT_S, follow_redirects=True
        ) as client:
            resp = client.get(hero_url)
            resp.raise_for_status()
        im = Image.open(io.BytesIO(resp.content))
        im.load()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        log.warning("cover: failed to fetch %s: %s", hero_url, exc)
        return None

    if im.width < COVER_MIN_RAW_DIM or im.height < COVER_MIN_RAW_DIM:
        log.info("cover: hero %s too small (%dx%d)", hero_url, im.width, im.height)
        return None

    composed = _compose(
        im,
        date,
        count,
        categories or [],
        masthead_font,
        date_font,
        issue_font,
        sub_font,
    )
    buf = io.BytesIO()
    composed.save(buf, format="JPEG", quality=COVER_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------- internals


_MAX_HERO_ASPECT = 2.0  # reject super-wide images (e.g. comic strips)


def _pick_hero_url(articles: list[Article]) -> str | None:
    """Pick the hero whose original image gives the largest post-crop area.

    Post-crop = the largest centered rectangle with the cover's aspect ratio
    that fits inside the original image. This naturally rewards portrait or
    near-portrait sources (which fill the cover) and naturally penalizes
    extreme-landscape ones (which lose most of their pixels to the crop).

    Images wider than ``_MAX_HERO_ASPECT`` are rejected outright: panoramas,
    comic strips, and wide screenshots make miserable portrait covers even
    after cropping.
    """
    best: tuple[int, str] | None = None
    for art in articles:
        if not art.images:
            continue
        hero = art.images[0]
        if not hero.source_url or not hero.orig_width or not hero.orig_height:
            continue
        if hero.orig_width / hero.orig_height > _MAX_HERO_ASPECT:
            continue
        cw, ch = _crop_dims(hero.orig_width, hero.orig_height)
        score = cw * ch
        if best is None or score > best[0]:
            best = (score, hero.source_url)
    return best[1] if best else None


def _crop_dims(w: int, h: int) -> tuple[int, int]:
    """Largest centered rectangle with cover aspect that fits inside (w, h)."""
    if w / h > COVER_ASPECT:
        return int(h * COVER_ASPECT), h
    return w, int(w / COVER_ASPECT)


def _compose(
    im: Image.Image,
    date: dt.date,
    count: int,
    categories: list[str],
    masthead_font: ImageFont.FreeTypeFont,
    date_font: ImageFont.FreeTypeFont,
    issue_font: ImageFont.FreeTypeFont,
    sub_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    """Crop to portrait, greyscale, draw masthead + roster + date over a band."""
    cw, ch = _crop_dims(im.width, im.height)
    left = (im.width - cw) // 2
    top = (im.height - ch) // 2
    im = im.crop((left, top, left + cw, top + ch))
    im = im.resize((COVER_W, COVER_H), Image.Resampling.LANCZOS)
    im = im.convert("L")

    # Soft fade-to-black across the lower third so text reads on bright photos.
    band_h = 800
    overlay = Image.new("L", (COVER_W, band_h), color=0)
    mask = Image.linear_gradient("L").resize((COVER_W, band_h))
    im.paste(overlay, (0, COVER_H - band_h), mask)

    draw = ImageDraw.Draw(im)
    _shadow_text(
        draw,
        (COVER_W // 2, 200),
        "KINDLE  DAILY",
        font=masthead_font,
        anchor="mm",
    )

    max_roster_width = COVER_W - 2 * ROSTER_MARGIN
    roster = _roster_text(
        categories,
        fits=lambda text: draw.textlength(text, font=date_font) <= max_roster_width,
    )
    if roster:
        _shadow_text(
            draw,
            (COVER_W // 2, 320),
            roster,
            font=date_font,
            anchor="mm",
        )
        draw.line(
            [(COVER_W // 2 - 250, 390), (COVER_W // 2 + 250, 390)],
            fill=255,
            width=3,
        )

    draw.text(
        (COVER_W // 2, COVER_H - 380),
        date.strftime("%B %-d, %Y"),
        font=issue_font,
        fill=255,
        anchor="mm",
    )
    draw.text(
        (COVER_W // 2, COVER_H - 220),
        f"{count} ARTICLES",
        font=sub_font,
        fill=200,
        anchor="mm",
    )
    return im


def _roster_text(categories: list[str], fits: Callable[[str], bool]) -> str:
    """Build the uppercased category roster line, ellipsizing on overflow.

    Parameters
    ----------
    categories : list[str]
        Categories present in the Part, in display order.
    fits : Callable[[str], bool]
        Returns whether a candidate string fits the available cover width.

    Returns
    -------
    str
        The full ``" · "``-joined uppercased roster when it fits. Otherwise
        the longest leading run of categories that fits, suffixed with an
        ellipsis. Empty string when ``categories`` is empty. (There is no
        weekday fallback — the date is drawn separately lower on the cover.)
    """
    if not categories:
        return ""
    upper = [c.upper() for c in categories]
    full = ROSTER_SEP.join(upper)
    if fits(full):
        return full
    for keep in range(len(upper) - 1, 0, -1):
        candidate = ROSTER_SEP.join(upper[:keep]) + ROSTER_ELLIPSIS
        if fits(candidate):
            return candidate
    # Even the first category alone overflows — truncate it character-wise.
    return _char_truncate(upper[0], fits)


def _char_truncate(text: str, fits: Callable[[str], bool]) -> str:
    """Trim ``text`` character-by-character until ``text + "…"`` fits."""
    for end in range(len(text), 0, -1):
        candidate = text[:end].rstrip() + "…"
        if fits(candidate):
            return candidate
    return "…"


def _shadow_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    anchor: str,
) -> None:
    """Draw white text with a 4-corner black shadow so it reads on any photo."""
    x, y = xy
    for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=0, anchor=anchor)
    draw.text(xy, text, font=font, fill=255, anchor=anchor)


def _load_font(
    candidates: list[tuple[str, int]], size: int
) -> ImageFont.FreeTypeFont | None:
    """Try each (path, ttc-index) until one loads. Return ``None`` if none do."""
    for path, idx in candidates:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size=size, index=idx)
        except (OSError, ValueError) as exc:
            log.debug("font %s[%d] failed to load: %s", path, idx, exc)
    return None
