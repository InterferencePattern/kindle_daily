"""Configuration loading and validation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Runtime configuration loaded from environment / .env file.

    Attributes
    ----------
    freshrss_url : str
        Base URL of the FreshRSS instance, e.g. ``http://nas.local:8080``.
    freshrss_user : str
        FreshRSS username.
    freshrss_api_password : str
        FreshRSS API password (NOT the login password).
    gmail_user : str
        Sending Gmail address. Must be on Amazon's approved-sender list.
    gmail_app_password : str
        16-character Gmail app password.
    kindle_email : str
        Destination ``@kindle.com`` address.
    max_epub_bytes : int
        Cap on built EPUB size, in bytes.
    max_age_days : int
        Only include articles whose published date is within this many days.
    excluded_categories : tuple[str, ...]
        FreshRSS category / label names to exclude server-side via the GReader
        ``xt`` filter. Empty tuple means no exclusions.
    out_dir : Path
        Where to write generated EPUBs.
    """

    freshrss_url: str
    freshrss_user: str
    freshrss_api_password: str
    gmail_user: str
    gmail_app_password: str
    kindle_email: str
    max_epub_bytes: int
    max_age_days: int
    excluded_categories: tuple[str, ...]
    out_dir: Path


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REQUIRED = (
    "FRESHRSS_URL",
    "FRESHRSS_USER",
    "FRESHRSS_API_PASSWORD",
    "KINDLE_DAILY_GMAIL_USER",
    "KINDLE_DAILY_GMAIL_APP_PASSWORD",
    "KINDLE_EMAIL",
)


def load_config(project_root: Path | None = None) -> Config:
    """Load configuration from the environment, with .env auto-loaded.

    Parameters
    ----------
    project_root : Path, optional
        Project root used to locate ``.env`` and the default ``out/`` dir.
        Defaults to the current working directory.

    Returns
    -------
    Config
        Validated configuration.

    Raises
    ------
    RuntimeError
        If any variable is missing or invalid; the message lists every problem.
    """
    project_root = project_root or Path.cwd()
    load_dotenv(project_root / ".env", override=False)

    config, errors = validate(os.environ, project_root)
    if errors:
        raise RuntimeError(
            "Config has problems:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    config.out_dir.mkdir(parents=True, exist_ok=True)
    return config


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
    # Consumed by run.sh, not Config; validated here so check-config catches it.
    _parse_positive_int(env, "RETENTION_DAYS", default="30", errors=errors)

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
        out_dir=project_root / "out",
    )
    return config, []


def _parse_positive_int(
    env: Mapping[str, str], name: str, *, default: str, errors: list[str]
) -> int:
    """Parse ``env[name]`` as a positive int, appending to ``errors`` on failure."""
    raw = env.get(name, default)
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name}: {raw!r} is not an integer")
        return 0
    if value <= 0:
        errors.append(f"{name}: {value} must be > 0")
    return value
