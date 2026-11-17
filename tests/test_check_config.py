"""Tests for the pre-flight env validator."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from kindle_daily import check_config


def _valid_env() -> dict[str, str]:
    return {
        "FRESHRSS_URL": "http://nas.local:8080",
        "FRESHRSS_USER": "alice",
        "FRESHRSS_API_PASSWORD": "api-pw",
        "KINDLE_DAILY_GMAIL_USER": "alice@gmail.com",
        "KINDLE_DAILY_GMAIL_APP_PASSWORD": "abcdefghijklmnop",
        "KINDLE_EMAIL": "alice_abc123@kindle.com",
        "MAX_EPUB_MB": "15",
        "MAX_AGE_DAYS": "7",
        "EXCLUDED_CATEGORIES": "Comics,Memes",
    }


def test_valid_env_returns_config_and_no_errors(tmp_path: Path):
    cfg, errors = check_config.validate(_valid_env(), project_root=tmp_path)
    assert errors == []
    assert cfg is not None
    assert cfg.kindle_email == "alice_abc123@kindle.com"
    assert cfg.excluded_categories == ("Comics", "Memes")
    assert cfg.max_epub_bytes == 15 * 1024 * 1024
    assert cfg.max_age_days == 7


def test_missing_required_var_is_reported(tmp_path: Path):
    env = _valid_env()
    del env["FRESHRSS_URL"]
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("FRESHRSS_URL" in e and "missing" in e for e in errors)


def test_non_integer_max_epub_mb_is_reported(tmp_path: Path):
    env = _valid_env()
    env["MAX_EPUB_MB"] = "fifteen"
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("MAX_EPUB_MB" in e and "not an integer" in e for e in errors)


@pytest.mark.parametrize("value", ["0", "-1", "-100"])
def test_non_positive_max_epub_mb_is_reported(tmp_path: Path, value: str):
    env = _valid_env()
    env["MAX_EPUB_MB"] = value
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("MAX_EPUB_MB" in e and "> 0" in e for e in errors)


def test_non_positive_max_age_days_is_reported(tmp_path: Path):
    env = _valid_env()
    env["MAX_AGE_DAYS"] = "0"
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("MAX_AGE_DAYS" in e and "> 0" in e for e in errors)


def test_non_positive_retention_days_is_reported(tmp_path: Path):
    env = _valid_env()
    env["RETENTION_DAYS"] = "0"
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("RETENTION_DAYS" in e and "> 0" in e for e in errors)


def test_invalid_kindle_email_is_reported(tmp_path: Path):
    env = _valid_env()
    env["KINDLE_EMAIL"] = "alice@gmail.com"  # wrong suffix
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("KINDLE_EMAIL" in e and "@kindle.com" in e for e in errors)


def test_malformed_kindle_email_is_reported(tmp_path: Path):
    env = _valid_env()
    env["KINDLE_EMAIL"] = "not-an-email"
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("KINDLE_EMAIL" in e and "email" in e for e in errors)


def test_invalid_gmail_user_is_reported(tmp_path: Path):
    env = _valid_env()
    env["KINDLE_DAILY_GMAIL_USER"] = "definitely-not-an-email"
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    assert any("KINDLE_DAILY_GMAIL_USER" in e and "email" in e for e in errors)


def test_multiple_errors_are_all_reported(tmp_path: Path):
    """A user with several problems should see them all in one run."""
    env = _valid_env()
    env["MAX_EPUB_MB"] = "0"
    env["KINDLE_EMAIL"] = "wrong@gmail.com"
    del env["FRESHRSS_API_PASSWORD"]
    cfg, errors = check_config.validate(env, project_root=tmp_path)
    assert cfg is None
    joined = "\n".join(errors)
    assert "MAX_EPUB_MB" in joined
    assert "KINDLE_EMAIL" in joined
    assert "FRESHRSS_API_PASSWORD" in joined


def test_format_config_masks_sensitive_fields(tmp_path: Path):
    cfg, _ = check_config.validate(_valid_env(), project_root=tmp_path)
    rendered = check_config.format_config(cfg)
    # Passwords never appear in plaintext.
    assert "abcdefghijklmnop" not in rendered
    assert "api-pw" not in rendered
    # But the length-based masked form does.
    assert "<set, length 16>" in rendered
    assert "<set, length 6>" in rendered
    # Non-sensitive fields print normally.
    assert "alice@gmail.com" in rendered
    assert "('Comics', 'Memes')" in rendered


def test_main_returns_zero_and_prints_config_on_valid_env(monkeypatch, tmp_path: Path):
    # Wipe the real environment so nothing leaks in.
    monkeypatch.setattr(check_config, "os", _StubOS(_valid_env()))
    # Skip .env loading entirely.
    monkeypatch.setattr(check_config, "load_dotenv", lambda _path, override=False: None)

    out = io.StringIO()
    err = io.StringIO()
    rc = check_config.main(project_root=tmp_path, out=out, err=err)

    assert rc == 0
    assert "OK" in out.getvalue()
    assert "alice_abc123@kindle.com" in out.getvalue()
    assert err.getvalue() == ""


def test_main_returns_two_and_prints_errors_on_invalid_env(monkeypatch, tmp_path: Path):
    env = _valid_env()
    env["MAX_EPUB_MB"] = "0"
    monkeypatch.setattr(check_config, "os", _StubOS(env))
    monkeypatch.setattr(check_config, "load_dotenv", lambda _path, override=False: None)

    out = io.StringIO()
    err = io.StringIO()
    rc = check_config.main(project_root=tmp_path, out=out, err=err)

    assert rc == 2
    assert "MAX_EPUB_MB" in err.getvalue()
    assert out.getvalue() == ""


class _StubOS:
    """Minimal stand-in for the os module that check_config uses."""

    def __init__(self, env: dict[str, str]) -> None:
        self.environ = env
