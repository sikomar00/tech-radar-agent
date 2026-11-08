"""규칙으로 만드는 변화 이력(S3-04, SPEC 3단계). AI를 쓰지 않는다.

GitHub 릴리스 항목이 기술에 붙으면 release, GitHub 저장소 항목이 붙으면 first_published(저장소 생성 시각)를 쌓는다.
인수·통합·지원 종료는 글에서 뽑아야 해서 4단계(S4-07)에서 연다(2026-11-08 결정).
"""
from typing import Any

import psycopg

_INSERT = (
    "insert into technology_events (technology_id, event_type, occurred_at, evidence_key, source_item_id, summary) "
    "values (%s, %s, %s, %s, %s, %s) on conflict do nothing"
)
_REFRESH_FIRST_PUBLISHED = (
    "update technologies set first_published_at = (select min(occurred_at) from technology_events "
    "where technology_id = %(id)s and event_type = 'first_published') where id = %(id)s"
)


def event_for(item: dict[str, Any]) -> tuple[str, str] | None:
    """항목이 만드는 이벤트의 (종류, 근거 키). 시각을 모르거나 GitHub 항목이 아니면 None."""
    meta = item["metadata"] or {}
    if item["source_kind"] != "github" or item.get("published_at") is None:
        return None
    if meta.get("kind") == "release":
        return "release", str(meta.get("tag") or item["url"])
    if meta.get("kind") == "new_repo":
        return "first_published", item["url"]
    return None


def record_event(conn: psycopg.Connection, technology_id: int, item: dict[str, Any]) -> bool:
    """이벤트를 쌓고 새로 쌓였으면 True. 같은 이벤트는 unique 제약이 막아 재수집해도 중복되지 않는다."""
    found = event_for(item)
    if found is None:
        return False
    kind, key = found
    inserted = conn.execute(_INSERT, (technology_id, kind, item["published_at"], key, item["id"], item["title"])).rowcount == 1
    if inserted and kind == "first_published":
        conn.execute(_REFRESH_FIRST_PUBLISHED, {"id": technology_id})
    return inserted


def backfill(conn: psycopg.Connection) -> int:
    """이미 기술에 붙은 GitHub 항목으로 이벤트를 채운다. 쓰기는 추가뿐이고 몇 번 돌려도 같다."""
    rows = conn.execute(
        "select ts.technology_id, i.id, i.url, i.title, i.metadata, i.published_at, s.kind as source_kind "
        "from technology_sources ts join source_items i on i.id = ts.source_item_id join sources s on s.id = i.source_id "
        "where s.kind = 'github' order by i.id"
    ).fetchall()
    return sum(record_event(conn, row["technology_id"], row) for row in rows)
