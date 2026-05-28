# 0006 — Extract `selector` deep module

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user story 18)

## What to build

Pull the item-selection logic out of `main.run` into a new `selector`
module. This is a pure-function refactor — no behavior change — to
give us a unit-testable seam over the filter rules.

The new module exposes:

```python
def select(items: list[FeedItem], config: Config) -> SelectResult:
    """Apply all selection filters in order and return what survived."""
```

Where `SelectResult` carries the surviving items plus per-filter drop
counts so `main.run` can log them with the same INFO lines we have
today.

Filters, applied in this order:

1. **Excluded categories.** Drop items whose `category` is in
   `config.excluded_categories`. (Today's inline filter in `main.run`.)
2. **Published-date age window.** Drop items whose `published_ms > 0`
   AND `published_ms` is older than `config.max_age_days` from now.
   Items with `published_ms == 0` pass through. (From issue #0002.)

Refactor scope:

- Move both filter blocks out of `main.run` into `selector.select`.
- `main.run` calls `selector.select(items, config)` once, then logs
  the drop counts, then proceeds to grouping.
- `selector` has no I/O, no global state, no logging — it returns
  drop counts and lets the caller decide how to log.

Tests for the new module (`tests/test_selector.py`):

- Empty input → empty output, all drop counts zero.
- Excluded-category filter: items in excluded categories dropped,
  others kept, drop count correct.
- Age filter: items older than cutoff dropped, items newer kept,
  items with no timestamp kept.
- Combined: an item in an excluded category that's also too old is
  counted once (per the filter that catches it first) — clarify in
  the test what the contract is.

Updates to existing tests:

- The `test_excluded_categories_*` tests in `test_main.py` keep
  working through `selector` — verify they still pass without
  modification (since `main.run` still produces the same end-to-end
  behavior).

## Acceptance criteria

- [ ] New file `src/kindle_daily/selector.py` exists.
- [ ] `select(items, config)` returns surviving items plus drop
      counts per filter.
- [ ] `main.run` no longer contains the inline excluded-category
      filter or the published-date filter — both live in `selector`.
- [ ] New `tests/test_selector.py` covers the cases listed above.
- [ ] All existing tests pass without modification.
- [ ] `pytest` passes.
- [ ] No behavior change observable from `--dry-run` output.

## Blocked by

- [#0002 — Switch age filter from crawl-date to client-side
  published-date](./0002-published-date-age-filter.md)
