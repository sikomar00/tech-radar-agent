"""`technologies`, `technology_aliases`, `technology_sources` 접근(기술 식별, 2단계)."""
from typing import Any, Iterable

import psycopg
from psycopg.errors import UniqueViolation

FUZZY_MIN = 0.6  # 초기값. 튜닝용 골든셋으로 조정한다(한 글자가 빠진 langchan도 0.58이다)
CANDIDATE_LIMIT = 5

_BY_NAME = (
    "select id from technologies where archived_at is null and lower(canonical_name) = lower(%(name)s) "
    "union select a.technology_id from technology_aliases a join technologies t on t.id = a.technology_id "
    "where t.archived_at is null and lower(a.alias) = lower(%(name)s)"
)
_BY_REPO = (
    "select ts.technology_id from technology_sources ts join technologies t on t.id = ts.technology_id "
    "join source_items i on i.id = ts.source_item_id where t.archived_at is null "
    "and (i.normalized_url = 'github.com/' || %(repo)s or lower(i.metadata ->> 'repo') = %(repo)s)"
)
# shortcut: 인덱스를 쓰지 않고 이름 전체를 훑는다. 기술이 수천 개를 넘으면 `%` 연산자와 gin 인덱스로 바꾼다
_FUZZY = (
    "with names as (select id as technology_id, lower(canonical_name) as name from technologies where archived_at is null "
    "  union all select a.technology_id, lower(a.alias) from technology_aliases a join technologies t on t.id = a.technology_id "
    "  where t.archived_at is null) "
    "select n.technology_id as id, max(similarity(n.name, lower(k))) as score "
    "from names n cross join unnest(%(keys)s::text[]) as k group by n.technology_id "
    "having max(similarity(n.name, lower(k))) >= %(min)s order by score desc, id limit %(limit)s"
)
_ADD_ALIAS = (
    "insert into technology_aliases (technology_id, alias, lang) select t.id, %(alias)s, %(lang)s from technologies t "
    "where t.id = %(id)s and lower(t.canonical_name) <> lower(%(alias)s) "
    "and not exists (select 1 from technology_aliases a where a.technology_id = t.id and lower(a.alias) = lower(%(alias)s))"
)


class TechnologyStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def definite_match(self, name: str, repo: str | None) -> list[int]:
        """대소문자 무시로 정규명·별칭이 같거나 같은 GitHub 저장소가 이미 연결된 기술. 하나면 확정이고, 여럿이면 모호하다."""
        ids = {row["id"] for row in self._conn.execute(_BY_NAME, {"name": name})}
        if repo:
            ids |= {row["technology_id"] for row in self._conn.execute(_BY_REPO, {"repo": repo})}
        return sorted(ids)

    def fuzzy_candidates(self, keys: list[str]) -> list[int]:
        """이름이 keys 중 하나와 비슷한(pg_trgm 유사도 FUZZY_MIN 이상) 기술 id, 유사도 순 상위 CANDIDATE_LIMIT개."""
        rows = self._conn.execute(_FUZZY, {"keys": keys, "min": FUZZY_MIN, "limit": CANDIDATE_LIMIT}).fetchall()
        return [row["id"] for row in rows]

    def describe(self, ids: Iterable[int]) -> list[dict[str, Any]]:
        """매칭 호출에 보낼 후보 설명: id, 정규명, 별칭."""
        return self._conn.execute(
            "select t.id, t.canonical_name, coalesce(array_agg(a.alias order by a.alias) filter (where a.alias is not null), "
            "'{}') as aliases from technologies t left join technology_aliases a on a.technology_id = t.id "
            "where t.id = any(%s) group by t.id order by t.id", (list(ids),),
        ).fetchall()

    def create(self, name: str, type_: str | None) -> tuple[int, bool]:
        """새 기술을 만든다. 소문자 정규명이 겹치면(동시에 처리된 경우) 만들지 않고 기존 기술을 (id, False)로 돌려준다."""
        try:
            with self._conn.transaction():
                row = self._conn.execute(
                    "insert into technologies (canonical_name, type) values (%s, %s) returning id", (name, type_)
                ).fetchone()
            return row["id"], True
        except UniqueViolation:
            row = self._conn.execute(
                "select id from technologies where archived_at is null and lower(canonical_name) = lower(%s)", (name,)
            ).fetchone()
            return row["id"], False

    def add_aliases(self, technology_id: int, english: Iterable[str], korean: Iterable[str]) -> None:
        """별칭을 더한다. 대소문자만 다른 중복과 정규명과 같은 별칭은 넣지 않는다."""
        for lang, names in (("en", english), ("ko", korean)):
            for alias in names:
                self._conn.execute(_ADD_ALIAS, {"id": technology_id, "alias": alias, "lang": lang})

    def link(self, technology_id: int, item_id: int, evidence_type: str) -> None:
        """기술과 수집 항목을 잇는다. 신뢰도는 기본값 Unverified다. AI 결과가 확인을 대신하지 않는다."""
        self._conn.execute(
            "insert into technology_sources (technology_id, source_item_id, evidence_type) values (%s, %s, %s) "
            "on conflict do nothing", (technology_id, item_id, evidence_type),
        )
