from datetime import datetime, timezone
from typing import Any

import feedparser

from radar.adapters.http import HttpClient, call, mark_parse_failure
from radar.domain.cleaning import EXCERPT_LIMIT, TITLE_LIMIT, clean_text
from radar.domain.models import FetchResult, RawItem


def fetch(config: dict[str, Any], http: HttpClient, now: datetime) -> FetchResult:
    result = FetchResult()
    res = call(http, config["feed_url"], result.requests)
    if res is None:
        return result
    feed = feedparser.parse(res.body)
    # feedparser는 깨진 피드도 가능한 만큼 읽는다. 항목이 없고 피드 형식을 알아보지 못했거나(version이 빈 문자열) 깨졌으면 해석 실패다.
    # 형식이 확인된 빈 피드(예: rss20)는 정상적인 빈 결과라서 성공으로 센다
    if not feed.entries and (feed.bozo or not feed.version):
        mark_parse_failure(result.requests, repr(feed.get("bozo_exception") or "피드 형식을 알 수 없습니다"))
        return result
    for entry in feed.entries[: config.get("limit", 30)]:
        item = _entry_item(entry)
        if item:
            result.items.append(item)
    return result


def _entry_item(entry: Any) -> RawItem | None:
    link, title = entry.get("link"), entry.get("title")
    if not link or not title:
        return None
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return RawItem(
        external_id=entry.get("id") or None,  # 빈 <guid></guid>가 같은 키 하나로 합쳐지지 않게 한다
        url=link,
        title=clean_text(title, TITLE_LIMIT),
        excerpt=clean_text(entry.get("summary"), EXCERPT_LIMIT, is_html=True),
        published_at=datetime(*stamp[:6], tzinfo=timezone.utc) if stamp else None,
    )
