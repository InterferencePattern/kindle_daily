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
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import Config, validate

_SENSITIVE_FIELDS = frozenset({"gmail_app_password", "freshrss_api_password"})


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
