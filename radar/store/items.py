import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Iterable

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from radar.domain.cleaning import strip_control
from radar.domain.metadata import merge_metadata
from radar.domain.models import RawItem
from radar.domain.normalize import content_hash, normalize_url

log = logging.getLogger(__name__)
_SELECT = "select id, source_id, external_id, metadata from source_items where "


@dataclass(frozen=True)
class SaveStats:
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0  # 다른 항목과 키가 겹쳐 건너뛴 것(소스 사이의 중복, 같은 URL을 가리키는 서로 다른 항목)
    conflicts: int = 0
    invalid: int = 0

    def plus(self, **delta: int) -> "SaveStats":
        return replace(self, **{name: getattr(self, name) + n for name, n in delta.items()})


@dataclass(frozen=True)
class _Candidate:
    source_id: int
    item: RawItem
    normalized_url: str
    content_hash: str


class ItemStore:
    """수집 항목을 `source_items`에 넣거나 갱신한다. 중복 판정 순서는 docs/SPEC.md를 따른다."""

    def __init__(self, conn: psycopg.Connection, clock: Callable[[], datetime]) -> None:
        self._conn = conn
        self._clock = clock

    def save(self, source_id: int, items: Iterable[RawItem]) -> SaveStats:
        stats, now = SaveStats(), self._clock()
        for raw in items:
            try:
                stats = stats.plus(**{self._save_one(source_id, raw, now): 1})
            except (ValueError, psycopg.DataError) as err:  # 호스트 없는 URL, 인코딩할 수 없는 문자 등. 배치 전체를 죽이지 않는다
                log.warning("저장할 수 없는 항목이라 건너뜁니다: %s", err)
                stats = stats.plus(invalid=1)
        return stats

    def _save_one(self, source_id: int, raw: RawItem, now: datetime) -> str:
        item = replace(raw, url=strip_control(raw.url), external_id=strip_control(raw.external_id or "") or None)
        nurl = normalize_url(item.url)
        candidate = _Candidate(source_id, item, nurl, content_hash(item.title, item.excerpt, nurl))
        row = self._find(candidate)
        if row is None:
            return self._insert(candidate, now)
        if row["source_id"] == source_id and row["external_id"] == item.external_id:
            self._update(row, candidate, now)
            return "updated"
        log.debug("다른 항목과 키가 겹쳐 건너뜁니다: %s", item.url)
        return "duplicates"

    def _find(self, c: _Candidate) -> dict[str, Any] | None:
        """소스의 external_id → normalized_url → content_hash 순으로 기존 행을 찾는다."""
        lookups = []
        if c.item.external_id is not None:
            lookups.append((_SELECT + "source_id = %s and external_id = %s", (c.source_id, c.item.external_id)))
        lookups += [(_SELECT + "normalized_url = %s", (c.normalized_url,)), (_SELECT + "content_hash = %s", (c.content_hash,))]
        for query, args in lookups:
            row = self._conn.execute(query, args).fetchone()
            if row:
                return row
        return None

    def _update(self, row: dict[str, Any], c: _Candidate, now: datetime) -> None:
        # shortcut: 키(external_id, normalized_url, content_hash)와 url은 갱신하지 않는다. 그래서 제약과 충돌하지 않지만
        # GitHub 저장소 이름이 바뀌면 url이 옛 이름을 가리킨다. 2단계에서 필요해지면 충돌 처리와 함께 갱신한다.
        meta = merge_metadata(row["metadata"], c.item.metadata, now)
        self._conn.execute(
            "update source_items set title = %s, excerpt = %s, metadata = %s, collected_at = %s, "
            "published_at = coalesce(published_at, %s) where id = %s",
            (c.item.title, c.item.excerpt, Jsonb(meta), now, c.item.published_at, row["id"]),
        )

    def _insert(self, c: _Candidate, now: datetime) -> str:
        item = c.item
        try:
            self._conn.execute(
                "insert into source_items (source_id, external_id, url, normalized_url, title, excerpt, content_hash, "
                "metadata, published_at, first_discovered_at, collected_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (c.source_id, item.external_id, item.url, c.normalized_url, item.title, item.excerpt, c.content_hash,
                 Jsonb(merge_metadata({}, item.metadata, now)), item.published_at, now, now),
            )
        except UniqueViolation as err:
            # 조회 직후 다른 쓰기가 끼어든 경우에만 생긴다. 숨기지 않고 센다
            log.warning("삽입 중 키 충돌: %s", err)
            return "conflicts"
        return "inserted"
