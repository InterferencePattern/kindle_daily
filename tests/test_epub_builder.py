"""EPUB builder integration tests.

Builds a real EPUB and re-opens it with ebooklib to assert the structure is
valid: nav has the right shape, every article is reachable from the contents
page, and the stylesheet is referenced.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import zipfile

from ebooklib import epub

from kindle_daily import epub_builder
from kindle_daily.images import ImageAsset


def test_build_produces_valid_epub_with_full_navigation(tmp_path, articles):
    out = epub_builder.build(articles, date=dt.date(2026, 5, 5), out_dir=tmp_path)

    assert out.exists()
    assert out.suffix == ".epub"
    assert zipfile.is_zipfile(out)

    book = epub.read_epub(str(out))
    assert "Kindle Daily" in book.title

    # ---- TOC structure: 2 sections, each with its articles
    toc = book.toc
    assert len(toc) == 2
    section_names = [entry[0].title for entry in toc]
    assert section_names == ["Politics", "Tech"]
    politics_articles = toc[0][1]
    tech_articles = toc[1][1]
    assert [a.title for a in politics_articles] == ["Why the polls moved"]
    assert [a.title for a in tech_articles] == ["The aggregation thesis"]

    # ---- nav.xhtml is in the manifest (so Kindle's TOC button works) but
    # NOT in the spine (so Apple Books doesn't render it as a duplicate page).
    items_by_name_check = {item.file_name for item in book.get_items()}
    assert "nav.xhtml" in items_by_name_check
    spine_ids = [s[0] for s in book.spine]
    assert "nav" not in spine_ids

    # ---- contents page links to every article
    items_by_name = {item.file_name: item for item in book.get_items()}
    contents = items_by_name["contents.xhtml"]
    contents_html = contents.get_content().decode()
    for art in articles:
        # the contents-page entry uses the same href as the article filename
        href = epub_builder._article_href(art)
        assert href in contents_html, f"contents page is missing link to {art.title}"
        # and the article file itself was emitted
        assert href in items_by_name, f"article file {href} missing from EPUB"

    # ---- stylesheet is present and linked.
    # ebooklib drops <head> contents when re-reading, so verify against the
    # raw XHTML inside the zip.
    assert "styles.css" in items_by_name
    with zipfile.ZipFile(out) as zf:
        cover_raw = zf.read("EPUB/cover.xhtml").decode()
    assert 'href="styles.css"' in cover_raw
    assert "<title>Cover</title>" in cover_raw


def test_build_groups_by_section_in_first_seen_order(tmp_path, articles):
    # reverse the input to confirm section order follows first-seen, not alpha
    reordered = list(reversed(articles))
    out = epub_builder.build(reordered, date=dt.date(2026, 5, 5), out_dir=tmp_path)
    book = epub.read_epub(str(out))
    section_names = [entry[0].title for entry in book.toc]
    assert section_names == ["Tech", "Politics"]


def test_build_sets_combined_title_and_filename(tmp_path, articles):
    out = epub_builder.build(articles, date=dt.date(2026, 5, 7), out_dir=tmp_path)
    assert out.name == "kindle_daily_2026-05-07.epub"
    book = epub.read_epub(str(out))
    assert book.title == "Kindle Daily — Thursday, May 7, 2026"


def test_build_with_part_adds_part_suffix_to_title_and_filename(tmp_path, articles):
    out = epub_builder.build(
        articles,
        date=dt.date(2026, 5, 7),
        out_dir=tmp_path,
        part=(2, 3),
    )
    assert out.name == "kindle_daily_2026-05-07_part2of3.epub"
    book = epub.read_epub(str(out))
    assert book.title == "Kindle Daily Part 2 — Thursday, May 7, 2026"


def test_build_manifest_ids_are_valid_xml_names(tmp_path, articles):
    # "img-images/abc.jpg" style ids fail epubcheck (RSC-005).
    asset = ImageAsset(
        local_name="images/0123456789abcdef.jpg",
        media_type="image/jpeg",
        data=b"\xff\xd8\xff",
    )
    articles[0] = dataclasses.replace(
        articles[0],
        body_html=articles[0].body_html + f'<img src="{asset.local_name}" alt=""/>',
        images=(asset,),
    )
    out = epub_builder.build(articles, date=dt.date(2026, 5, 5), out_dir=tmp_path)

    with zipfile.ZipFile(out) as zf:
        opf = zf.read("EPUB/content.opf").decode()
        assert "EPUB/images/0123456789abcdef.jpg" in zf.namelist()
    ids = re.findall(r'<item [^>]*\bid="([^"]*)"', opf)
    assert "img-0123456789abcdef" in ids
    for item_id in ids:
        assert re.fullmatch(r"[A-Za-z_][\w.-]*", item_id), item_id


def test_section_filenames_dont_collide_when_slugs_match(tmp_path, articles):
    # "C++" and "C" both slug to "c"; each still needs its own opener page.
    clashing = [
        dataclasses.replace(articles[0], category="C++"),
        dataclasses.replace(articles[1], category="C"),
    ]
    out = epub_builder.build(clashing, date=dt.date(2026, 5, 5), out_dir=tmp_path)
    with zipfile.ZipFile(out) as zf:
        openers = [n for n in zf.namelist() if "/section-" in n]
    assert len(openers) == 2 and len(set(openers)) == 2
