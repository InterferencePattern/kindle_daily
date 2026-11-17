"""Tests for the FreshRSS GReader API client."""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from kindle_daily.freshrss import FreshRSSClient


@pytest.fixture
def stream_payload() -> dict:
    return {
        "items": [
            {
                "id": "tag:google.com,2005:reader/item/0001",
                "title": "Why the polls moved",
                "published": 1714606800,
                "author": "Anne Applebaum",
                "summary": {"content": "<p>summary</p>"},
                "content": {"content": "<p>full</p>"},
                "categories": [
                    "user/-/state/com.google/reading-list",
                    "user/1000/label/Politics",
                ],
                "origin": {"title": "The Atlantic"},
                "canonical": [{"href": "https://www.theatlantic.com/x"}],
            },
            {
                "id": "tag:google.com,2005:reader/item/0002",
                "title": "Untagged thing",
                "summary": {"content": "<p>s</p>"},
                "categories": ["user/-/state/com.google/reading-list"],
                "origin": {"title": "Some Blog"},
                "canonical": [{"href": "https://blog.example/x"}],
            },
        ]
    }


def test_list_items_unread_parses_items(config, httpx_mock, stream_payload):
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/accounts/ClientLogin",
        text="SID=...\nLSID=...\nAuth=tok123\n",
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "http://freshrss.test/api/greader.php/reader/api/0/stream/contents/"
            "user/-/state/com.google/reading-list"
            "?xt=user%2F-%2Fstate%2Fcom.google%2Fread&n=1000&r=o&output=json"
        ),
        json=stream_payload,
    )

    client = FreshRSSClient(config, client=httpx.Client())
    items = client.list_items(unread_only=True)

    assert len(items) == 2
    first, second = items
    assert first.item_id.endswith("0001")
    assert first.title == "Why the polls moved"
    assert first.feed_title == "The Atlantic"
    assert first.category == "Politics"
    assert first.url == "https://www.theatlantic.com/x"
    assert first.summary_html == "<p>full</p>"  # content preferred over summary
    assert first.author == "Anne Applebaum"
    # uncategorized item falls through with empty category string
    assert second.category == ""


def test_list_items_unread_does_not_send_label_xt_even_when_excludes_configured(
    config, httpx_mock
):
    """FreshRSS reads a single ``xt`` value (PHP ``$_GET['xt']``) and only honours
    ``user/-/state/com.google/{read,unread,starred}`` as the exclude target.
    Sending label-xt does nothing server-side and, worse, overwrites the
    read-state exclude. So with excluded categories configured we must still
    send exactly the one read-state xt — label filtering happens in selector."""
    excluding = dataclasses.replace(config, excluded_categories=("Comics", "Memes"))
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/accounts/ClientLogin",
        text="Auth=tok123\n",
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "http://freshrss.test/api/greader.php/reader/api/0/stream/contents/"
            "user/-/state/com.google/reading-list"
            "?xt=user%2F-%2Fstate%2Fcom.google%2Fread"
            "&n=1000&r=o&output=json"
        ),
        json={"items": []},
    )

    client = FreshRSSClient(excluding, client=httpx.Client())
    client.list_items(unread_only=True)
    # If the URL didn't match (e.g. label-xt was sent), pytest-httpx raises.


def test_list_items_follows_continuation_past_a_page_of_dropped_items(
    config, httpx_mock
):
    """Issue #1: a first page full of items the selector drops (excluded
    category here) must not hide deliverable items on later pages."""
    from kindle_daily import selector

    def raw(i: int, label: str) -> dict:
        return {
            "id": f"tag:google.com,2005:reader/item/{i:04d}",
            "title": f"t{i}",
            "categories": [f"user/1/label/{label}"],
            "origin": {"title": "Feed"},
            "canonical": [{"href": f"https://example.test/{i}"}],
        }

    stream = (
        "http://freshrss.test/api/greader.php/reader/api/0/stream/contents/"
        "user/-/state/com.google/reading-list"
        "?xt=user%2F-%2Fstate%2Fcom.google%2Fread&n=1000&r=o&output=json"
    )
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/accounts/ClientLogin",
        text="Auth=tok123\n",
    )
    httpx_mock.add_response(
        method="GET",
        url=stream,
        json={"items": [raw(i, "Comics") for i in range(3)], "continuation": "p2"},
    )
    httpx_mock.add_response(
        method="GET",
        url=stream + "&c=p2",
        json={"items": [raw(i, "Tech") for i in range(3, 5)]},
    )

    items = FreshRSSClient(config, client=httpx.Client()).list_items(unread_only=True)
    kept = selector.select(items, config).items

    assert len(items) == 5
    assert [it.title for it in kept] == ["t3", "t4"]


def test_mark_read_batches_and_uses_write_token(config, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/accounts/ClientLogin",
        text="Auth=tok123\n",
    )
    httpx_mock.add_response(
        method="GET",
        url="http://freshrss.test/api/greader.php/reader/api/0/token",
        text="WRITE-TOKEN",
    )
    # Two batches: first 50, then 5
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/reader/api/0/edit-tag",
    )
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/reader/api/0/edit-tag",
    )

    ids = [f"tag:item/{i}" for i in range(55)]
    client = FreshRSSClient(config, client=httpx.Client())
    client.mark_read(ids)

    edit_calls = [
        r for r in httpx_mock.get_requests() if r.url.path.endswith("/edit-tag")
    ]
    assert len(edit_calls) == 2
    body = edit_calls[0].read().decode()
    assert "T=WRITE-TOKEN" in body
    assert "a=user%2F-%2Fstate%2Fcom.google%2Fread" in body
    # 50 i= params in the first batch
    assert body.count("i=") == 50


def test_mark_read_noop_for_empty(config, httpx_mock):
    client = FreshRSSClient(config, client=httpx.Client())
    client.mark_read([])  # must not make any HTTP calls
    assert httpx_mock.get_requests() == []


def test_mark_read_retries_on_transient_timeout(config, httpx_mock, monkeypatch):
    # Skip the real backoff sleeps so the test stays fast.
    import kindle_daily.freshrss as freshrss_mod

    monkeypatch.setattr(freshrss_mod.time, "sleep", lambda _s: None)

    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/accounts/ClientLogin",
        text="Auth=tok123\n",
    )
    httpx_mock.add_response(
        method="GET",
        url="http://freshrss.test/api/greader.php/reader/api/0/token",
        text="WRITE-TOKEN",
    )
    # First two edit-tag attempts time out, third succeeds — proves retry.
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(
        method="POST",
        url="http://freshrss.test/api/greader.php/reader/api/0/edit-tag",
    )

    client = FreshRSSClient(config, client=httpx.Client())
    client.mark_read(["tag:item/1"])  # one batch, three POST attempts

    edit_calls = [
        r for r in httpx_mock.get_requests() if r.url.path.endswith("/edit-tag")
    ]
    assert len(edit_calls) == 3
