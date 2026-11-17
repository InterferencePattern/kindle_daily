"""Tests for inline image download and rewriting."""

from __future__ import annotations

import kindle_daily.images as images_mod
from kindle_daily.images import ImageAsset, rewrite_images_inline


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

    def fake_download(client, url):
        return _fake_asset(url) if "good" in url else None

    monkeypatch.setattr(images_mod, "_download_and_optimize", fake_download)

    body = (
        "<p>before</p>"
        '<img src="https://x.test/good-1.jpg" alt="ok"/>'
        '<img src="https://x.test/bad.jpg" alt="broken"/>'
        '<img src="https://x.test/good-2.jpg" alt="ok2"/>'
        "<p>after</p>"
    )
    out, assets = rewrite_images_inline(body, "https://x.test/article", client=None)
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

    monkeypatch.setattr(
        images_mod, "_download_and_optimize", lambda _c, u: _fake_asset(u)
    )

    imgs = "".join(
        f'<img src="https://x.test/img-{i}.jpg"/>'
        for i in range(images_mod.MAX_IMAGES_PER_ARTICLE + 3)
    )
    out, assets = rewrite_images_inline(imgs, "https://x.test/", client=None)
    assert len(assets) == images_mod.MAX_IMAGES_PER_ARTICLE
    # Over-cap images are dropped from the HTML so Kindle doesn't show
    # broken-image placeholders for them.
    assert out.count("<img") == images_mod.MAX_IMAGES_PER_ARTICLE


def test_rewrite_images_inline_dedupes_repeated_urls(monkeypatch):

    call_count = {"n": 0}

    def fake_download(client, url):
        call_count["n"] += 1
        return _fake_asset(url)

    monkeypatch.setattr(images_mod, "_download_and_optimize", fake_download)

    body = '<img src="https://x.test/same.jpg"/>' '<img src="https://x.test/same.jpg"/>'
    out, assets = rewrite_images_inline(body, "https://x.test/", client=None)
    assert len(assets) == 1
    assert call_count["n"] == 1
    # Both <img> tags remain, pointing at the same local file
    assert out.count(assets[0].local_name) == 2


def test_rewrite_images_inline_resolves_relative_and_lazy(monkeypatch):

    captured: list[str] = []

    def fake_download(client, url):
        captured.append(url)
        return _fake_asset(url)

    monkeypatch.setattr(images_mod, "_download_and_optimize", fake_download)

    body = (
        '<img src="data:image/gif;base64,xxx" data-src="/lazy.jpg"/>'
        '<img src="../up.jpg"/>'
    )
    _out, assets = rewrite_images_inline(
        body, "https://x.test/section/article", client=None
    )
    # data: URL ignored; lazy-load attribute picked up
    assert captured[0] == "https://x.test/lazy.jpg"
    # relative URL resolved against base
    assert captured[1] == "https://x.test/up.jpg"
    assert len(assets) == 2


def test_rewrite_images_inline_skips_noise_substrings(monkeypatch):

    monkeypatch.setattr(
        images_mod, "_download_and_optimize", lambda _c, u: _fake_asset(u)
    )

    body = (
        '<img src="https://x.test/logo.png"/>' '<img src="https://x.test/photo.jpg"/>'
    )
    out, assets = rewrite_images_inline(body, "https://x.test/", client=None)
    assert len(assets) == 1
    assert assets[0].source_url.endswith("photo.jpg")
    assert "logo.png" not in out


def test_rewrite_images_inline_drops_orphaned_caption_with_image(monkeypatch):

    monkeypatch.setattr(
        images_mod, "_download_and_optimize", lambda _c, u: _fake_asset(u)
    )

    # A gallery exceeding the cap, each image followed by an identical caption <p>.
    # Dropped images must take their caption with them — no orphaned repetition.
    n = images_mod.MAX_IMAGES_PER_ARTICLE + 4
    body = "".join(
        f'<img src="https://x.test/g-{i}.jpg"/><p>Photo credit: Someone</p>'
        for i in range(n)
    )
    out, assets = rewrite_images_inline(body, "https://x.test/", client=None)
    assert len(assets) == images_mod.MAX_IMAGES_PER_ARTICLE
    # One caption survives per kept image; the over-cap captions are gone.
    assert out.count("Photo credit: Someone") == images_mod.MAX_IMAGES_PER_ARTICLE
    # A long paragraph after a dropped image is NOT mistaken for a caption.
    long_p = "<p>" + " ".join(["word"] * 40) + "</p>"
    body2 = "".join(f'<img src="https://x.test/h-{i}.jpg"/>' for i in range(n)) + long_p
    out2, _ = rewrite_images_inline(body2, "https://x.test/", client=None)
    assert "word word" in out2


def test_download_and_optimize_skips_decompression_bombs(monkeypatch):
    """A bomb drops just the image, not the whole article."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")

    class _Resp:
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    class _Client:
        def get(self, _url):
            return _Resp()

    # 100 px > 2 * MAX_IMAGE_PIXELS, so PIL raises DecompressionBombError.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    assert images_mod._download_and_optimize(_Client(), "https://x/bomb.png") is None
