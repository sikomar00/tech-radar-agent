from datetime import datetime, timezone

from radar.adapters import github
from radar.adapters.http import HttpResponse
from radar.domain.models import FetchError
from tests.conftest import FakeHttp, fixture_json, ok

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
SEARCH_URL = f"{github.API}/search/repositories?q=created%3A%3E2026-10-29+stars%3A%3E20&sort=stars&order=desc&per_page=30"
RELEASES_URL = f"{github.API}/repos/langchain-ai/langchain/releases?per_page=3"
CONFIG = {"seed_repos": ["langchain-ai/langchain"]}


def _routes() -> dict:
    return {SEARCH_URL: ok(fixture_json("github_search.json")), RELEASES_URL: ok(fixture_json("github_releases.json"))}


def test_fetch_maps_new_repos_and_releases() -> None:
    result = github.fetch(CONFIG, FakeHttp(_routes()), NOW)
    repos = [i for i in result.items if i.metadata["kind"] == "new_repo"]
    releases = [i for i in result.items if i.metadata["kind"] == "release"]
    assert (len(repos), len(releases)) == (2, 2)
    assert len(result.requests) == 2 and not result.failed_requests

    top = repos[0]
    assert (top.external_id, top.title) == ("repo:1440705679", "deepseek-ai/dsh-superagent-prototype")
    assert top.metadata["stars"] == 19001
    assert top.published_at == datetime(2026, 10, 31, 9, 32, 46, tzinfo=timezone.utc)

    core = next(r for r in releases if r.metadata["tag"] == "langchain-core==1.7.6")
    assert core.title == "langchain-ai/langchain langchain-core==1.7.6"
    assert core.external_id == "release:434616524"
    assert core.metadata["prerelease"] is False


def test_search_window_and_threshold_come_from_config() -> None:
    url = f"{github.API}/search/repositories?q=created%3A%3E2026-10-22+stars%3A%3E100&sort=stars&order=desc&per_page=5"
    http = FakeHttp({url: ok({"items": []})})
    github.fetch({"window_days": 14, "min_stars": 100, "per_page": 5}, http, NOW)
    assert http.calls == [url]


def test_rate_limited_search_is_counted_and_releases_still_collected() -> None:
    routes = _routes()
    routes[SEARCH_URL] = HttpResponse(403, b"{}", {"x-ratelimit-remaining": "0"})
    result = github.fetch(CONFIG, FakeHttp(routes), NOW)
    assert [r.error_kind for r in result.failed_requests] == ["rate_limited"]
    assert len(result.items) == 2


def test_missing_repo_is_http_status_failure() -> None:
    routes = _routes()
    routes[RELEASES_URL] = HttpResponse(404, b"{}", {})
    result = github.fetch(CONFIG, FakeHttp(routes), NOW)
    assert [r.error_kind for r in result.failed_requests] == ["http_status"]


def test_unexpected_response_shape_is_a_parse_failure_and_yields_no_items_from_it() -> None:
    routes = _routes()
    routes[SEARCH_URL] = ok({"items": [{"id": 1}]})
    result = github.fetch(CONFIG, FakeHttp(routes), NOW)
    assert [r.error_kind for r in result.failed_requests] == ["parse"]
    assert all(i.metadata["kind"] == "release" for i in result.items)


def test_network_error_is_recorded() -> None:
    routes = _routes()
    routes[SEARCH_URL] = FetchError("timeout", "slow")
    result = github.fetch(CONFIG, FakeHttp(routes), NOW)
    assert [r.error_kind for r in result.failed_requests] == ["timeout"]
