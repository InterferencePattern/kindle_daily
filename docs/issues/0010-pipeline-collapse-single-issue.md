# 0010 — Pipeline collapse: one Issue per Run

## Parent

[ADR 0002 — One Issue per Run, not one Issue per Category](../adr/0002-single-daily-issue-not-per-category.md)

## What to build

Rewrite the daily Run pipeline so it produces a single combined Issue
containing every non-excluded Category as a TOC section inside one EPUB,
instead of the current one-Issue-per-Category loop.

The per-Category outer loop in `main.run` is deleted. Selection, exclusion,
and per-article oversize pre-flight remain unchanged. After selection, the
full surviving article set goes through one build path:

1. Compute the Run's Category ordering: article count descending, with
   alphabetical tiebreaks. Misc participates by the same rule as every
   other Category — no special pinning.
2. Hand the full article list plus the Category ordering to
   `chunker.split` (using the new category-boundary mode from issue
   #0009).
3. For each resulting Part, in order: build the EPUB, send it via the
   mailer (with the mailer's existing retry), and on SMTP 250 mark that
   Part's article ids read in FreshRSS.

### Failure model

The previous per-Category isolation is intentionally dropped (see
ADR-0002). On the happy path, the Run produces one Part and the whole
Run succeeds or fails as a unit. When a multi-Part split is needed:

- A build failure on any Part aborts the Run with a loud error log. No
  Parts are sent. No ids are marked read. The unread queue carries
  everything into tomorrow's Run.
- An SMTP failure on a Part (after the mailer's retry) leaves that Part's
  ids unread and continues to subsequent Parts. The Run's exit code
  reflects the partial failure exactly as today.

### Labels

When the Run produces one Part:

- Subject: `Kindle Daily — {YYYY-MM-DD}` (no Category in the subject).
- Filename: `kindle_daily_{YYYY-MM-DD}.epub` (the existing no-Category
  fallback in `epub_builder`).

When the Run produces multiple Parts (`n > 1`):

- Subject: `Kindle Daily Part {i}/{n} — {YYYY-MM-DD}`.
- Filename: `kindle_daily_{YYYY-MM-DD}_part{i}of{n}.epub`.

The `category=` kwarg is dropped at the `epub_builder.build` callsite —
the builder already groups articles by Category internally and emits TOC
sections without needing the caller to pre-partition.

### Cover wiring

The cover is built per-Part. Its subtitle slot receives the Categories
present in *that* Part as an ordered list of strings — the cover module
decides how to render them (issue #0011). The existing article-count
parameter continues to receive the count of articles in that Part.

### Tests

`tests/test_main.py` is rewritten to match the unified pipeline:

- Single-Part happy path: a Run with multiple Categories totalling under
  the cap produces exactly one EPUB whose subject and filename carry no
  Part suffix; all article ids are marked read.
- Multi-Part on Category boundaries: a Run whose Categories collectively
  exceed the cap produces N Parts that break only on Category
  boundaries, each Part sends, each Part's ids are marked read
  independently.
- SMTP failure on one of N Parts: other Parts deliver and mark read,
  failed Part's ids stay unread, exit code reflects the failure.
- Build failure on the combined Issue: no email sent, no ids marked
  read, exit code reflects the failure.
- Category ordering: a Run with known per-Category counts produces an
  Issue whose internal section order matches count-desc + alpha
  tiebreak, with Misc participating by the same rule.

The old per-Category isolation tests (one Category fails, another still
ships) are deleted — that behaviour no longer exists.

### README

Refresh the "how it works" section to describe the unified pipeline.
Remove any references to per-Category Issues from the user-facing
documentation. The pipeline diagram should show: select → group/order
→ chunk → for each Part: build → send → mark read.

## Acceptance criteria

- [ ] `main.run` no longer contains a per-Category loop.
- [ ] One Run produces one combined EPUB on the happy path, with every
      non-excluded Category appearing as a TOC section.
- [ ] Multi-Part splits use the category-boundary mode from issue #0009.
- [ ] Category order inside the Issue (and across Parts) is article-count
      descending with alphabetical tiebreaks; Misc is not pinned.
- [ ] Subject and filename carry no Category name. They gain a `Part i/n`
      suffix iff `n > 1`.
- [ ] Each Part's `mark_read` call contains only that Part's article ids.
- [ ] A failed Part does NOT mark its articles read; subsequent Parts
      still run.
- [ ] A build failure on the combined Issue aborts the Run with no
      Delivery.
- [ ] `grep -rn "per-category\|per_category\|category={\|Kindle Daily: " src/ tests/ README.md docs/`
      returns no matches outside historical issues/PRDs.
- [ ] Rewritten `tests/test_main.py` covers the scenarios listed above.
- [ ] `pytest` passes.
- [ ] An end-to-end `--dry-run` against a Run with multiple Categories
      produces one EPUB containing every Category as a TOC section.

## Blocked by

- [#0009 — `chunker` gains category-boundary mode](./0009-chunker-category-boundary-mode.md)
