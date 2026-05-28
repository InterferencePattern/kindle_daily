# 0002 — Switch age filter from crawl-date to client-side published-date

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user stories 11, 12)

## What to build

Replace the server-side FreshRSS `ot` (crawl-time) filter with a
client-side filter on each `FeedItem`'s `published_ms`. Default window
shrinks from 24 hours to 7 days, and the unit changes from hours to
days. This guards against the "added a new feed with years of
backlog" scenario while eliminating the crawl-vs-published-date
confusion.

Behavioral details:

- Filter is applied in `main.run` after the FreshRSS fetch, before
  category exclusion (or wherever it slots in next to the other
  filters — order doesn't matter for correctness).
- Cutoff is `now - max_age_days days` in epoch ms.
- Items with `published_ms == 0` (no timestamp in the feed) pass
  through with a single WARNING log per Run summarizing how many.
  Inbox-drain prefers over-Delivery to silent loss for malformed
  feeds.
- A single INFO log records the drop count when items are filtered.

Config changes:

- Rename `Config.max_age_hours` → `Config.max_age_days`.
- Rename env var `MAX_AGE_HOURS` → `MAX_AGE_DAYS`.
- Change default `24` → `7`.

FreshRSS client changes:

- Drop the `since_seconds` parameter from `list_unread`, `list_recent`,
  and `_list`. All three signatures change.
- Drop the `("ot", str(since_seconds))` param from the GReader request.

Test changes:

- Delete `test_list_unread_passes_since_seconds_as_ot_param` from
  `test_freshrss.py`.
- Update the `conftest.py` `Config` fixture to use `max_age_days=7`.
- Update all test fakes in `test_main.py` that mention
  `since_seconds=` to drop that kwarg.
- Add new tests for the published-date filter: too old → dropped;
  recent → kept; `published_ms == 0` → kept with warning.

README:

- Update the config table: `MAX_AGE_HOURS` row becomes `MAX_AGE_DAYS`
  with new default and description mentioning published date.

## Acceptance criteria

- [ ] `grep -r "ot\b\|since_seconds\|max_age_hours\|MAX_AGE_HOURS" src/ tests/`
      returns no matches.
- [ ] `Config` has `max_age_days: int` field with default 7.
- [ ] `FreshRSSClient.list_unread` and `list_recent` no longer accept
      `since_seconds`.
- [ ] No `ot` parameter is sent to FreshRSS in any request.
- [ ] New tests cover: item older than `max_age_days` is dropped;
      item within window is kept; item with `published_ms == 0` is
      kept and triggers a warning log.
- [ ] `pytest` passes.
- [ ] README config table reflects new var name, default, and
      published-date semantics.

## Blocked by

None — can start immediately.
