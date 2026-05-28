# 0008 — Add `check-config` script

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user story 21)

## What to build

A standalone CLI command the user runs after editing `.env` to confirm
the file is parseable and that list-valued vars (like
`EXCLUDED_CATEGORIES`) parsed the way they expected.

This is a "verify before deploy" tool. It does not fetch, build, send,
or mark anything. It only loads the config and prints what the
application sees.

### Behavior

Invocation:

```
python -m kindle_daily.check_config
```

(Add a `check-config` subcommand to `run.sh` as well, mirroring the
existing patterns.)

Output on success:

- A formatted dump of every field in `Config`, one per line, with the
  field name, type, and *parsed* value. List-valued fields print as
  their tuple representation so the user can see exactly how each
  comma-separated env var was tokenized.
- Sensitive fields (`gmail_app_password`, `freshrss_api_password`)
  print as `<set, length N>` rather than the raw value.
- Exit code 0.

Output on failure:

- A clear error message naming the offending env var and what was
  wrong with it (missing, wrong type, out of range).
- Exit code non-zero (use 2 to match `cli`'s convention for config
  errors).

Validation layered on top of `load_config`:

- All checks that `load_config` already performs (required vars
  present) are surfaced as-is.
- New checks:
  - `max_epub_mb > 0`
  - `max_age_days > 0`
  - `kindle_email` matches a basic email shape ending in `@kindle.com`
    (the Send-to-Kindle address has a fixed suffix).
  - `gmail_user` matches a basic email shape.
- Validation failures print all problems found (not just the first)
  so the user can fix them in one round-trip.

### Implementation notes

- New file `src/kindle_daily/check_config.py`. Reuses `load_config`.
- The script's `main()` function is testable in isolation (takes an
  optional override for `load_config` or for the env, the same way
  existing tests mock things).
- No new dependencies — `pprint` from the stdlib is enough for the
  output formatting.

### README

- Add a "Verify your config" subsection under "Configuration"
  pointing at the command and showing a sample successful output.

### Tests

`tests/test_check_config.py` (new):

- Happy path: a fully-populated valid `Config` prints all fields and
  returns 0.
- Missing required var: returns non-zero, error message names the
  missing var.
- Invalid `MAX_EPUB_MB` (e.g. `0`, negative, non-integer): returns
  non-zero with a clear message.
- Invalid `KINDLE_EMAIL` shape: returns non-zero with a clear message.
- Multiple errors at once: all of them are reported in one Run.
- Sensitive fields are masked (`gmail_app_password` does not appear
  in plaintext).

## Acceptance criteria

- [ ] `python -m kindle_daily.check_config` exits 0 on a valid `.env`
      and prints every `Config` field with its parsed value.
- [ ] List-valued fields print as tuples so the user can verify
      tokenization.
- [ ] `gmail_app_password` and `freshrss_api_password` are masked.
- [ ] On any validation failure, exits non-zero with a clear
      per-field error message; multiple errors are reported together.
- [ ] `run.sh check-config` (or equivalent shorthand) wraps the
      module call.
- [ ] New `tests/test_check_config.py` covers all the cases listed
      above.
- [ ] README has a "Verify your config" subsection documenting the
      command and showing sample output.
- [ ] `pytest` passes.

## Blocked by

- [#0001 — Remove URL dedup cache and related config](./0001-remove-url-dedup.md)
- [#0002 — Switch age filter from crawl-date to client-side
  published-date](./0002-published-date-age-filter.md)
- [#0003 — Raise `MAX_EPUB_MB` default to 15](./0003-raise-max-epub-mb-default.md)

(Reason: those three change the `Config` schema. Landing #0008 first
means rewriting it.)
