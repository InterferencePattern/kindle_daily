"""Tests for the Gmail SMTP sender."""

from __future__ import annotations

import logging
import smtplib
import socket
from pathlib import Path

import pytest

from kindle_daily import mailer


@pytest.fixture
def epub(tmp_path: Path) -> Path:
    path = tmp_path / "issue.epub"
    path.write_bytes(b"epub-bytes")
    return path


class _FakeSMTP:
    """Stand-in for smtplib.SMTP_SSL — records calls and optionally fails."""

    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.fail_on_send = fail_on_send
        self.logins: list[tuple[str, str]] = []
        self.sent: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))

    def send_message(self, msg) -> None:
        if self.fail_on_send:
            raise smtplib.SMTPException("simulated transient failure")
        self.sent.append(msg)


def test_send_succeeds_on_first_attempt(monkeypatch, config, epub):
    instances: list[_FakeSMTP] = []

    def factory(host, port, timeout):
        instance = _FakeSMTP()
        instances.append(instance)
        return instance

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)

    mailer.send(config, epub, subject="Test subject")

    assert len(instances) == 1
    assert len(instances[0].sent) == 1


def test_send_retries_once_on_transient_smtp_failure(monkeypatch, config, epub, caplog):
    """First send_message raises SMTPException; the second succeeds."""
    instances: list[_FakeSMTP] = []

    def factory(host, port, timeout):
        # First attempt fails, second succeeds.
        instance = _FakeSMTP(fail_on_send=(len(instances) == 0))
        instances.append(instance)
        return instance

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="kindle_daily.mailer"):
        mailer.send(config, epub, subject="Retry me")

    assert len(instances) == 2
    # Each attempt opens a fresh connection and re-logs in.
    assert len(instances[0].logins) == 1
    assert len(instances[1].logins) == 1
    # Only the second attempt actually delivered.
    assert instances[0].sent == []
    assert len(instances[1].sent) == 1
    # The warning mentions the exception class and message.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "SMTPException" in msg
    assert "simulated transient failure" in msg


def test_send_propagates_when_both_attempts_fail(monkeypatch, config, epub):
    instances: list[_FakeSMTP] = []

    def factory(host, port, timeout):
        instance = _FakeSMTP(fail_on_send=True)
        instances.append(instance)
        return instance

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)

    with pytest.raises(smtplib.SMTPException):
        mailer.send(config, epub, subject="Both fail")

    assert len(instances) == mailer.SMTP_MAX_ATTEMPTS


def test_send_retries_on_connect_oserror(monkeypatch, config, epub):
    """DNS failures raise socket.gaierror (OSError, not SMTPException)."""
    instances: list[_FakeSMTP] = []
    timeouts: list[float] = []

    def factory(host, port, timeout):
        timeouts.append(timeout)
        if not timeouts[1:]:
            raise socket.gaierror("simulated DNS failure")
        instance = _FakeSMTP()
        instances.append(instance)
        return instance

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(mailer.time, "sleep", lambda _s: None)

    mailer.send(config, epub, subject="DNS blip")

    assert timeouts == [mailer.SMTP_TIMEOUT_S] * 2
    assert len(instances[0].sent) == 1
