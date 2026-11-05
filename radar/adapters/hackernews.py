import html
from datetime import datetime, timezone
from typing import Any

from radar.adapters.http import HttpClient, get_json, mark_parse_failure
from radar.domain.cleaning import EXCERPT_LIMIT, TITLE_LIMIT, clean_text
from radar.domain.models import FetchResult, RawItem

BASE = "https://hacker-news.firebaseio.com/v0"


def fetch(config: dict[str, Any], http: HttpClient, now: datetime) -> FetchResult:
    result = FetchResult()
    seen: set[int] = set()
    for name in config.get("lists", ["topstories", "showstories"]):
        ids = get_json(http, f"{BASE}/{name}.json", result.requests)
        if ids is not None and not isinstance(ids, list):
            mark_parse_failure(result.requests, f"{name}가 목록이 아닙니다")
            continue
        for story_id in (ids or [])[: config.get("limit", 15)]:
            if story_id in seen:
                continue
            seen.add(story_id)
            story = get_json(http, f"{BASE}/item/{story_id}.json", result.requests)
            item = _story_item(story) if isinstance(story, dict) else None
            if item:
                result.items.append(item)
    return result


def _story_item(story: dict[str, Any]) -> RawItem | None:
    """삭제되거나 죽은 글, 제목이 없는 글은 정상적으로 건너뛴다."""
    if story.get("dead") or story.get("deleted") or not story.get("title"):
        return None
    story_id = story["id"]
    posted = story.get("time")
    return RawItem(
        external_id=str(story_id),
        url=story.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
        # 제목은 HTML 엔티티만 풀고 태그는 지우지 않는다. 일반 텍스트의 `<T>`가 지워지면 안 된다
        title=clean_text(html.unescape(story["title"]), TITLE_LIMIT),
        excerpt=clean_text(story.get("text"), EXCERPT_LIMIT, is_html=True),
        published_at=datetime.fromtimestamp(posted, tz=timezone.utc) if posted else None,
        metadata={"score": story.get("score"), "num_comments": story.get("descendants"), "type": story.get("type")},
    )
