from datetime import datetime, timezone

import pytest

from radar.adapters import rss
from radar.adapters.http import HttpResponse
from radar.domain.models import FetchError
from tests.conftest import FakeHttp, fixture_bytes, ok

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
FEED = "https://feed.test/rss"


def _fetch(body: bytes | HttpResponse | FetchError, config: dict | None = None):
    route = ok(body) if isinstance(body, bytes) else body
    return rss.fetch({"feed_url": FEED, **(config or {})}, FakeHttp({FEED: route}), NOW)


@pytest.mark.parametrize("name", [
    "rss_github_blog.xml", "rss_cloudflare.xml", "rss_huggingface.xml", "atom_simonwillison.xml",
])
def test_real_feeds_yield_two_complete_items(name: str) -> None:
    result = _fetch(fixture_bytes(name))
    assert len(result.items) == 2 and not result.failed_requests
    for item in result.items:
        assert item.title and item.url.startswith("https://")
        assert item.published_at is not None and item.published_at.tzinfo is not None


def test_guid_and_atom_id_become_external_id() -> None:
    assert _fetch(fixture_bytes("rss_github_blog.xml")).items[0].external_id == "https://github.blog/?p=99955"
    assert _fetch(fixture_bytes("rss_cloudflare.xml")).items[0].external_id == "01M6HZ245M2MBZAF7ZQXKC85X6"
    atom_ids = [i.external_id for i in _fetch(fixture_bytes("atom_simonwillison.xml")).items]
    assert atom_ids[0] == "https://simonwillison.net/2026/Nov/4/datasette-llm/"


def test_feed_without_description_gives_empty_excerpt() -> None:
    assert [i.excerpt for i in _fetch(fixture_bytes("rss_huggingface.xml")).items] == ["", ""]


def test_item_without_guid_has_no_external_id_and_html_is_stripped() -> None:
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>No guid</title><link>https://a.test/1</link><description>&lt;p&gt;Hello &amp;amp; bye&lt;/p&gt;</description></item>
    </channel></rss>"""
    item = _fetch(xml).items[0]
    assert item.external_id is None and item.published_at is None
    assert item.excerpt == "Hello & bye"


def test_empty_guid_is_treated_as_missing() -> None:
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>One</title><link>https://a.test/1</link><guid></guid></item>
    <item><title>Two</title><link>https://a.test/2</link><guid></guid></item></channel></rss>"""
    assert [i.external_id for i in _fetch(xml).items] == [None, None]


def test_items_missing_title_or_link_are_skipped() -> None:
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>No link</title></item><item><link>https://a.test/2</link></item>
    <item><title>Fine</title><link>https://a.test/3</link></item></channel></rss>"""
    assert [i.title for i in _fetch(xml).items] == ["Fine"]


def test_limit_caps_items() -> None:
    assert len(_fetch(fixture_bytes("rss_cloudflare.xml"), {"limit": 1}).items) == 1


def test_valid_empty_feed_is_success_with_no_items() -> None:
    result = _fetch(b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>')
    assert result.items == [] and not result.failed_requests


def test_unparseable_body_is_a_parse_failure() -> None:
    result = _fetch(b"<html><body>Not a feed</body></html>")
    assert result.items == [] and [r.error_kind for r in result.failed_requests] == ["parse"]


@pytest.mark.parametrize("response, kind", [
    (HttpResponse(500, b"", {}), "http_status"),
    (FetchError("network", "down"), "network"),
])
def test_request_failures_are_recorded(response: HttpResponse | FetchError, kind: str) -> None:
    result = _fetch(response)
    assert result.items == [] and [r.error_kind for r in result.failed_requests] == [kind]
