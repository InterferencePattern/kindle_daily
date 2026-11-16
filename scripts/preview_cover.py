"""One-off: preview a magazine-style EPUB cover for today's issue.

Pulls today's articles via FreshRSS, harvests image URLs for each, downloads
candidate hero images at full resolution, picks the one with the largest
post-crop area (best signal for "real article photo, not an icon"), then
center-crops to 1600x2560, converts to greyscale, and overlays masthead +
date + article count in a darkened band at the bottom.

Writes a PNG to ``out/cover_preview_<date>.png``.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import sys
import time

import httpx
import trafilatura
from PIL import Image, ImageDraw, ImageFont

from kindle_daily.config import load_config
from kindle_daily.fetcher import _harvest_image_urls
from kindle_daily.freshrss import FreshRSSClient

COVER_W, COVER_H = 1600, 2560
COVER_ASPECT = COVER_W / COVER_H  # ~0.625

GEORGIA = "/System/Library/Fonts/Supplemental/Georgia.ttf"
GEORGIA_BOLD = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
HELVETICA = "/System/Library/Fonts/HelveticaNeue.ttc"  # index 0 = Regular

log = logging.getLogger("preview_cover")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config()
    today = dt.date.today()
    client = FreshRSSClient(config)
    since = int(time.time()) - config.max_age_hours * 3600
    items = client.list_recent(limit=config.max_articles, since_seconds=since)
    log.info("got %d items from last %dh", len(items), config.max_age_hours)
    if not items:
        log.error("no items; aborting")
        return 1

    # Harvest one candidate URL per article (the og:image / first article img),
    # which is what the cover would actually use. Skipping the full extra-image
    # harvest because we only need one good photo.
    candidates: list[str] = []
    for item in items:
        if not item.url:
            continue
        try:
            downloaded = trafilatura.fetch_url(item.url)
        except Exception:
            continue
        if not downloaded:
            continue
        urls = _harvest_image_urls(downloaded, item.url)
        if urls:
            candidates.append(urls[0])
    log.info("got %d candidate hero URLs", len(candidates))

    best_image = _pick_largest(candidates)
    if best_image is None:
        log.error("could not load any candidate image")
        return 1
    log.info("chose image: %dx%d", best_image.width, best_image.height)

    cover = _compose_cover(best_image, today, len(items))
    out_path = config.out_dir / f"cover_preview_{today.isoformat()}.png"
    cover.save(out_path, format="PNG", optimize=True)
    log.info("wrote %s (%d bytes)", out_path, out_path.stat().st_size)
    return 0


def _pick_largest(urls: list[str]) -> Image.Image | None:
    """Download every URL, return the one with the largest post-crop area.

    "Post-crop" means area after a hypothetical center-crop to the cover
    aspect — so an extreme-landscape image doesn't beat a portrait one just
    because of its raw pixel count.
    """
    best: tuple[int, Image.Image] | None = None
    with httpx.Client(timeout=15, follow_redirects=True) as net:
        for url in urls:
            try:
                resp = net.get(url)
                resp.raise_for_status()
                im = Image.open(io.BytesIO(resp.content))
                im.load()
            except Exception as exc:
                log.debug("skip %s: %s", url, exc)
                continue
            if im.width < 600 or im.height < 600:
                continue  # too small to make a believable cover
            cropped_w, cropped_h = _crop_dims(im.width, im.height)
            score = cropped_w * cropped_h
            if best is None or score > best[0]:
                best = (score, im.copy())
    return best[1] if best else None


def _crop_dims(w: int, h: int) -> tuple[int, int]:
    """Largest centered rectangle with cover aspect that fits inside (w, h)."""
    if w / h > COVER_ASPECT:
        return int(h * COVER_ASPECT), h
    return w, int(w / COVER_ASPECT)


def _compose_cover(im: Image.Image, date: dt.date, count: int) -> Image.Image:
    """Crop to portrait, greyscale, render masthead + date band over photo."""
    cw, ch = _crop_dims(im.width, im.height)
    left = (im.width - cw) // 2
    top = (im.height - ch) // 2
    im = im.crop((left, top, left + cw, top + ch))
    im = im.resize((COVER_W, COVER_H), Image.Resampling.LANCZOS)
    im = im.convert("L")  # greyscale for Kindle e-ink

    # Soft vignette / darken the lower band so text reads regardless of photo.
    band_h = 800
    overlay = Image.new("L", (COVER_W, band_h), color=0)
    grad = Image.linear_gradient("L").resize((COVER_W, band_h))
    # linear_gradient goes 0->255 top-to-bottom; we want transparent->opaque,
    # i.e. paste with mask = grad
    mask = grad
    im.paste(overlay, (0, COVER_H - band_h), mask)

    # Top masthead — small caps, light, against the photo with a subtle shadow.
    masthead_font = ImageFont.truetype(GEORGIA_BOLD, size=110)
    date_font = ImageFont.truetype(HELVETICA, size=72, index=1)  # Helvetica Neue Bold
    issue_font = ImageFont.truetype(HELVETICA, size=130, index=1)
    sub_font = ImageFont.truetype(HELVETICA, size=54, index=0)  # Regular

    draw = ImageDraw.Draw(im)
    _shadow_text(
        draw,
        (COVER_W // 2, 200),
        "KINDLE  DAILY",
        font=masthead_font,
        fill=255,
        anchor="mm",
    )
    _shadow_text(
        draw,
        (COVER_W // 2, 320),
        date.strftime("%A").upper(),
        font=date_font,
        fill=255,
        anchor="mm",
    )

    # Bottom block: big issue date + small stats line
    draw.text(
        (COVER_W // 2, COVER_H - 380),
        date.strftime("%B %-d, %Y"),
        font=issue_font,
        fill=255,
        anchor="mm",
    )
    feed_count_text = f"{count} ARTICLES"
    draw.text(
        (COVER_W // 2, COVER_H - 220),
        feed_count_text,
        font=sub_font,
        fill=200,
        anchor="mm",
    )

    # Hairline rule under the masthead, just for fun
    draw.line(
        [(COVER_W // 2 - 250, 390), (COVER_W // 2 + 250, 390)],
        fill=255,
        width=3,
    )
    return im


def _shadow_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: int,
    anchor: str,
) -> None:
    """Draw text with a soft drop-shadow so it stays legible on any photo."""
    x, y = xy
    for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=0, anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


if __name__ == "__main__":
    sys.exit(main())
