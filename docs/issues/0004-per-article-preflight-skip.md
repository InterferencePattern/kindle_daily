# 0004 — Per-article pre-flight 14 MB skip

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user story 8)

## What to build

Before any chunking or EPUB build happens in `_run_issue`, drop any
single article whose estimated content size exceeds 14 MB. Such
articles cannot fit in any EPUB (under the 15 MB EPUB cap with
scaffolding overhead), so chunking would never converge on a working
size — they need to be excluded upstream.

Estimation:

- `len(article.body_html.encode("utf-8")) + sum(len(img.data) for img in article.images)`.
- The 14 MB threshold leaves ~1 MB headroom for EPUB scaffolding (CSS,
  nav, TOC, cover, per-chapter wrapper). This is a worst-case estimate
  because EPUBs zip-compress prose well; the false-positive rate is
  acceptable per the PRD.

Behavior on skip:

- Article does **not** reach the chunker, builder, mailer, or
  mark-read. It stays unread in FreshRSS so the user can read it in
  another RSS client.
- Single WARNING log per skipped article with the URL and the
  estimated size, e.g.:
  `kindle_daily._run_issue: skipping oversized article (18.2 MB > 14 MB cap): https://example.com/long-read`
- Issue continues with the remaining articles. If all articles in a
  Category were oversized, the Category produces no Issue (existing
  `if not articles: skip` path).

Where the skip lives:

- Inside `_run_issue`, after `fetcher.fetch_all` and before any
  cover/build/chunker work.

## Acceptance criteria

- [ ] `_run_issue` skips articles whose estimated size exceeds
      `14 * 1024 * 1024` bytes.
- [ ] Skipped articles do not appear in the EPUB.
- [ ] Skipped articles are NOT passed to `client.mark_read`.
- [ ] A WARNING log is emitted per skip with the URL and estimated
      size.
- [ ] New test in `test_main.py`: oversized article in the input
      list does not reach `fetcher`'s output → does not reach the
      builder → does not appear in the mark-read call.
- [ ] Existing tests pass without modification (they all use small
      synthetic articles).
- [ ] `pytest` passes.

## Blocked by

None — can start immediately. (#7 will rely on this being in place
before chunker runs, but #4 itself is self-contained against the
current pipeline.)
