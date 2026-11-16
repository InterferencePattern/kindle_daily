"""End-to-end orchestration test with all I/O mocked."""

from __future__ import annotations

import dataclasses
import logging
import smtplib
import time

from kindle_daily import main as main_mod
from kindle_daily.fetcher import Article
from kindle_daily.freshrss import FeedItem


def _feed_item(i: int, category: str = "News") -> FeedItem:
    return FeedItem(
        item_id=f"tag:item/{i:04d}",
        title=f"Article {i}",
        url=f"https://example.test/{i}",
        feed_title="Example",
        category=category,
        summary_html=f"<p>summary {i}</p>",
        published_ms=0,
        author="",
    )


def _article(item: FeedItem) -> Article:
    return Article(
        item_id=item.item_id,
        title=item.title,
        url=item.url,
        feed_title=item.feed_title,
        category=item.category,
        body_html=f"<p>body for {item.item_id}</p>",
        author="",
        word_count=2,
    )


def _install_fakes(monkeypatch, items, sent, marked):
    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return items

        def list_recent(self, limit):
            return items

        def mark_read(self, ids):
            marked.append(list(ids))

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)
    monkeypatch.setattr(
        main_mod.fetcher,
        "fetch_all",
        lambda its: [_article(it) for it in its],
    )
    monkeypatch.setattr(
        main_mod.mailer,
        "send",
        lambda cfg, path, subject: sent.append((path, subject)),
    )


def test_dry_run_builds_one_combined_epub_no_send_no_mark(monkeypatch, config):
    sent: list = []
    marked: list = []
    items = [
        _feed_item(0, category="Tech"),
        _feed_item(1, category="Tech"),
        _feed_item(2, category="Politics"),
    ]
    _install_fakes(monkeypatch, items, sent, marked)

    rc = main_mod.run(config, dry_run=True)

    assert rc == 0
    assert sent == []
    assert marked == []
    # Exactly one combined EPUB, with no category slug in the filename.
    epubs = sorted(p.name for p in config.out_dir.glob("kindle_daily_*.epub"))
    today = main_mod.dt.date.today().isoformat()
    assert epubs == [f"kindle_daily_{today}.epub"]


def test_full_run_sends_one_combined_issue(monkeypatch, config):
    sent: list = []
    marked: list = []
    items = [
        _feed_item(0, category="Tech"),
        _feed_item(1, category="Politics"),
        _feed_item(2, category="Tech"),
    ]
    _install_fakes(monkeypatch, items, sent, marked)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    today = main_mod.dt.date.today().isoformat()
    # One combined issue, no category or Part in the subject.
    assert [subject for _, subject in sent] == [f"Kindle Daily — {today}"]
    # A single mark-read batch covering every id, ordered by the day's
    # category order (Tech has 2 → leads; Politics 1 → trails).
    assert marked == [["tag:item/0000", "tag:item/0002", "tag:item/0001"]]


def test_cover_failure_still_sends_the_issue_without_a_cover(monkeypatch, config):
    sent: list = []
    marked: list = []
    _install_fakes(monkeypatch, [_feed_item(0)], sent, marked)

    def boom(*args, **kwargs):
        raise RuntimeError("compositing exploded")

    monkeypatch.setattr(main_mod.cover, "_render_cover", boom)
    covers: list = []
    real_build = main_mod.epub_builder.build

    def spy_build(arts, *, cover_jpeg, **kw):
        covers.append(cover_jpeg)
        return real_build(arts, cover_jpeg=cover_jpeg, **kw)

    monkeypatch.setattr(main_mod.epub_builder, "build", spy_build)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    assert len(sent) == 1
    assert covers and all(c is None for c in covers)
    assert marked == [["tag:item/0000"]]


def test_uncategorized_items_appear_as_misc_in_the_combined_issue(monkeypatch, config):
    sent: list = []
    marked: list = []
    # item 0 is uncategorized -> "Misc"; item 1 is "Tech". Counts tie at 1, so
    # the alphabetical tiebreak orders Misc before Tech. The empty category
    # mapping to "Misc" is what makes item 0 lead.
    items = [_feed_item(0, category=""), _feed_item(1, category="Tech")]
    _install_fakes(monkeypatch, items, sent, marked)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # One combined issue; the subject carries no category name.
    assert [subject for _, subject in sent] == [f"Kindle Daily — {main_mod.dt.date.today().isoformat()}"]
    # Single mark-read batch; Misc (item 0) leads Tech (item 1).
    assert marked == [["tag:item/0000", "tag:item/0001"]]


def test_send_failure_fails_the_run(monkeypatch, config):
    """A single-Part issue whose send fails leaves everything unread, rc=1."""
    sent: list = []
    marked: list = []
    items = [
        _feed_item(0, category="Politics"),
        _feed_item(1, category="Tech"),
    ]

    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return items

        def mark_read(self, ids):
            marked.append(list(ids))

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)
    monkeypatch.setattr(main_mod.fetcher, "fetch_all", lambda its: [_article(it) for it in its])

    def fake_send(cfg, path, subject):
        raise smtplib.SMTPException("simulated SMTP failure")

    monkeypatch.setattr(main_mod.mailer, "send", fake_send)

    rc = main_mod.run(config, dry_run=False)

    # The whole issue failed to send — exit 1, nothing marked read.
    assert rc == 1
    assert sent == []
    assert marked == []


def test_no_mark_read_sends_but_leaves_items_unread(monkeypatch, config):
    sent: list = []
    marked: list = []
    items = [_feed_item(0, category="Tech")]
    _install_fakes(monkeypatch, items, sent, marked)

    rc = main_mod.run(config, dry_run=False, mark_read=False)

    assert rc == 0
    assert len(sent) == 1
    assert marked == []


def test_excluded_categories_are_dropped_before_fetch(monkeypatch, config):
    """Items in EXCLUDED_CATEGORIES never get fetched, sent, or marked read."""
    sent: list = []
    marked: list = []
    fetched: list = []
    items = [
        _feed_item(0, category="Tech"),
        _feed_item(1, category="Comics"),
        _feed_item(2, category="Comics"),
    ]

    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return items

        def mark_read(self, ids):
            marked.append(list(ids))

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)

    def capture_fetch(its):
        fetched.extend(its)
        return [_article(it) for it in its]

    monkeypatch.setattr(main_mod.fetcher, "fetch_all", capture_fetch)
    monkeypatch.setattr(
        main_mod.mailer,
        "send",
        lambda cfg, path, subject: sent.append((path, subject)),
    )

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # Only the Tech item should have reached the fetcher.
    assert [it.item_id for it in fetched] == ["tag:item/0000"]
    # Exactly one combined issue; Comics never contributed any item.
    assert len(sent) == 1
    today = main_mod.dt.date.today().isoformat()
    assert sent[0][1] == f"Kindle Daily — {today}"
    # Mark-read only covers the surviving Tech id.
    assert marked == [["tag:item/0000"]]


def test_run_with_only_excluded_items_is_a_clean_noop(monkeypatch, config):
    """If every fetched item is in an excluded category, treat as 'nothing to do'."""
    sent: list = []
    marked: list = []
    fetched: list = []
    items = [_feed_item(0, category="Comics"), _feed_item(1, category="Comics")]

    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return items

        def mark_read(self, ids):
            marked.append(list(ids))

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)
    monkeypatch.setattr(
        main_mod.fetcher,
        "fetch_all",
        lambda its: fetched.append(its) or [_article(it) for it in its],
    )
    monkeypatch.setattr(
        main_mod.mailer,
        "send",
        lambda cfg, path, subject: sent.append((path, subject)),
    )

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    assert sent == []
    assert marked == []
    assert fetched == []  # nothing reached the fetcher


def test_no_unread_is_a_clean_noop(monkeypatch, config):
    fetched: list = []

    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return []

        def mark_read(self, ids):
            raise AssertionError("should not be called")

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)
    monkeypatch.setattr(
        main_mod.fetcher,
        "fetch_all",
        lambda its: fetched.append(its) or [],
    )
    monkeypatch.setattr(
        main_mod.mailer,
        "send",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no send")),
    )

    assert main_mod.run(config, dry_run=False) == 0
    assert fetched == []


def test_oversized_article_is_skipped_before_epub_build(monkeypatch, config, caplog):
    """Articles whose body exceeds the 14 MB cap don't reach the builder or mark-read."""
    sent: list = []
    marked: list = []
    items = [_feed_item(0, category="Tech"), _feed_item(1, category="Tech")]

    huge_body = "a" * (15 * 1024 * 1024)  # 15 MB, over the 14 MB cap

    def fetch_oversized(its):
        out: list[Article] = []
        for it in its:
            base = _article(it)
            if it.item_id.endswith("0001"):
                out.append(dataclasses.replace(base, body_html=huge_body))
            else:
                out.append(base)
        return out

    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return items

        def mark_read(self, ids):
            marked.append(list(ids))

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)
    monkeypatch.setattr(main_mod.fetcher, "fetch_all", fetch_oversized)

    built: list = []

    def fake_build(articles, *, date, out_dir, cover_jpeg, part=None):
        built.append([a.item_id for a in articles])
        path = out_dir / "kindle_daily_test.epub"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    monkeypatch.setattr(main_mod.epub_builder, "build", fake_build)
    monkeypatch.setattr(main_mod.cover, "render_cover", lambda articles, *, date, count, categories: b"")
    monkeypatch.setattr(
        main_mod.mailer,
        "send",
        lambda cfg, path, subject: sent.append((path, subject)),
    )

    with caplog.at_level(logging.WARNING, logger="kindle_daily"):
        rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # The oversized article never reached the builder (chunker measure +
    # real build both invoke the build closure, so we assert content not
    # call count).
    assert built  # at least one build happened
    for call in built:
        assert call == ["tag:item/0000"]
    # And only the small article was marked read.
    assert marked == [["tag:item/0000"]]
    # The oversized URL appears in the skip warning.
    matching = [r for r in caplog.records if "skipping oversized article" in r.getMessage()]
    assert len(matching) == 1
    assert matching[0].levelno == logging.WARNING
    assert "https://example.test/1" in matching[0].getMessage()


def _feed_item_published(i: int, *, published_ms: int, category: str = "Tech") -> FeedItem:
    return dataclasses.replace(_feed_item(i, category=category), published_ms=published_ms)


def test_published_date_filter_drops_items_older_than_max_age(monkeypatch, config):
    """An item whose published date is older than max_age_days never reaches fetch."""
    now_ms = int(time.time()) * 1000
    fresh = _feed_item_published(0, published_ms=now_ms - 1 * 86400 * 1000)  # 1 day old
    stale = _feed_item_published(1, published_ms=now_ms - 30 * 86400 * 1000)  # 30 days old

    sent: list = []
    marked: list = []
    _install_fakes(monkeypatch, [fresh, stale], sent, marked)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # Only the fresh item should have been marked read.
    assert marked == [["tag:item/0000"]]


def test_published_date_filter_keeps_items_within_window(monkeypatch, config):
    """An item published within the window is kept."""
    now_ms = int(time.time()) * 1000
    items = [_feed_item_published(i, published_ms=now_ms - i * 86400 * 1000) for i in range(3)]

    sent: list = []
    marked: list = []
    _install_fakes(monkeypatch, items, sent, marked)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    assert sorted(marked[0]) == sorted(it.item_id for it in items)


def test_published_date_filter_keeps_items_with_no_timestamp_and_warns(monkeypatch, config, caplog):
    """published_ms == 0 means the feed didn't supply a date — keep, but warn."""
    items = [
        _feed_item_published(0, published_ms=0),
        _feed_item_published(1, published_ms=0),
    ]

    sent: list = []
    marked: list = []
    _install_fakes(monkeypatch, items, sent, marked)

    with caplog.at_level(logging.WARNING, logger="kindle_daily"):
        rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # Both items kept and marked read.
    assert sorted(marked[0]) == sorted(it.item_id for it in items)
    # A single warning summarising the count was emitted.
    matching = [r for r in caplog.records if "no published timestamp" in r.getMessage()]
    assert len(matching) == 1
    assert matching[0].levelno == logging.WARNING
    assert "2" in matching[0].getMessage()


# ---------------------------------------------------------------- chunking


def _install_chunking_fakes(
    monkeypatch,
    items,
    sent,
    marked,
    built,
    *,
    size_for,
    send_raises_for_part=None,
):
    """Like _install_fakes, but with a controllable epub builder for chunking.

    The patched ``epub_builder.build`` writes a placeholder file whose size
    is dictated by ``size_for(articles)``, and records every call as
    ``(part, [item_id, ...])``. ``mailer.send`` records every successful
    send as ``(path, subject)``; if ``send_raises_for_part`` is set, the
    send for that part number raises ``SMTPException`` on every attempt.
    """

    class FakeClient:
        def __init__(self, cfg):
            pass

        def list_unread(self, limit):
            return items

        def mark_read(self, ids):
            marked.append(list(ids))

    monkeypatch.setattr(main_mod, "FreshRSSClient", FakeClient)
    monkeypatch.setattr(main_mod.fetcher, "fetch_all", lambda its: [_article(it) for it in its])
    monkeypatch.setattr(main_mod.cover, "render_cover", lambda articles, *, date, count, categories: b"")

    def fake_build(articles, *, date, out_dir, cover_jpeg, part=None):
        built.append((part, [a.item_id for a in articles]))
        size = size_for(articles)
        part_suffix = f"_part{part[0]}of{part[1]}" if part else ""
        path = out_dir / f"kindle_daily_{date.isoformat()}{part_suffix}.epub"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * size)
        return path

    monkeypatch.setattr(main_mod.epub_builder, "build", fake_build)

    def fake_send(cfg, path, subject):
        if send_raises_for_part is not None and f"Part {send_raises_for_part}/" in subject:
            raise smtplib.SMTPException("forced failure")
        sent.append((path, subject))

    monkeypatch.setattr(main_mod.mailer, "send", fake_send)
    # Don't actually sleep between mailer retries.
    monkeypatch.setattr(main_mod.mailer.time, "sleep", lambda _s: None)


def test_single_chunk_uses_no_part_suffix(monkeypatch, config):
    """A category that fits in one EPUB keeps today's subject/filename format."""
    sent: list = []
    marked: list = []
    built: list = []
    items = [_feed_item(i, category="Tech") for i in range(3)]

    cap = config.max_epub_bytes
    _install_chunking_fakes(
        monkeypatch,
        items,
        sent,
        marked,
        built,
        # Always small enough; one chunk only.
        size_for=lambda arts: cap // 2,
    )

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # Exactly one send, no Part suffix anywhere.
    assert len(sent) == 1
    path, subject = sent[0]
    today = main_mod.dt.date.today().isoformat()
    assert subject == f"Kindle Daily — {today}"
    assert "_part" not in path.name
    # All ids marked read in one batch.
    assert marked == [[it.item_id for it in items]]
    # No Part labels passed to the real (delivery) build.
    delivered = [b for b in built if b[0] is not None]
    assert delivered == []


def test_multi_chunk_delivers_n_issues_with_part_suffixes(monkeypatch, config):
    """When the EPUB exceeds the cap, the issue is split into N Part-N chunks."""
    sent: list = []
    marked: list = []
    built: list = []
    items = [_feed_item(i, category="Tech") for i in range(4)]
    cap = config.max_epub_bytes

    def size_for(arts):
        # Whole 4-article list is 2× cap (forces split into 2 chunks of 2);
        # each 2-article slice fits.
        return 2 * cap if len(arts) == 4 else cap // 2

    _install_chunking_fakes(monkeypatch, items, sent, marked, built, size_for=size_for)

    rc = main_mod.run(config, dry_run=False)

    today = main_mod.dt.date.today().isoformat()
    assert rc == 0
    # Two sends, both with Part suffixes, in order.
    subjects = [s for _, s in sent]
    assert subjects == [
        f"Kindle Daily Part 1/2 — {today}",
        f"Kindle Daily Part 2/2 — {today}",
    ]
    # Each Part's mark-read carries only its own ids; oldest first.
    assert marked == [
        ["tag:item/0000", "tag:item/0001"],
        ["tag:item/0002", "tag:item/0003"],
    ]
    # Filenames carry _part1of2 / _part2of2 suffixes.
    paths = [p.name for p, _ in sent]
    assert "_part1of2" in paths[0]
    assert "_part2of2" in paths[1]


def test_failed_part_does_not_mark_read_but_others_proceed(monkeypatch, config):
    """If one Part's send fails on both attempts, its ids stay unread; other
    Parts still deliver and mark read."""
    sent: list = []
    marked: list = []
    built: list = []
    items = [_feed_item(i, category="Tech") for i in range(4)]
    cap = config.max_epub_bytes

    def size_for(arts):
        return 2 * cap if len(arts) == 4 else cap // 2

    _install_chunking_fakes(
        monkeypatch,
        items,
        sent,
        marked,
        built,
        size_for=size_for,
        send_raises_for_part=1,  # Part 1 always fails
    )

    rc = main_mod.run(config, dry_run=False)

    today = main_mod.dt.date.today().isoformat()
    # A failed Part bumps the run exit code to 1 but doesn't block the rest.
    assert rc == 1
    # Only Part 2 made it through.
    assert [s for _, s in sent] == [f"Kindle Daily Part 2/2 — {today}"]
    # Only Part 2's ids were marked read.
    assert marked == [["tag:item/0002", "tag:item/0003"]]


def test_multi_part_splits_on_category_boundaries(monkeypatch, config):
    """When categories don't fit together, Parts break on category boundaries."""
    sent: list = []
    marked: list = []
    built: list = []
    items = [
        _feed_item(0, category="Tech"),
        _feed_item(1, category="Tech"),
        _feed_item(2, category="Tech"),
        _feed_item(3, category="Politics"),
        _feed_item(4, category="Politics"),
    ]
    cap = config.max_epub_bytes

    # Each article weighs 0.3×cap: Tech (3) = 0.9×cap fits, Politics (2) =
    # 0.6×cap fits, but Tech+Politics = 1.5×cap overflows -> boundary split.
    def size_for(arts):
        return int(len(arts) * cap * 0.3)

    _install_chunking_fakes(monkeypatch, items, sent, marked, built, size_for=size_for)

    rc = main_mod.run(config, dry_run=False)

    today = main_mod.dt.date.today().isoformat()
    assert rc == 0
    # Two Parts, split on the Tech/Politics boundary (Tech leads on count).
    assert [s for _, s in sent] == [
        f"Kindle Daily Part 1/2 — {today}",
        f"Kindle Daily Part 2/2 — {today}",
    ]
    # No category straddles a Part: Part 1 is all Tech, Part 2 all Politics.
    assert marked == [
        ["tag:item/0000", "tag:item/0001", "tag:item/0002"],
        ["tag:item/0003", "tag:item/0004"],
    ]


def test_category_order_is_count_desc_with_alpha_tiebreak(monkeypatch, config):
    """Sections order by count desc; equal counts break alphabetically."""
    sent: list = []
    marked: list = []
    # Tech 3 (leads on count); Art 2 and Books 2 tie -> Art before Books.
    items = [
        _feed_item(0, category="Tech"),
        _feed_item(1, category="Tech"),
        _feed_item(2, category="Tech"),
        _feed_item(3, category="Books"),
        _feed_item(4, category="Art"),
        _feed_item(5, category="Books"),
        _feed_item(6, category="Art"),
    ]
    _install_fakes(monkeypatch, items, sent, marked)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 0
    # One combined Part: Tech (0,1,2), then Art (4,6), then Books (3,5).
    assert marked == [
        [
            "tag:item/0000",
            "tag:item/0001",
            "tag:item/0002",
            "tag:item/0004",
            "tag:item/0006",
            "tag:item/0003",
            "tag:item/0005",
        ]
    ]


def test_build_failure_ships_nothing(monkeypatch, config):
    """If the EPUB build raises, the run sends nothing and marks nothing."""
    sent: list = []
    marked: list = []
    items = [_feed_item(0, category="Tech"), _feed_item(1, category="Politics")]
    _install_fakes(monkeypatch, items, sent, marked)

    def boom(*args, **kwargs):
        raise RuntimeError("build exploded")

    monkeypatch.setattr(main_mod.epub_builder, "build", boom)

    rc = main_mod.run(config, dry_run=False)

    assert rc == 1
    assert sent == []
    assert marked == []
