from datetime import datetime, timezone

from radar.adapters import hackernews
from radar.adapters.http import HttpResponse
from radar.domain.models import FetchError
from tests.conftest import FakeHttp, fixture_json, ok

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
BASE = hackernews.BASE
IDS = fixture_json("hn_topstories.json")


def _routes() -> dict:
    routes = {f"{BASE}/topstories.json": ok(IDS), f"{BASE}/showstories.json": ok([IDS[0], 111])}
    routes.update({f"{BASE}/item/{i}.json": ok(fixture_json(f"hn_item_{i}.json")) for i in IDS})
    return routes


def test_fetch_maps_real_stories_and_counts_every_request() -> None:
    http = FakeHttp(_routes())
    result = hackernews.fetch({"lists": ["topstories"], "limit": 3}, http, NOW)
    assert len(result.items) == 3
    assert len(result.requests) == 4 and not result.failed_requests
    first = next(i for i in result.items if i.external_id == "50371246")
    assert first.title == "Residential proxies detected on White House network"
    assert first.url == "https://layer3intel.com/blog/residential-proxies-white-house-network"
    assert first.published_at == datetime.fromtimestamp(1793825118, tz=timezone.utc)
    assert first.metadata["score"] == 967


def test_story_seen_in_two_lists_is_fetched_once() -> None:
    routes = _routes()
    routes[f"{BASE}/item/111.json"] = ok({"id": 111, "type": "story", "title": "Only in show"})
    http = FakeHttp(routes)
    result = hackernews.fetch({"limit": 3}, http, NOW)
    assert sum(url.endswith(f"/item/{IDS[0]}.json") for url in http.calls) == 1
    assert {i.external_id for i in result.items} == {str(i) for i in IDS} | {"111"}


def test_story_without_url_uses_hn_item_page() -> None:
    routes = {f"{BASE}/topstories.json": ok([7]), f"{BASE}/item/7.json": ok({"id": 7, "title": "Ask HN: x", "text": "<p>hi</p>"})}
    item = hackernews.fetch({"lists": ["topstories"]}, FakeHttp(routes), NOW).items[0]
    assert item.url == "https://news.ycombinator.com/item?id=7"
    assert item.excerpt == "hi"


def test_title_entities_are_decoded_but_plain_angle_brackets_survive() -> None:
    routes = {f"{BASE}/topstories.json": ok([5]), f"{BASE}/item/5.json": ok({"id": 5, "title": "AT&amp;T: Vec<T> &lt;3"})}
    item = hackernews.fetch({"lists": ["topstories"]}, FakeHttp(routes), NOW).items[0]
    assert item.title == "AT&T: Vec<T> <3"


def test_dead_deleted_and_null_stories_are_skipped_without_failure() -> None:
    routes = {
        f"{BASE}/topstories.json": ok([1, 2, 3]),
        f"{BASE}/item/1.json": ok({"id": 1, "dead": True, "title": "x"}),
        f"{BASE}/item/2.json": ok({"id": 2, "deleted": True}),
        f"{BASE}/item/3.json": ok("null"),
    }
    result = hackernews.fetch({"lists": ["topstories"]}, FakeHttp(routes), NOW)
    assert result.items == [] and not result.failed_requests


def test_one_failing_item_request_is_counted_and_others_survive() -> None:
    routes = _routes()
    routes[f"{BASE}/item/{IDS[1]}.json"] = HttpResponse(500, b"", {})
    result = hackernews.fetch({"lists": ["topstories"], "limit": 3}, FakeHttp(routes), NOW)
    assert len(result.items) == 2
    assert [r.error_kind for r in result.failed_requests] == ["http_status"]


def test_list_request_failure_yields_no_items() -> None:
    routes = {f"{BASE}/topstories.json": FetchError("network", "unreachable")}
    result = hackernews.fetch({"lists": ["topstories"]}, FakeHttp(routes), NOW)
    assert result.items == [] and [r.error_kind for r in result.failed_requests] == ["network"]


def test_list_that_is_not_an_array_is_a_parse_failure() -> None:
    routes = {f"{BASE}/topstories.json": ok({"oops": 1})}
    result = hackernews.fetch({"lists": ["topstories"]}, FakeHttp(routes), NOW)
    assert [r.error_kind for r in result.failed_requests] == ["parse"]
