# PRD 0001 — Chunked Inbox Drain

## Problem Statement

Each morning, kindle_daily is supposed to deliver every unread article in
FreshRSS to my Kindle so I can read them throughout the day. In practice it
doesn't:

- High-volume Categories (Photography, Tech) regularly produce more articles
  than fit in one EPUB. When that happens today, the code halves the article
  list to make it fit and marks only the kept half as read. The dropped half
  stays unread in FreshRSS and reappears tomorrow — but tomorrow brings new
  articles too, so the backlog never drains. Some Photography articles have
  been showing up in five consecutive daily Runs without ever being
  Delivered.

- The same articles show up two or three days in a row even when the
  previous day's Issue did Deliver them. FreshRSS is reissuing items with
  new item_ids (typical for digest feeds like LensCulture), so the
  mark-read does nothing the next day.

- `MAX_ARTICLES = 50` silently truncates Categories with more than 50
  articles. Anything past the cap is dropped on the floor without being
  marked read or Delivered, then loses its place in the queue as newer
  items push it back.

- `MAX_AGE_HOURS = 24` uses FreshRSS's `ot` parameter, which filters by
  *crawl time* not *published time*. An article published months ago but
  crawled today slips through; an article published yesterday but crawled
  the day before doesn't.

The combined effect: I have no confidence that articles I haven't manually
read in FreshRSS will reach my Kindle. The system has become a lossy filter
when it was supposed to be a faithful drain.

## Solution

Rewrite the per-Run pipeline so the contract is provably "every unread
article that survives category exclusion is Delivered in the same Run, and
nothing else is marked read." See [ADR-0001](../adr/0001-inbox-drain-not-curated-digest.md).

Concretely, when a Category produces more content than fits in a single
EPUB, the Run produces multiple Issues for that Category ("Photography
Part 1", "Photography Part 2", …) and Delivers all of them in the same
Run. Articles past the per-EPUB size cap are no longer dropped — they get
their own Issue.

The age filter is changed to use article *published* date (client-side, not
FreshRSS-side) with a 7-day window, so the "huge backlog on first day after
adding a new feed" scenario is still bounded but the crawl/published
mismatch goes away. Removing per-Category article caps eliminates the
silent drop class entirely. URL-level dedup is removed; if FreshRSS thinks
something is unread, we Deliver it.

## User Stories

1. As the user, I want every unread article in FreshRSS to be Delivered to
   my Kindle in the same Run that fetched it, so that I don't have to
   audit FreshRSS to make sure nothing was lost.

2. As the user, I want a Category with more articles than fit in one EPUB
   to be split into multiple Issues that all Deliver in the same Run, so
   that high-volume Categories like Photography drain completely each day.

3. As the user, I want chunked Issues to carry a "Part N" suffix in their
   subject and filename only when there are multiple chunks, so that
   single-Issue Categories look identical to today and chunked Categories
   are obviously chunked.

4. As the user, I want each chunked Issue to have its own cover image
   derived from its own slice of articles, so that the cover represents
   what's inside that specific Issue.

5. As the user, I want chunk Issues to ship in publish-time order ("Part
   1" is the oldest articles, "Part N" is the newest), so that I read them
   chronologically.

6. As the user, I want an SMTP failure on one chunk to be retried once and
   then logged and skipped, so that one transient failure doesn't waste
   the work of already-Delivered chunks or block subsequent chunks.

7. As the user, I want articles in a failed chunk to stay unread in
   FreshRSS, so that tomorrow's Run picks them up naturally.

8. As the user, I want articles whose individual size exceeds 14 MB to be
   skipped during a Run with a loud log line and left unread in FreshRSS,
   so that I can read them in another RSS reader without the pipeline
   getting stuck or producing oversized EPUBs.

9. As the user, I want `MAX_ARTICLES` removed entirely, so that no
   articles are silently dropped just because today brought a lot of them.

10. As the user, I want `MAX_EPUB_MB` to default to 15, so that we have
    safe headroom under Gmail's 25 MB encoded-attachment limit.

11. As the user, I want a 7-day filter applied to article *published* date
    (not crawl date), so that adding a new feed with years of backlog
    doesn't flood my Kindle with old articles on the first Run.

12. As the user, I want articles with no published timestamp to be
    Delivered (not silently filtered out), so that poorly-formed feeds
    don't lose articles — accepting that a malformed feed publishing
    everything with a missing timestamp would over-Deliver.

13. As the user, I want the URL-based dedup cache removed, so that the
    system has a single source of truth (FreshRSS's unread state) and
    doesn't silently skip anything FreshRSS thinks is unread.

14. As the user, I want articles in `EXCLUDED_CATEGORIES` to remain
    untouched (no Delivery, no mark-read), so that feeds I read elsewhere
    (e.g. Comics on my phone) don't accumulate read-state from a tool
    that never sent them anywhere.

15. As the user, I want articles that fail to fetch to stay unread without
    any quarantine machinery, so that I can clean them up via another RSS
    reader on a different device.

16. As the user, I want mark-read to only happen after a confirmed Gmail
    250 OK for the specific Issue containing the article, so that
    Delivery and read-state can never disagree.

17. As the user, I want per-Category failure isolation preserved (a
    failure in Photography doesn't block Tech), so that one bad Category
    can't ruin the whole Run.

18. As a developer of this codebase, I want article selection (excluded
    categories + age filter) extracted into a pure function I can test in
    isolation, so that I can add new filter rules with confidence.

19. As a developer of this codebase, I want the chunking algorithm
    extracted into a pure function with an injected build function, so
    that I can test split correctness without building real EPUBs.

20. As a developer of this codebase, I want all existing per-Category and
    end-to-end tests updated to reflect the new contract, so that the
    test suite serves as the executable spec of inbox-drain semantics.

21. As the user, I want a `check-config` command that loads my `.env` and
    pretty-prints the parsed values (including the parsed form of
    list-valued vars like `EXCLUDED_CATEGORIES`), so that I can verify
    my config the way the application sees it before deploying — without
    triggering a real fetch/send.

## Implementation Decisions

### Architecture: two new deep modules

**`selector`** — pure function that takes `(items, config)` and returns
`(selected_items, drop_stats)`. Encapsulates two filters applied in order:

1. Drop items whose `category` is in `EXCLUDED_CATEGORIES`.
2. Drop items whose `published_ms > 0` AND `published_ms` is older than
   `MAX_AGE_DAYS` from now. Items with `published_ms == 0` pass through
   with a warning logged.

The function returns drop counts per filter so `main` can log them. No
I/O, no global state, trivially testable with synthetic FeedItems.

**`chunker`** — takes `(articles, build_fn, size_cap_bytes)` where
`build_fn(articles) -> Path` builds an EPUB and returns its path.
Returns `list[list[Article]]` — one inner list per chunk. Algorithm:

1. Call `build_fn(articles)`, measure size.
2. If size ≤ cap, return `[articles]`.
3. Else compute `N = ceil(size / cap)`, split `articles` into N
   equal-count contiguous slices, recursively chunk each slice.
4. Combine the results.

`build_fn` is injected so tests can pass a fake builder that reports
deterministic synthetic sizes without writing real EPUBs.

### Modified modules

**`config`**:
- Drop `max_articles` field and the `MAX_ARTICLES` env var.
- Drop `dedup_retention_days` field and the `DEDUP_RETENTION_DAYS` env var.
- Rename `max_age_hours` → `max_age_days`, env var
  `MAX_AGE_HOURS` → `MAX_AGE_DAYS`, default value `24` → `7`.
- Change `MAX_EPUB_MB` default `10` → `15`.

**`freshrss`**:
- Drop the `since_seconds` parameter from `list_unread`, `list_recent`,
  and the private `_list` (all three signatures change).
- Drop the `("ot", ...)` line from request params construction.
- Update tests accordingly; delete the `since_seconds`-as-`ot` test.

**`mailer`**:
- `send` retries once on `SMTPException` with a short fixed delay (e.g.
  10 seconds). On second failure the exception propagates. Login is
  re-attempted on the retry.
- The retry policy lives in `mailer` so `_run_issue` doesn't need to
  know about SMTP failure modes.

**`main._run_issue`**:
- Becomes: pre-flight per-article size skip → `chunker.split(...)` → for
  each chunk: build → send (mailer retries internally) → on success,
  mark just that chunk's article ids read.
- Pre-flight skip: any article where
  `len(body_html) + sum(len(img.data) for img in images)` exceeds
  `14 * 1024 * 1024` is dropped from the input to `chunker`, logged at
  WARNING with the URL, and left unmarked.
- Chunk labeling: when `len(chunks) > 1`, subject becomes
  `"Kindle Daily: {Category} Part {i+1} — {date}"` and filename suffix
  becomes `_part{i+1}of{n}`. When `len(chunks) == 1`, today's existing
  subject and filename are preserved exactly.
- Per-chunk covers: pass each chunk's article slice to `cover.render_cover`
  independently — already supported by the cover code, no change needed.

**`main.run`**:
- Drop `since_seconds` calculation entirely.
- Drop the URL-dedup pre-filter block.
- Call `selector.select(items, config)` instead of inline filtering.
- The rest of the orchestration loop (per-Category, alphabetical) is
  unchanged.

### Deletions

- `src/kindle_daily/sent_urls.py` — entire file.
- Its tests in `tests/test_main.py` (the `test_url_dedup_*` cases I added
  earlier).
- The `DEDUP_RETENTION_DAYS` row in `README.md`'s config table.

### CLI flags

- `--dry-run` and `--no-mark-read` are unchanged.
- `--include-read` keeps its size-testing purpose. Its dedup-cache
  carve-out (the `not include_read` check) is moot because dedup is gone.
  The flag still bypasses unread-only filtering at the FreshRSS layer.

### Cron / schedule

- Unchanged: single Run per day at 05:00 local time via `install.sh`'s
  cron line.
- One Run may now generate substantially more emails than before on
  heavy days (several Categories × 2–3 chunks each). Personal Gmail
  allows 500 messages per day, so this is well below the ceiling even
  in worst-case scenarios.

## Testing Decisions

### What makes a good test

Test external behavior of each module — what callers see — not internal
helpers or data structures. For `selector` and `chunker`, that means
feeding inputs and asserting on outputs without mocking internals. For
`_run_issue`, that means mocking I/O (FreshRSS, fetcher, mailer) and
asserting on the sequence and contents of calls. The existing
`tests/test_main.py` pattern (a `_install_fakes` helper that wires a
fake `FreshRSSClient`, a fake `fetcher.fetch_all`, and a fake
`mailer.send`) is the prior art and should be reused.

### Modules to test

**`selector`** (new): heavy coverage. Cases:
- No filters configured: passes everything through.
- Excluded categories: drops matching items, keeps others.
- Age filter: drops items older than `max_age_days`, keeps newer.
- Items with `published_ms == 0`: included with warning.
- Combinations of all three filters.
- Returned drop counts match expected.

**`chunker`** (new): heavy coverage with a deterministic fake `build_fn`.
Cases:
- Fits in one chunk: returns single-element list.
- Splits exactly in half when total = 2 × cap.
- Splits into 3 when total = 3 × cap.
- Recursively re-splits when initial split still has oversized chunk
  (uneven article sizes).
- Single oversized article (caller's responsibility to pre-skip — but
  `chunker` should not crash; it should return that article as its own
  chunk and let the build/send fail naturally).

**`mailer`** (modified): light coverage. One test: first send raises
`SMTPException`, second succeeds → `send` returns normally and called
twice. One test: both attempts raise → exception propagates. Use
`monkeypatch` on `smtplib.SMTP_SSL`.

**`main._run_issue`** (modified): integration coverage extending
existing patterns. New cases:
- Single-chunk happy path (no chunking triggered): subject and filename
  match today's format exactly, all article ids marked read.
- Multi-chunk happy path: N chunks built, all sent, all article ids
  marked read; subjects carry `Part i` suffix; filenames carry `_partN
  ofN` suffix.
- Multi-chunk with one chunk failing on first attempt and succeeding on
  retry: all chunks Delivered, all ids marked read.
- Multi-chunk with one chunk failing on both attempts: other chunks
  Delivered, only their ids marked read, failed chunk's ids stay unread.
- Pre-flight oversized article skip: oversized article does not reach
  the builder, is logged, and is not marked read.

**`freshrss`** (modified): update existing tests for signature changes.
Delete the `ot`-parameter test.

**`config`** (modified): update existing tests for renamed/dropped
fields and new defaults.

## Out of Scope

- Multi-Run-per-day scheduling. The cron stays daily.
- Per-Category overrides for `MAX_EPUB_MB` or chunk size. One global cap.
- Smarter chunking that tries to balance article counts vs. estimated
  reading time, or breaks at natural boundaries. The equal-count split is
  good enough.
- Quarantine / dead-letter handling for skipped articles. They just stay
  unread; the user clears them via other RSS readers.
- Any change to the cover rendering, EPUB layout, or trafilatura
  extraction pipeline. Those modules are reused unchanged.
- Backwards-compatibility shims for the old config env vars
  (`MAX_ARTICLES`, `MAX_AGE_HOURS`, `DEDUP_RETENTION_DAYS`). They are
  removed cleanly; the README documents the new vars; the user updates
  their `.env`.
- The `--include-read` flag's exact semantics under chunking. It already
  short-circuits the unread filter; chunking applies regardless.
- The Connection pool warnings visible in production logs
  (`urllib3.connectionpool: Connection pool is full`). Unrelated to this
  PRD.

## Further Notes

- The Gmail 25 MB attachment cap is on the *encoded* message size, not
  the raw file. Base64 inflates attachments by ~33%. The 15 MB raw cap
  gives ~5 MB headroom after encoding. Verified May 2026 against current
  Gmail and Amazon Send-to-Kindle documentation.
- The `MAX_AGE_DAYS = 7` value is a guard against the "new feed brings
  years of backlog" scenario. It is not a tuning knob for daily volume —
  daily volume is controlled by which feeds the user subscribes to in
  FreshRSS and which Categories they exclude.
- After this PRD lands, the only remaining surface for "silent" article
  loss is articles that fail to fetch (existing, unchanged behavior) and
  articles whose individual size exceeds 14 MB (newly explicit). Both
  produce loud log lines per occurrence.
- The existing per-Category failure isolation in `main.run` (one bad
  Category logs and continues to the next) is preserved verbatim. The
  chunking changes are entirely contained within `_run_issue`.
