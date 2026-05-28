# 0003 — Raise `MAX_EPUB_MB` default to 15

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user story 10)

## What to build

Change the default for `MAX_EPUB_MB` from `10` to `15`. Gmail's
outbound SMTP cap is 25 MB on the base64-encoded message, which works
out to ~18 MB of raw file. 15 MB leaves comfortable headroom for the
encoding overhead and EPUB scaffolding.

Scope:

- Change the default in `load_config`.
- Update the README config table to reflect the new default.
- Update the `Config` fixture in `tests/conftest.py` if it has a
  hardcoded `10 * 1024 * 1024` (it does — change to `15 * 1024 * 1024`).
- No other behavioral change. This is a default bump, not a new
  feature.

## Acceptance criteria

- [ ] `MAX_EPUB_MB` default is `15` in `load_config`.
- [ ] README config table shows `15` as the default and explains the
      Gmail/base64 reasoning briefly.
- [ ] `tests/conftest.py` fixture uses `15 * 1024 * 1024`.
- [ ] `pytest` passes.
- [ ] An end-to-end `--dry-run` produces EPUBs that may now contain
      more articles per Category than before (no halving until the
      EPUB exceeds 15 MB instead of 10 MB).

## Blocked by

None — can start immediately.
