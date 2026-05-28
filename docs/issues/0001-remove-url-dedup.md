# 0001 — Remove URL dedup cache

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user story 13)

## What to build

Remove the URL-level dedup cache entirely. Under the inbox-drain
contract ([ADR-0001](../adr/0001-inbox-drain-not-curated-digest.md)),
FreshRSS's unread state is the single source of truth: if FreshRSS says
an item is unread, it gets Delivered. There is no parallel cache that
silently skips items.

Scope:

- Delete the `sent_urls` module and its tests.
- Drop the `dedup_retention_days` field from `Config` and the
  `DEDUP_RETENTION_DAYS` env var from `load_config`.
- Drop the dedup pre-filter block from `main.run` (the section that
  constructs a `SentUrlCache`, filters items against it, and records
  URLs after send).
- Drop the dedup carve-out check from the `--include-read` code path.
- Remove the `DEDUP_RETENTION_DAYS` row from the README's config table.
- Remove the `record(...)` call from `_run_issue`'s post-send block —
  it only existed to feed the cache.

The `--include-read` CLI flag itself stays — it's used for size testing
and unrelated to dedup.

## Acceptance criteria

- [ ] `src/kindle_daily/sent_urls.py` no longer exists.
- [ ] `grep -r sent_urls src/ tests/` returns no matches.
- [ ] `grep -ri "dedup\|DEDUP" src/ tests/ README.md` returns no
      matches.
- [ ] `Config` dataclass has no `dedup_retention_days` field.
- [ ] `load_config()` does not read `DEDUP_RETENTION_DAYS`.
- [ ] The `test_url_dedup_*` tests are removed from `test_main.py`.
- [ ] `pytest` passes.
- [ ] A manual end-to-end run with `--dry-run` produces the same set
      of EPUBs as before this change (URL dedup wasn't doing anything
      on a fresh cache file anyway, so behavior should be identical
      modulo the cache file no longer being created).

## Blocked by

None — can start immediately.
