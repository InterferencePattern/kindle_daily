"""Logging configuration for kindle_daily."""

from __future__ import annotations

import logging
import sys


def configure(level: int = logging.INFO) -> None:
    """Configure root logging once.

    Parameters
    ----------
    level : int
        Logging level. Defaults to ``logging.INFO``.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)
    # quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)
    # trafilatura.downloads logs every non-200 (paywalls, bot blocks) at ERROR
    # — we already handle those cases by falling back to the FreshRSS summary,
    # so the noise is just noise.
    logging.getLogger("trafilatura.downloads").setLevel(logging.CRITICAL)
