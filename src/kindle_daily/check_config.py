"""Pre-flight validator for the user's ``.env``.

Run after editing ``.env`` to confirm:

* every required var is present and parseable,
* numeric caps are positive integers,
* ``KINDLE_EMAIL`` is an ``@kindle.com`` address and ``GMAIL_USER`` is an
  email-shaped string,
* list-valued vars (``EXCLUDED_CATEGORIES``) tokenized the way the user
  expected.

Does not fetch, build, send, or mark anything. Prints what the rest of the
application will see, with the two secrets (``gmail_app_password``,
``freshrss_api_password``) masked.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from .config import Config

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SENSITIVE_FIELDS = frozenset({"gmail_app_password", "freshrss_api_password"})
_REQUIRED = (
    "FRESHRSS_URL",
    "FRESHRSS_USER",
    "FRESHRSS_API_PASSWORD",
    "KINDLE_DAILY_GMAIL_USER",
    "KINDLE_DAILY_GMAIL_APP_PASSWORD",
    "KINDLE_EMAIL",
)


def validate(
    env: Mapping[str, str], project_root: Path
) -> tuple[Config | None, list[str]]:
    """Parse and validate ``env`` into a :class:`Config`.

    Parameters
    ----------
    env : Mapping[str, str]
        Environment to read from (e.g. ``os.environ``). Already-loaded
        ``.env`` values are expected to be present in this mapping.
    project_root : Path
        Project root used to derive ``out_dir``.

    Returns
    -------
    (Config | None, list[str])
        On success, ``(config, [])``. On any validation failure,
        ``(None, [error_messages...])`` with every problem found — never
        just the first — so the user can fix them in one round-trip.
    """
    errors: list[str] = []

    missing = [name for name in _REQUIRED if not env.get(name)]
    for name in missing:
        errors.append(f"{name}: missing (required)")

    max_epub_mb = _parse_positive_int(env, "MAX_EPUB_MB", default="15", errors=errors)
    max_age_days = _parse_positive_int(env, "MAX_AGE_DAYS", default="7", errors=errors)

    kindle_email = env.get("KINDLE_EMAIL", "")
    if kindle_email and not _EMAIL_RE.match(kindle_email):
        errors.append(f"KINDLE_EMAIL: {kindle_email!r} doesn't look like an email")
    elif kindle_email and not kindle_email.endswith("@kindle.com"):
        errors.append(
            f"KINDLE_EMAIL: {kindle_email!r} must end in @kindle.com "
            "(the Send-to-Kindle address suffix)"
        )

    gmail_user = env.get("KINDLE_DAILY_GMAIL_USER", "")
    if gmail_user and not _EMAIL_RE.match(gmail_user):
        errors.append(
            f"KINDLE_DAILY_GMAIL_USER: {gmail_user!r} doesn't look like an email"
        )

    if errors:
        return None, errors

    excluded_categories = tuple(
        name.strip()
        for name in env.get("EXCLUDED_CATEGORIES", "").split(",")
        if name.strip()
    )

    out_dir = project_root / "out"
    config = Config(
        freshrss_url=env["FRESHRSS_URL"].rstrip("/"),
        freshrss_user=env["FRESHRSS_USER"],
        freshrss_api_password=env["FRESHRSS_API_PASSWORD"],
        gmail_user=env["KINDLE_DAILY_GMAIL_USER"],
        gmail_app_password=env["KINDLE_DAILY_GMAIL_APP_PASSWORD"],
        kindle_email=env["KINDLE_EMAIL"],
        max_epub_bytes=max_epub_mb * 1024 * 1024,
        max_age_days=max_age_days,
        excluded_categories=excluded_categories,
        out_dir=out_dir,
    )
    return config, []


def _parse_positive_int(
    env: Mapping[str, str], name: str, *, default: str, errors: list[str]
) -> int:
    raw = env.get(name, default)
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name}: {raw!r} is not an integer")
        return 0
    if value <= 0:
        errors.append(f"{name}: {value} must be > 0")
    return value


def format_config(config: Config) -> str:
    """Render a Config as a one-field-per-line table for human review."""
    lines = ["Config:"]
    for field in dataclasses.fields(config):
        value = getattr(config, field.name)
        if field.name in _SENSITIVE_FIELDS:
            rendered = f"<set, length {len(value)}>"
        else:
            rendered = repr(value)
        lines.append(f"  {field.name} ({field.type}) = {rendered}")
    return "\n".join(lines)


def main(
    project_root: Path | None = None,
    *,
    out: object = sys.stdout,
    err: object = sys.stderr,
) -> int:
    """CLI entry point. Returns a process exit code (``0`` ok, ``2`` invalid)."""
    project_root = project_root or Path.cwd()
    load_dotenv(project_root / ".env", override=False)

    config, errors = validate(os.environ, project_root)
    if errors:
        print("Config has problems:", file=err)
        for msg in errors:
            print(f"  - {msg}", file=err)
        return 2

    print(format_config(config), file=out)
    print("OK — config parses cleanly.", file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
