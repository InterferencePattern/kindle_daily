"""Inline images for article bodies.

Every ``<img>`` in an article body is resolved (``src``, lazy-load attributes,
``srcset``), downloaded, optimized for the Kindle (resized, greyscale, JPEG),
and rewritten to point at the local asset, preserving the author's image
placement.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from PIL import Image, UnidentifiedImageError

from .html_cleanup import drop, parse_fragment, serialize

log = logging.getLogger(__name__)

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


# Trafilatura emits an image's caption as a plain <p> sibling right after it
# (e.g. "Photo credit: ..."), not a <figcaption>. Dropping the image alone
# orphans that caption — and galleries that exceed MAX_IMAGES_PER_ARTICLE leave
# a run of orphaned, often-identical caption lines. So drop the caption too.
# Guard on length so we never eat a real body paragraph that merely follows an
# inline image; captions are short.
_CAPTION_MAX_WORDS = 30


def _drop_with_caption(img) -> None:
    """Drop an ``<img>`` plus an immediately-following short caption ``<p>``."""
    nxt = img.getnext()
    if (
        nxt is not None
        and isinstance(nxt.tag, str)
        and nxt.tag == "p"
        and len("".join(nxt.itertext()).split()) < _CAPTION_MAX_WORDS
    ):
        drop(nxt)
    drop(img)


def rewrite_images_inline(
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

    root = parse_fragment(body_html)
    if root is None:
        return body_html, ()

    assets: list[ImageAsset] = []
    seen_source: dict[str, str] = {}  # source URL -> local_name (for dedupe)

    for img in list(root.iter("img")):
        absolute = _resolve_img_src(img, base_url)
        if not absolute:
            _drop_with_caption(img)
            continue
        if len(assets) >= MAX_IMAGES_PER_ARTICLE and absolute not in seen_source:
            _drop_with_caption(img)
            continue

        local_name = seen_source.get(absolute)
        if local_name is None:
            asset = _download_and_optimize(client, absolute)
            if asset is None:
                _drop_with_caption(img)
                continue
            local_name = asset.local_name
            seen_source[absolute] = local_name
            assets.append(asset)

        # Rewrite this <img> to point at the local asset; strip noisy attrs
        # (srcset, lazy-load, sizes) so the EPUB renderer doesn't argue.
        alt = img.get("alt", "")
        img.attrib.clear()
        img.set("src", local_name)
        img.set("alt", alt)

    return serialize(root), tuple(assets)


def _resolve_img_src(img, base_url: str) -> str:
    """Return the absolute source URL for an ``<img>`` or '' if unusable.

    Tries ``src`` first, then lazy-load attributes (``data-src``,
    ``data-original``, …), then ``srcset`` (first URL). Skips ``data:``
    URLs, relative URLs that can't be resolved, and known noise paths
    (logos, tracking pixels, etc.).
    """
    candidates = [img.get(attr) for attr in ("src", *_LAZY_ATTRS, "srcset")]

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
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
    ) as exc:
        log.info("image decode failed for %s: %s", url, exc)
        return None

    # Stable filename keyed off the source URL — dedupes when the same image
    # appears in multiple articles (rare, but cheap).
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return ImageAsset(
        local_name=f"images/{digest}.jpg",
        media_type="image/jpeg",
        data=data,
        source_url=url,
        orig_width=orig_w,
        orig_height=orig_h,
    )
