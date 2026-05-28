# 0011 — Cover redesign: category roster subtitle

## Parent

[ADR 0002 — One Issue per Run, not one Issue per Category](../adr/0002-single-daily-issue-not-per-category.md)

## What to build

Rework the EPUB cover so its subtitle slot shows the roster of Categories
present in the Part, replacing the old per-Category single-name subtitle
and its weekday fallback.

Today the cover's "line two" slot (under the masthead) renders the
per-Category name when one is supplied, falling back to the weekday when
it isn't. With the unified Issue there is no single Category, so the slot
becomes a roster of the Categories present in that Part, rendered as an
ordered, separator-joined list (e.g. `PHOTOGRAPHY · TECH · POLITICS ·
MISC`) in the same Category order the Issue uses.

### Behaviour

- The cover renderer accepts the Part's ordered Category list and joins it
  into a single roster line.
- When the rendered roster exceeds the available cover width, it is
  truncated with an ellipsis (e.g. `PHOTOGRAPHY · TECH · POLITICS …`).
  The EPUB's TOC remains the authoritative complete list, so truncation
  loses no information the reader can't recover inside the book.
- The weekday fallback is removed. The full date is still drawn lower on
  the cover, so the weekday line was redundant.
- The bottom-of-cover article count line is unchanged and continues to
  show the count of articles in that Part.

### Caller

`main.py` passes the Categories present in each Part, in Issue order, to
the cover renderer (wired in issue #0010). Single-Part Runs pass the full
Run roster; multi-Part Runs pass only the Categories in that Part.

### Tests

- A roster that fits renders all Categories joined by the separator.
- A roster that overflows the cover width is truncated with a trailing
  ellipsis and does not exceed the cover bounds.
- No weekday string is rendered in the subtitle slot under any input.
- The article-count line still renders the supplied count.

## Acceptance criteria

- [ ] The cover subtitle slot renders an ordered, separator-joined
      Category roster supplied by the caller.
- [ ] An over-width roster is ellipsis-truncated within the cover bounds.
- [ ] The weekday fallback is removed from the cover.
- [ ] The article-count line is unchanged.
- [ ] Tests cover the fitting, overflow/truncation, and no-weekday cases.
- [ ] `pytest` passes.
- [ ] An end-to-end `--dry-run` produces a cover whose subtitle shows the
      Part's Category roster.

## Blocked by

- [#0010 — Pipeline collapse: one Issue per Run](./0010-pipeline-collapse-single-issue.md)
