# 0005 — Add single SMTP retry to mailer

## Parent

[PRD 0001 — Chunked Inbox Drain](../prd/0001-chunked-inbox-drain.md)
(user story 6)

## What to build

`mailer.send` currently makes one SMTP attempt and propagates any
`smtplib.SMTPException` on the first failure. Under the chunked
pipeline (which can fire several emails per Run on heavy days),
transient Gmail blips would cost a whole chunk needlessly.

Add a single retry inside `mailer.send`:

- First attempt fails with any `smtplib.SMTPException` → log a WARNING
  with the exception class and message, sleep briefly (10s fixed,
  matching the cadence in `freshrss._post_edit_tag`), and try once
  more.
- Second attempt fails → propagate the exception. Caller treats it as
  "Delivery did not happen" and skips mark-read for that chunk.
- Second attempt succeeds → return normally. The two-attempt nature
  is transparent to the caller.
- Both attempts go through a fresh `SMTP_SSL` + `login` cycle — Gmail
  may have terminated the session.

Non-goals:

- No exponential backoff. We're not chasing a Gmail outage; we're
  smoothing over one-off blips.
- No retry on non-SMTP exceptions (e.g. `FileNotFoundError` from the
  EPUB path is a real bug, not transient).

## Acceptance criteria

- [ ] `mailer.send` retries once on `smtplib.SMTPException`.
- [ ] Retry uses a fresh SMTP connection (re-`SMTP_SSL`, re-`login`).
- [ ] Retry has a fixed ~10s delay between attempts.
- [ ] WARNING log on first failure includes exception type and
      message.
- [ ] New test in `test_main.py` (or a new `test_mailer.py`): mock
      `smtplib.SMTP_SSL` so the first send raises `SMTPException` and
      the second succeeds → `send` returns normally and the mock was
      called twice.
- [ ] New test: both attempts raise → exception propagates.
- [ ] Existing tests pass without modification.
- [ ] `pytest` passes.

## Blocked by

None — can start immediately.
