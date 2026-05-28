"""Pipeline entry point for kindle_daily.

Run with::

    python -m kindle_daily.main           # full run
    python -m kindle_daily.main --dry-run # build EPUB, don't send or mark read
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import smtplib
import sys
import tempfile
from pathlib import Path

from . import chunker, cover, epub_builder, fetcher, logging_setup, mailer, selector
from .config import Config, load_config
from .fetcher import Article
from .freshrss import FreshRSSClient

log = logging.getLogger("kindle_daily")

# Per-part caps are enforced after grouping, so the FreshRSS pull needs to
# be large enough to fill all categories. 1000 is comfortably above any
# realistic 24h backlog and keeps the GReader response a single page.
FETCH_POOL_LIMIT = 1000

# Pre-flight per-article size cap: any single article whose body + images
# exceeds this can't fit in a 15 MB EPUB after scaffolding, so chunking
# would never converge. Skipped articles stay unread upstream.
MAX_ARTICLE_BYTES = 14 * 1024 * 1024


def cli(argv: list[str] | None = None) -> int:
    """Argparse-driven entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="kindle_daily")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the EPUB to ./out/ but do not send or mark items as read.",
    )
    parser.add_argument(
        "--no-mark-read",
        action="store_true",
        help="Send the email but leave items unread in FreshRSS. " "Useful for first-time testing.",
    )
    parser.add_argument(
        "--include-read",
        action="store_true",
        help="Include items from the last MAX_AGE_DAYS regardless of "
        "read/unread state. Implies --no-mark-read. Useful for size testing.",
    )
    args = parser.parse_args(argv)

    logging_setup.configure()
    try:
        config = load_config()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2

    mark_read = not (args.no_mark_read or args.include_read)
    return run(
        config,
        dry_run=args.dry_run,
        mark_read=mark_read,
        include_read=args.include_read,
    )


def run(
    config: Config,
    dry_run: bool = False,
    mark_read: bool = True,
    include_read: bool = False,
) -> int:
    """Execute one Run of the daily Issue, end-to-end.

    Every non-excluded article is collected into a single combined Issue —
    one EPUB with each Category as a TOC section. When the combined book
    exceeds ``config.max_epub_bytes`` it is split into multiple Parts on
    Category boundaries (a single Category that overflows alone is split
    mid-Category). All Parts are built up front, so a build failure ships
    nothing; each Part is then sent and marked-read independently, so a
    single Part's SMTP failure leaves only its items unread.

    Parameters
    ----------
    config : Config
        Loaded configuration.
    dry_run : bool
        If ``True``, build the EPUB(s) but skip SMTP send and mark-as-read.
    mark_read : bool
        If ``False``, send the email but leave items unread in FreshRSS.
        Ignored when ``dry_run`` is ``True``.
    include_read : bool
        If ``True``, pull recent items regardless of read state.

    Returns
    -------
    int
        Process exit code: 0 on success (or "nothing to send"); 1 if the
        build failed or any Part failed to send.
    """
    today = dt.date.today()
    client = FreshRSSClient(config)

    if include_read:
        items = client.list_recent(limit=FETCH_POOL_LIMIT)
    else:
        items = client.list_unread(limit=FETCH_POOL_LIMIT)

    result = selector.select(items, config)
    items = result.items
    if result.dropped_excluded_category:
        log.info(
            "dropped %d items in excluded categories: %s",
            result.dropped_excluded_category,
            sorted(config.excluded_categories),
        )
    if result.dropped_too_old:
        log.info(
            "dropped %d items older than %dd (published-date filter)",
            result.dropped_too_old,
            config.max_age_days,
        )
    if result.no_timestamp:
        log.warning(
            "%d item(s) had no published timestamp; kept (inbox-drain)",
            result.no_timestamp,
        )

    if not items:
        log.info("no items; nothing to do")
        return 0

    articles = fetcher.fetch_all(items)
    articles = _drop_oversized_articles(articles)
    if not articles:
        log.warning("no fetchable articles after filtering; nothing to do")
        return 0

    category_order = _category_order(articles)
    log.info(
        "building one issue from %d articles across %d categories: %s",
        len(articles),
        len(category_order),
        ", ".join(f"{cat}({sum(1 for a in articles if a.category == cat)})" for cat in category_order),
    )

    # Partition into size-capped Parts on Category boundaries. The chunker's
    # measurement builds happen here; a failure means we ship nothing today.
    try:
        with tempfile.TemporaryDirectory(prefix="kindle_daily_chunk_") as tmp:
            measure_dir = Path(tmp)

            def measure_build(arts: list[Article]) -> Path:
                return epub_builder.build(arts, date=today, out_dir=measure_dir, cover_jpeg=None)

            parts = chunker.split(articles, measure_build, config.max_epub_bytes, category_order)
    except Exception:
        log.exception("failed to partition the daily issue; nothing sent")
        return 1

    n = len(parts)
    log.info("issue split into %d part(s)", n)

    # Build every Part before sending any, so a build failure ships nothing
    # (no partial delivery). Each Part gets its own cover and Part label.
    try:
        built = [
            _build_part(config, part_articles, today=today, index=i, total=n) for i, part_articles in enumerate(parts)
        ]
    except Exception:
        log.exception("failed to build the daily issue; nothing sent")
        return 1

    if dry_run:
        for epub_path in built:
            log.info("dry-run: built %s; not sending, not marking read", epub_path)
        return 0

    failures: list[int] = []
    for i, (epub_path, part_articles) in enumerate(zip(built, parts)):
        subject = _subject(today, index=i, total=n)
        try:
            mailer.send(config, epub_path, subject=subject)
        except smtplib.SMTPException:
            log.exception(
                "part %d/%d: SMTP send failed after retry; leaving ids unread",
                i + 1,
                n,
            )
            failures.append(i + 1)
            continue

        if mark_read:
            client.mark_read([a.item_id for a in part_articles])
        else:
            log.info(
                "part %d/%d: --no-mark-read, leaving %d items unread",
                i + 1,
                n,
                len(part_articles),
            )

    if failures:
        log.warning("done with %d failed part(s): %s", len(failures), failures)
        return 1
    log.info("done")
    return 0


def _drop_oversized_articles(articles: list[Article]) -> list[Article]:
    """Drop articles whose body + images exceed ``MAX_ARTICLE_BYTES``.

    Such articles can't fit in any 15 MB EPUB after scaffolding overhead, so
    chunking would never converge. They stay unread in FreshRSS so the user
    can read them in another RSS client.
    """
    kept: list[Article] = []
    for article in articles:
        size = len(article.body_html.encode("utf-8")) + sum(len(img.data) for img in article.images)
        if size > MAX_ARTICLE_BYTES:
            log.warning(
                "skipping oversized article (%.1f MB > %.0f MB cap): %s",
                size / (1024 * 1024),
                MAX_ARTICLE_BYTES / (1024 * 1024),
                article.url or article.title,
            )
            continue
        kept.append(article)
    return kept


def _category_order(articles: list[Article]) -> list[str]:
    """Order the Categories present in ``articles`` for the day's Issue.

    Ordering is by article count descending, with alphabetical tiebreaks.
    Misc participates by the same rule as every other Category — no special
    pinning — so the order is fully determined by the day's content and needs
    no config the user has to maintain when subscriptions change.
    """
    counts: dict[str, int] = {}
    for article in articles:
        counts[article.category] = counts.get(article.category, 0) + 1
    return sorted(counts, key=lambda category: (-counts[category], category))


def _part_categories(part_articles: list[Article]) -> list[str]:
    """Return the Categories present in a Part, in first-seen (delivery) order."""
    seen: list[str] = []
    for article in part_articles:
        if article.category not in seen:
            seen.append(article.category)
    return seen


def _subject(today: dt.date, *, index: int, total: int) -> str:
    """Email subject for the ``index``-th of ``total`` Parts (0-indexed)."""
    if total == 1:
        return f"Kindle Daily — {today.isoformat()}"
    return f"Kindle Daily Part {index + 1}/{total} — {today.isoformat()}"


def _build_part(
    config: Config,
    part_articles: list[Article],
    *,
    today: dt.date,
    index: int,
    total: int,
) -> Path:
    """Render the cover and build one delivered Part of the day's Issue."""
    part = None if total == 1 else (index + 1, total)
    cover_jpeg = cover.render_cover(
        part_articles,
        date=today,
        count=len(part_articles),
        categories=_part_categories(part_articles),
    )
    return epub_builder.build(
        part_articles,
        date=today,
        out_dir=config.out_dir,
        cover_jpeg=cover_jpeg,
        part=part,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
