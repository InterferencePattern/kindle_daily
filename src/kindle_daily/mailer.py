"""Gmail SMTP sender for the Send-to-Kindle delivery."""

from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

# One retry on transient Gmail SMTP blips. Mirrors the cadence used by the
# FreshRSS edit-tag retry loop. No exponential backoff — we're not chasing
# an outage, just smoothing one-off failures.
SMTP_MAX_ATTEMPTS = 2
SMTP_RETRY_DELAY_S = 10.0
# Without a timeout a stalled connection blocks the cron job forever.
SMTP_TIMEOUT_S = 60.0


def send(config: Config, epub_path: Path, subject: str) -> None:
    """Email an EPUB to the configured Send-to-Kindle address.

    Parameters
    ----------
    config : Config
        Runtime configuration with Gmail credentials and the Kindle address.
    epub_path : Path
        Path to the EPUB to send.
    subject : str
        Email subject line. Becomes the "title" Amazon shows in the library.

    Raises
    ------
    OSError
        If connecting, logging in or sending fails on every attempt
        (``smtplib.SMTPException`` is a subclass). Caller relies on this to
        gate mark-as-read.
    """
    msg = EmailMessage()
    msg["From"] = config.gmail_user
    msg["To"] = config.kindle_email
    msg["Subject"] = subject
    msg.set_content("Daily Kindle issue attached.")

    payload = epub_path.read_bytes()
    msg.add_attachment(
        payload,
        maintype="application",
        subtype="epub+zip",
        filename=epub_path.name,
    )

    log.info(
        "sending %s (%d bytes) to %s via %s",
        epub_path.name,
        len(payload),
        config.kindle_email,
        SMTP_HOST,
    )
    for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as smtp:
                smtp.login(config.gmail_user, config.gmail_app_password)
                smtp.send_message(msg)
        # OSError covers SMTPException plus DNS, refused-connection, SSL and
        # timeout errors.
        except OSError as exc:
            if attempt == SMTP_MAX_ATTEMPTS:
                raise
            log.warning(
                "SMTP send attempt %d/%d failed (%s: %s); retrying in %.0fs",
                attempt,
                SMTP_MAX_ATTEMPTS,
                type(exc).__name__,
                exc,
                SMTP_RETRY_DELAY_S,
            )
            time.sleep(SMTP_RETRY_DELAY_S)
            continue
        log.info("send-to-kindle email accepted by Gmail")
        return
