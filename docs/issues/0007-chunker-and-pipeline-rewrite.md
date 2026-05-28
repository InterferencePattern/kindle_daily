# 0007 — Introduce `chunker` module and rewrite per-Issue pipeline

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user stories 1–7, 9, 14–17, 19, 20)

## What to build

This is the main payload of PRD 0001. It introduces the `chunker`
deep module, rewrites `_run_issue` around it, removes `MAX_ARTICLES`,
adds "Part N" labeling for chunked Issues, and proves the
inbox-drain contract via integration tests.

### `chunker` module

New file `src/kindle_daily/chunker.py` exposing:

```python
def split(
    articles: list[Article],
    build_fn: Callable[[list[Article]], Path],
    size_cap_bytes: int,
) -> list[list[Article]]:
    """Partition articles into chunks that each build under the size cap."""
```

Algorithm (recursive):

1. Call `build_fn(articles)`, measure file size.
2. If size ≤ cap, return `[articles]`.
3. Else compute `N = ceil(size / cap)`, split `articles` into N
   equal-count contiguous slices (preserving publish-time order
   inherited from the caller), recursively chunk each slice.
4. Concatenate the resulting chunk lists in order.

`build_fn` is injected so tests can pass a fake builder that
returns synthetic deterministic sizes without writing real EPUBs.
The chunker performs no I/O of its own beyond what `build_fn`
does — measurement happens via `path.stat().st_size`.

Edge cases:

- Single article that still exceeds cap after recursive bisection
  reaches `len(articles) == 1`: chunker returns it as its own
  chunk and lets the caller deal with it. (Issue #0004's pre-flight
  skip prevents this in practice; chunker doesn't need a special
  case.)
- Empty list: return `[]`.

### `_run_issue` rewrite

The per-Category pipeline becomes:

1. `articles = fetcher.fetch_all(items)`.
2. Apply per-article pre-flight 14 MB skip (from issue #0004). Drop
   oversized articles with WARNING logs.
3. If no articles remain, log and return.
4. `chunks = chunker.split(articles, build_fn, config.max_epub_bytes)`
   where `build_fn` is a closure that calls `epub_builder.build` with
   the appropriate filename and cover for the chunk.
5. For each chunk, in order:
   - Build the EPUB (already done by `chunker` during measurement,
     but the chunker returns `list[list[Article]]`, not built EPUBs —
     so the outer loop calls `build_fn` again with the final args
     including the correct `Part N` suffix in filename and cover
     subtitle).
   - Call `mailer.send` (which retries once internally — issue #0005).
   - On send success, call `client.mark_read` with just this chunk's
     article ids.
   - On send failure (both attempts exhausted), log ERROR and
     continue to the next chunk. Failed chunk's articles stay unread.

### Labeling

When `len(chunks) > 1`:

- Subject: `f"Kindle Daily: {category} Part {i+1} — {today.isoformat()}"`
  for the i-th chunk (1-indexed in the user-facing string).
- Filename: `kindle_daily_{slug}_{date}_part{i+1}of{n}.epub`.
- Cover subtitle (the text already shown on the cover): include
  `Part {i+1}/{n}` so the user can tell chunks apart on the Kindle.
- Each chunk's `cover.render_cover` is called with only that chunk's
  articles, so the hero image is representative of what's inside.

When `len(chunks) == 1`:

- Subject, filename, and cover subtitle match today's format exactly
  (no Part suffix anywhere). This is the common case and shouldn't
  feel different to the user.

### Config changes

- Drop `Config.max_articles` field and `MAX_ARTICLES` env var from
  `load_config`.
- Remove the `capped = group_items[: config.max_articles]` line in
  `main.run` — all articles for a Category go to `_run_issue`.
- Remove the `MAX_ARTICLES` row from the README config table.

### Halving removal

- Delete the entire `while epub_path.stat().st_size > config.max_epub_bytes`
  loop from `_run_issue` — the chunker replaces it.
- Delete the `all_fetched` snapshot I added earlier when I "fixed"
  the halving bug — it's dead code under the new pipeline.

### Tests

`tests/test_chunker.py` (new):

- Fits in one chunk → returns single-element list.
- Splits exactly in half when total = 2 × cap (synthetic build_fn
  returns `cap * len(articles) / 2` so the math is deterministic).
- Splits into 3 when total = 3 × cap.
- Recursive re-split: build_fn returns wildly uneven sizes (e.g.
  one article is huge) → chunker recurses on the oversized slice
  and the final partition has each chunk under cap.
- Empty input → empty output.
- Build_fn is called the expected number of times (no unnecessary
  rebuilds — at most one build per recursive node).
- Article order is preserved across chunks (chunk 0 has the oldest
  articles, chunk N-1 the newest).

`tests/test_main.py` (extend):

- Single-chunk happy path: a Category whose EPUB fits in one chunk
  produces today's exact subject and filename (no Part suffix).
- Multi-chunk happy path: a Category whose articles can't fit in
  one chunk produces N Issues with `Part i` suffixes, all sent, all
  ids marked read, ids split correctly across chunks.
- Multi-chunk with one chunk failing on both attempts: other
  chunks Delivered and marked, failed chunk's ids stay unread,
  Run exit code reflects the partial failure (or doesn't — clarify
  in the test based on current per-Category isolation behavior).
- Pre-flight oversized article + chunking interaction: an article
  >14 MB is skipped, remaining articles chunk normally.

### README

- Update the "How it works" section's pipeline diagram to mention
  chunking.
- Document the Part-N labeling convention.
- Remove the `MAX_ARTICLES` row.

## Acceptance criteria

- [ ] New file `src/kindle_daily/chunker.py` with the `split`
      function.
- [ ] `_run_issue` uses `chunker.split` and no longer contains the
      halving while-loop.
- [ ] `Config` has no `max_articles` field; `MAX_ARTICLES` env var
      is no longer read.
- [ ] Subject and filename gain a `Part N` suffix iff there is more
      than one chunk for that Category in that Run.
- [ ] Each chunk's mark-read call contains only that chunk's
      article ids.
- [ ] A failed chunk does NOT mark its articles read; subsequent
      chunks for the same Category still run.
- [ ] New `tests/test_chunker.py` covers the cases listed above
      with a fake `build_fn`.
- [ ] New integration tests in `test_main.py` cover single-chunk,
      multi-chunk, and chunk-failure paths.
- [ ] `grep -r "max_articles\|MAX_ARTICLES\|halving\|all_fetched" src/ tests/ README.md`
      returns no matches.
- [ ] `pytest` passes.
- [ ] An end-to-end `--dry-run` against a Category with enough
      articles to trigger chunking produces N EPUBs with correct
      Part suffixes, each under the size cap.

## Blocked by

- [#0003 — Raise `MAX_EPUB_MB` default to 15](./0003-raise-max-epub-mb-default.md)
- [#0004 — Per-article pre-flight 14 MB skip](./0004-per-article-preflight-skip.md)
- [#0005 — Add single SMTP retry to mailer](./0005-mailer-smtp-retry-once.md)
