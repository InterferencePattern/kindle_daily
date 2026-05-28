"""Configuration loading and validation."""

from __future__ import annotations

import os
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
        If any required environment variable is missing.
    """
    project_root = project_root or Path.cwd()
    load_dotenv(project_root / ".env", override=False)

    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    out_dir = project_root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    max_epub_mb = int(os.environ.get("MAX_EPUB_MB", "15"))
    max_age_days = int(os.environ.get("MAX_AGE_DAYS", "7"))
    excluded_categories = tuple(
        name.strip() for name in os.environ.get("EXCLUDED_CATEGORIES", "").split(",") if name.strip()
    )

    return Config(
        freshrss_url=os.environ["FRESHRSS_URL"].rstrip("/"),
        freshrss_user=os.environ["FRESHRSS_USER"],
        freshrss_api_password=os.environ["FRESHRSS_API_PASSWORD"],
        gmail_user=os.environ["KINDLE_DAILY_GMAIL_USER"],
        gmail_app_password=os.environ["KINDLE_DAILY_GMAIL_APP_PASSWORD"],
        kindle_email=os.environ["KINDLE_EMAIL"],
        max_epub_bytes=max_epub_mb * 1024 * 1024,
        max_age_days=max_age_days,
        excluded_categories=excluded_categories,
        out_dir=out_dir,
    )
