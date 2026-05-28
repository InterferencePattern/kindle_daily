"""Item selection: a pure-function seam over the per-run filter rules.

`select` applies every filter the daily run cares about — excluded categories,
published-date window — and returns the surviving items plus per-filter drop
counts. No I/O, no logging, no globals; the caller decides how to surface the
drops and what to do next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Config
from .freshrss import FeedItem


@dataclass(frozen=True)
class SelectResult:
    """Outcome of one `select` call.

    Attributes
    ----------
    items : list[FeedItem]
        Items that survived every filter, in input order.
    dropped_excluded_category : int
        Count of items dropped because their category was in
        ``config.excluded_categories``.
    dropped_too_old : int
        Count of items dropped because their published date was older than
        the ``config.max_age_days`` cutoff.
    no_timestamp : int
        Count of items whose ``published_ms`` was ``0`` and were therefore
        kept by the age filter. Useful for the caller to surface a warning;
        not a drop count.
    """

    items: list[FeedItem]
    dropped_excluded_category: int
    dropped_too_old: int
    no_timestamp: int


def select(items: list[FeedItem], config: Config) -> SelectResult:
    """Apply every per-run filter in order and report what survived."""
    excluded = set(config.excluded_categories)
    cutoff_ms = (int(time.time()) - config.max_age_days * 86400) * 1000

    kept: list[FeedItem] = []
    dropped_excluded = 0
    dropped_old = 0
    no_timestamp = 0
    for item in items:
        if item.category in excluded:
            dropped_excluded += 1
            continue
        if item.published_ms == 0:
            no_timestamp += 1
            kept.append(item)
            continue
        if item.published_ms < cutoff_ms:
            dropped_old += 1
            continue
        kept.append(item)

    return SelectResult(
        items=kept,
        dropped_excluded_category=dropped_excluded,
        dropped_too_old=dropped_old,
        no_timestamp=no_timestamp,
    )
