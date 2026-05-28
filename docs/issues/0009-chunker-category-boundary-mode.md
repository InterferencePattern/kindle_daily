# 0009 — `chunker` gains category-boundary mode

## Parent

[ADR 0002 — One Issue per Run, not one Issue per Category](../adr/0002-single-daily-issue-not-per-category.md)

## What to build

Extend the `chunker` deep module to support packing a multi-category article
list into Parts that split on Category boundaries, with the current
intra-Category bisection kept as a fallback for the pathological case where
a single Category alone overflows the size cap.

Today `chunker.split` is single-Category by contract: it receives one
Category's articles and bisects them by article count until each chunk fits.
The new mode receives the full Run's article list plus a Category ordering
and packs Categories whole, in the given order, into successive Parts.

### Behaviour

The packing algorithm proceeds greedily over Categories in the supplied
order:

1. Group articles by Category, preserving each Category's internal
   publish-time order.
2. Starting from an empty Part, append Categories one at a time. After each
   append, measure the candidate Part via `build_fn`.
3. If the candidate fits the cap, the appended Category stays and the
   algorithm moves to the next Category.
4. If the candidate exceeds the cap, the just-appended Category is removed,
   the current Part is sealed, and a new Part begins with that Category as
   its first member.
5. If a single Category alone exceeds the cap on a fresh Part, fall back to
   the existing intra-Category bisection (recursive halving) for that
   Category only. The resulting sub-chunks each become their own Part. The
   next Category continues packing from a fresh Part afterwards.

Order is preserved end-to-end: the first Part contains the first Category
in the supplied order, the last Part the last. The function performs no I/O
of its own beyond what `build_fn` does.

### Inputs and outputs

The function signature carries a Category ordering so the chunker doesn't
have to know how the order is computed — that's the pipeline's
responsibility. The result remains `list[list[Article]]`, one sub-list per
Part, in delivery order. The caller is still responsible for re-building
each final Part with its real cover and filename.

### Tests

A new test file covers the category-boundary mode end-to-end with a fake
`build_fn` that returns deterministic synthetic sizes. The existing
single-Category bisection path keeps its current tests; the fallback path
is exercised by a scenario where one Category's synthetic size exceeds the
cap on its own.

Scenarios to cover:

- Single Part: all Categories fit under the cap together → returns one
  Part containing all articles in Category order.
- Multi-Part on boundaries: two Categories at half-cap each, three
  Categories at three-quarter-cap each, etc. → Parts break exactly on
  Category boundaries with no Category split.
- Fallback: one Category alone exceeds the cap → that Category is bisected
  intra-Category, other Categories pack normally around it.
- Empty input → empty output.
- Single Category, single Part (legacy path) still works and matches the
  pre-change behaviour bit-for-bit.
- Article order preserved across Parts (oldest article in earliest Part of
  the Category that contains it).
- `build_fn` is called a bounded number of times — at most one
  measurement per candidate append plus one per recursive fallback node.

## Acceptance criteria

- [ ] `chunker.split` accepts a Category ordering and groups articles by
      Category internally.
- [ ] Multi-Category input with no Category exceeding the cap alone
      produces Parts that break only on Category boundaries.
- [ ] A single Category that alone exceeds the cap is bisected
      intra-Category via the existing recursive halving logic.
- [ ] The pre-existing single-Category callsite contract is preserved
      (callers passing one Category get behaviour identical to today).
- [ ] New `tests/test_chunker.py` cases cover the scenarios listed above
      with a fake `build_fn`.
- [ ] `pytest` passes.

## Blocked by

None — can start immediately.
