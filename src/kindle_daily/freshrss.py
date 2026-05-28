"""FreshRSS Google Reader-compatible API client.

FreshRSS exposes a Google Reader API at ``${FRESHRSS_URL}/api/greader.php``.
This module wraps the three endpoints kindle_daily needs: ``ClientLogin``,
``stream/contents``, and ``edit-tag`` (for mark-as-read).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .config import Config

log = logging.getLogger(__name__)

READING_LIST = "user/-/state/com.google/reading-list"
READ_STATE = "user/-/state/com.google/read"

# Mark-as-read is sent with one ``i=<id>`` per item; FreshRSS accepts large
# batches but we keep them tidy.
EDIT_TAG_BATCH = 50

# edit-tag can be slow on a busy FreshRSS host. The default httpx 30s read
# timeout has bitten us, so we use a longer per-request timeout here and
# retry on transient errors. Marking already-read items as read is a no-op
# server-side, so retries are safe.
MARK_READ_TIMEOUT_S = 60.0
MARK_READ_MAX_ATTEMPTS = 3
MARK_READ_BACKOFF_S = 2.0


@dataclass(frozen=True)
class FeedItem:
    """One unread item as returned by FreshRSS.

    Attributes
    ----------
    item_id : str
        The greader-style item id (used to mark read).
    title : str
        Article title.
    url : str
        Original article URL.
    feed_title : str
        Source feed name (e.g. "The Atlantic").
    category : str
        FreshRSS category / label (e.g. "Politics"). Empty string if uncategorized.
    summary_html : str
        Whatever HTML the feed itself published. Used as a fallback when
        full-text extraction fails.
    published_ms : int
        Publication time in epoch milliseconds. ``0`` if unknown.
    author : str
        Author from the feed, if any.
    """

    item_id: str
    title: str
    url: str
    feed_title: str
    category: str
    summary_html: str
    published_ms: int
    author: str


class FreshRSSClient:
    """Thin client for the GReader API exposed by FreshRSS."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        self._config = config
        # Split timeout: fail fast on connect, generous on read. The unread
        # stream can be briefly slow to serialize when FreshRSS is busy
        # crawling feeds, but if the server is genuinely down we want to
        # know within a few seconds.
        self._client = client or httpx.Client(timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0))
        self._auth_token: str | None = None
        self._write_token: str | None = None

    # ------------------------------------------------------------------ auth

    def _login(self) -> str:
        """Run ``ClientLogin`` and cache the auth token.

        Returns
        -------
        str
            The auth token to use in the ``Authorization: GoogleLogin auth=...``
            header for subsequent requests.
        """
        if self._auth_token:
            return self._auth_token

        url = f"{self._config.freshrss_url}/api/greader.php/accounts/ClientLogin"
        body = urlencode(
            {
                "Email": self._config.freshrss_user,
                "Passwd": self._config.freshrss_api_password,
            }
        )
        resp = self._client.post(
            url,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        # response body is key=value lines
        for line in resp.text.splitlines():
            if line.startswith("Auth="):
                self._auth_token = line[len("Auth=") :]
                return self._auth_token
        raise RuntimeError("FreshRSS ClientLogin did not return Auth token")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"GoogleLogin auth={self._login()}"}

    def _get_write_token(self) -> str:
        """Fetch the write token required by ``edit-tag``."""
        if self._write_token:
            return self._write_token
        url = f"{self._config.freshrss_url}/api/greader.php/reader/api/0/token"
        resp = self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        self._write_token = resp.text.strip()
        return self._write_token

    # ------------------------------------------------------------------ reads

    def list_unread(self, limit: int) -> list[FeedItem]:
        """List unread items, oldest first.

        Parameters
        ----------
        limit : int
            Maximum number of items to return.

        Returns
        -------
        list[FeedItem]
            Unread items, sorted by publication time ascending so daily issues
            drain backlogs in chronological order.
        """
        return self._list(limit=limit, unread_only=True)

    def list_recent(self, limit: int) -> list[FeedItem]:
        """List items from the reading-list, regardless of read/unread state.

        Same shape as :meth:`list_unread` but skips the ``xt=read`` filter.
        Used for size-comparison tests where read items need to be included
        for an apples-to-apples view.
        """
        return self._list(limit=limit, unread_only=False)

    def _list(self, limit: int, unread_only: bool) -> list[FeedItem]:
        url = f"{self._config.freshrss_url}/api/greader.php" f"/reader/api/0/stream/contents/{READING_LIST}"
        # FreshRSS's GReader implementation only reads a single ``xt`` param
        # (``$_GET['xt']`` — last value wins under repeated keys) and only
        # honours ``user/-/state/com.google/{read,unread,starred}`` as the
        # exclude target. Label exclusion (``xt=user/-/label/X``) is a no-op
        # server-side and, worse, sending it as an additional ``xt`` would
        # overwrite the read-state exclude. Selector handles label exclusion
        # client-side; here we send at most the one ``xt`` the server supports.
        params: list[tuple[str, str]] = []
        if unread_only:
            params.append(("xt", READ_STATE))
        params.append(("n", str(limit)))
        params.append(("r", "o"))  # oldest first
        params.append(("output", "json"))
        resp = self._client.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        payload = resp.json()
        items: list[FeedItem] = []
        for raw in payload.get("items", []):
            items.append(_parse_item(raw))
        log.info(
            "FreshRSS returned %d items (unread_only=%s, limit=%d)",
            len(items),
            unread_only,
            limit,
        )
        return items

    # ----------------------------------------------------------------- writes

    def mark_read(self, item_ids: list[str]) -> None:
        """Mark the given item ids as read.

        Retries each batch up to :data:`MARK_READ_MAX_ATTEMPTS` times on
        transient errors (timeouts, network blips, 5xx responses). Marking
        already-read items as read is a no-op on FreshRSS, so retries can't
        cause duplicates or corruption — a ``ReadTimeout`` after the request
        body has been delivered very likely means the server processed the
        edit anyway, and the retry just confirms it.

        Parameters
        ----------
        item_ids : list[str]
            Greader-style item ids (as returned in ``FeedItem.item_id``).
        """
        if not item_ids:
            return
        url = f"{self._config.freshrss_url}/api/greader.php/reader/api/0/edit-tag"
        token = self._get_write_token()
        for start in range(0, len(item_ids), EDIT_TAG_BATCH):
            batch = item_ids[start : start + EDIT_TAG_BATCH]
            body = urlencode([("a", READ_STATE), ("T", token)] + [("i", iid) for iid in batch])
            headers = {
                **self._headers(),
                "Content-Type": "application/x-www-form-urlencoded",
            }
            self._post_edit_tag(url, headers=headers, body=body, batch_size=len(batch))

    def _post_edit_tag(self, url: str, *, headers: dict[str, str], body: str, batch_size: int) -> None:
        """POST one edit-tag batch with retry+backoff on transient failures."""
        for attempt in range(1, MARK_READ_MAX_ATTEMPTS + 1):
            try:
                resp = self._client.post(url, headers=headers, content=body, timeout=MARK_READ_TIMEOUT_S)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == MARK_READ_MAX_ATTEMPTS:
                    raise
                wait_s = MARK_READ_BACKOFF_S * (2 ** (attempt - 1))
                log.warning(
                    "mark_read attempt %d/%d failed (%s: %s); retrying in %.1fs",
                    attempt,
                    MARK_READ_MAX_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
                continue

            # Retry 5xx; let 4xx bubble up immediately via raise_for_status.
            if resp.status_code >= 500 and attempt < MARK_READ_MAX_ATTEMPTS:
                wait_s = MARK_READ_BACKOFF_S * (2 ** (attempt - 1))
                log.warning(
                    "mark_read attempt %d/%d returned %d; retrying in %.1fs",
                    attempt,
                    MARK_READ_MAX_ATTEMPTS,
                    resp.status_code,
                    wait_s,
                )
                time.sleep(wait_s)
                continue

            resp.raise_for_status()
            log.info("Marked %d items as read", batch_size)
            return


def _parse_item(raw: dict) -> FeedItem:
    """Translate one greader item dict into a :class:`FeedItem`."""
    origin = raw.get("origin", {}) or {}
    summary = (raw.get("summary") or {}).get("content") or ""
    content = (raw.get("content") or {}).get("content") or ""
    # Prefer richer "content" field if present, else "summary".
    body = content or summary

    # FreshRSS exposes categories as ``user/<n>/label/<Name>`` strings.
    category = ""
    for label in raw.get("categories", []) or []:
        if isinstance(label, str) and "/label/" in label:
            category = label.rsplit("/label/", 1)[-1]
            break

    canonical = raw.get("canonical") or raw.get("alternate") or []
    url = canonical[0]["href"] if canonical else ""

    return FeedItem(
        item_id=raw.get("id", ""),
        title=raw.get("title", "(untitled)"),
        url=url,
        feed_title=origin.get("title", ""),
        category=category,
        summary_html=body,
        published_ms=int(raw.get("published", 0)) * 1000 if raw.get("published") else 0,
        author=raw.get("author", "") or "",
    )
