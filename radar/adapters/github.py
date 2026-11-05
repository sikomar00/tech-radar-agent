from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from radar.adapters.http import HttpClient, get_json, mark_parse_failure
from radar.domain.cleaning import EXCERPT_LIMIT, TITLE_LIMIT, clean_text
from radar.domain.models import FetchResult, RawItem

API = "https://api.github.com"


def fetch(config: dict[str, Any], http: HttpClient, now: datetime) -> FetchResult:
    result = FetchResult()
    since = (now - timedelta(days=config.get("window_days", 7))).date().isoformat()
    query = urlencode({
        "q": f"created:>{since} stars:>{config.get('min_stars', 20)}",
        "sort": "stars", "order": "desc", "per_page": config.get("per_page", 30),
    })
    data = get_json(http, f"{API}/search/repositories?{query}", result.requests)
    _add_items(result, data, lambda d: [_repo_item(r) for r in d["items"]])
    per_repo = config.get("releases_per_repo", 3)
    for repo in config.get("seed_repos", []):
        data = get_json(http, f"{API}/repos/{repo}/releases?per_page={per_repo}", result.requests)
        _add_items(result, data, lambda d, repo=repo: [_release_item(repo, r) for r in d])
    return result


def _add_items(result: FetchResult, data: Any, to_items: Any) -> None:
    """응답 하나를 항목 목록으로 바꿔 더한다. 해석하지 못하면 그 요청을 parse 실패로 센다."""
    if data is None:
        return
    try:
        result.items.extend(to_items(data))
    except (KeyError, TypeError, ValueError) as err:
        mark_parse_failure(result.requests, repr(err))


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _repo_item(repo: dict[str, Any]) -> RawItem:
    # 저장소 id와 릴리스 id는 GitHub에서 서로 다른 번호 공간이라, 접두사로 구분해야 같은 숫자가 한 행으로 합쳐지지 않는다
    return RawItem(
        external_id=f"repo:{repo['id']}",
        url=repo["html_url"],
        title=clean_text(repo["full_name"], TITLE_LIMIT),
        excerpt=clean_text(repo.get("description"), EXCERPT_LIMIT),
        published_at=_parse_time(repo.get("created_at")),
        metadata={
            "kind": "new_repo", "stars": repo["stargazers_count"], "language": repo.get("language"),
            "topics": repo.get("topics", []), "archived": repo["archived"], "fork": repo["fork"],
            "pushed_at": repo.get("pushed_at"),
        },
    )


def _release_item(repo: str, release: dict[str, Any]) -> RawItem:
    return RawItem(
        external_id=f"release:{release['id']}",
        url=release["html_url"],
        title=clean_text(f"{repo} {release['tag_name']}", TITLE_LIMIT),
        excerpt=clean_text(release.get("body"), EXCERPT_LIMIT),
        published_at=_parse_time(release.get("published_at")),
        metadata={"kind": "release", "repo": repo, "tag": release["tag_name"], "prerelease": release["prerelease"]},
    )
