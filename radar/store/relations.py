"""4단계 관계 저장과 관리(SPEC v1.7): AI 관계, 검토 목록, 확인·숨김, 직접 잇기. 그래프·표본 조회는 radar/store/graph.py에 있다.

매트릭스(ADR-0002)는 DB 트리거가 지킨다. 여기서는 트리거가 막은 관계를 검토 목록으로 돌린다.
"""
from typing import Any, Iterable

import psycopg
from psycopg.errors import RaiseException

from radar.domain.relations import SYMMETRIC, Proposed


def known_names(conn: psycopg.Connection) -> list[tuple[int, list[str]]]:
    """보관하지 않은 기술마다 (id, 정규명과 별칭). 프로필 페이지에서 후보를 찾을 때 쓴다."""
    rows = conn.execute(
        "select t.id, t.canonical_name, coalesce(array_agg(a.alias) filter (where a.alias is not null), '{}') as aliases "
        "from technologies t left join technology_aliases a on a.technology_id = t.id "
        "where t.archived_at is null group by t.id order by t.id"
    ).fetchall()
    return [(row["id"], [row["canonical_name"], *row["aliases"]]) for row in rows]


def active_names(conn: psycopg.Connection) -> list[str]:
    """직접 잇기 폼의 고르기 목록(보관하지 않은 기술의 정규명)."""
    return [r["canonical_name"] for r in conn.execute(
        "select canonical_name from technologies where archived_at is null order by lower(canonical_name)").fetchall()]


def resolve(conn: psycopg.Connection, name: str) -> int | None:
    """이름으로 보관하지 않은 기술 하나를 찾는다. 정규명이 먼저이고, 별칭은 한 기술만 가리킬 때만 쓴다."""
    exact = conn.execute("select id from technologies where archived_at is null and lower(canonical_name) = lower(%s)",
                         (name.strip(),)).fetchone()
    if exact:
        return exact["id"]
    rows = conn.execute("select distinct t.id from technology_aliases a join technologies t on t.id = a.technology_id "
                        "where t.archived_at is null and lower(a.alias) = lower(%s)", (name.strip(),)).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def describe(conn: psycopg.Connection, ids: list[int]) -> list[dict[str, Any]]:
    """모델에 보낼 후보 목록(id, 이름, 유형). 순서는 ids를 따른다."""
    rows = {r["id"]: r for r in conn.execute(
        "select id, canonical_name as name, coalesce(type::text, 'NONE') as type from technologies where id = any(%s)", (ids,)
    ).fetchall()}
    return [rows[i] for i in ids if i in rows]


def _types_known(conn: psycopg.Connection, a: int, b: int) -> bool:
    return conn.execute("select count(*) = 2 as ok from technologies where id in (%s, %s) and type is not null",
                        (a, b)).fetchone()["ok"]


def _active(conn: psycopg.Connection, a: int, b: int) -> bool:
    return conn.execute("select count(*) = 2 as ok from technologies where id in (%s, %s) and archived_at is null",
                        (a, b)).fetchone()["ok"]


def _insert(conn: psycopg.Connection, p: Proposed, origin: str, run_id: int | None) -> int | None:
    """관계를 넣거나 이미 있는 관계의 id를 돌려준다. 매트릭스를 어기면 None(세이브포인트로 되돌린다)."""
    manual = origin == "manual"
    try:
        with conn.transaction():
            conn.execute(
                "insert into technology_relations (source_technology_id, target_technology_id, relation_type, origin, "
                "confidence, user_verified, analysis_run_id) values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                (p.source_id, p.target_id, p.relation_type, origin, "Verified" if manual else "Unverified", manual, run_id),
            )
    except RaiseException:
        return None
    return conn.execute("select id from technology_relations where source_technology_id = %s and target_technology_id = %s "
                        "and relation_type = %s", (p.source_id, p.target_id, p.relation_type)).fetchone()["id"]


def _evidence(conn: psycopg.Connection, relation_id: int, item_id: int | None, quote: str | None) -> None:
    if item_id is not None:
        conn.execute("insert into relation_evidence (relation_id, source_item_id, quote) values (%s, %s, %s) "
                     "on conflict do nothing", (relation_id, item_id, quote))


def _propose(conn: psycopg.Connection, p: Proposed, reason: str, item_id: int, run_id: int | None) -> None:
    """검토 목록에 올린다. 사용자가 이미 버린 같은 제안은 다시 올리지 않는다."""
    conn.execute(
        "insert into relation_proposals (source_technology_id, target_technology_id, relation_type, reason, source_item_id, "
        "quote, analysis_run_id) select %(s)s, %(t)s, %(r)s, %(reason)s, %(item)s, %(quote)s, %(run)s "
        "where not exists (select 1 from relation_proposals where source_technology_id = %(s)s and target_technology_id = %(t)s "
        "and relation_type = %(r)s and status = 'discarded') "
        "on conflict (source_technology_id, target_technology_id, relation_type) where status = 'open' do nothing",
        {"s": p.source_id, "t": p.target_id, "r": p.relation_type, "reason": reason, "item": item_id, "quote": p.quote, "run": run_id},
    )


def save_ai(conn: psycopg.Connection, proposals: Iterable[Proposed], item_id: int, run_id: int | None) -> tuple[int, int]:
    """(저장한 관계 수, 검토 목록으로 보낸 수). 같은 관계가 이미 있으면 근거만 더한다. 확인·숨김 값은 건드리지 않는다."""
    saved = queued = 0
    with conn.transaction():
        for p in proposals:
            relation_id = _insert(conn, p, "ai", run_id) if _types_known(conn, p.source_id, p.target_id) else None
            if relation_id is None:
                _propose(conn, p, "matrix" if _types_known(conn, p.source_id, p.target_id) else "untyped", item_id, run_id)
                queued += 1
                continue
            _evidence(conn, relation_id, item_id, p.quote)
            saved += 1
    return saved, queued


def recheck(conn: psycopg.Connection, technology_id: int) -> int:
    """유형이 정해진 뒤 그 기술이 낀 open 제안을 다시 검사한다. 통과한 수를 돌려준다."""
    rows = conn.execute("select * from relation_proposals where status = 'open' and %s in (source_technology_id, target_technology_id)",
                        (technology_id,)).fetchall()
    accepted = 0
    for row in rows:
        p = Proposed(row["source_technology_id"], row["target_technology_id"], row["relation_type"], row["quote"] or "")
        if not _types_known(conn, p.source_id, p.target_id):
            continue
        with conn.transaction():
            relation_id = _insert(conn, p, "ai", row["analysis_run_id"])
            if relation_id is None:  # 이제 둘 다 유형이 있으니 남은 까닭은 매트릭스다
                conn.execute("update relation_proposals set reason = 'matrix' where id = %s", (row["id"],))
                continue
            _evidence(conn, relation_id, row["source_item_id"], row["quote"])
            conn.execute("update relation_proposals set status = 'accepted' where id = %s", (row["id"],))
            accepted += 1
    return accepted


def add_manual(conn: psycopg.Connection, source_id: int, target_id: int, relation_type: str) -> tuple[bool, str]:
    """사용자가 직접 잇는다. 근거 항목 없이 확인된 실선 관계로 넣는다(2026-11-09 결정)."""
    if source_id == target_id:
        return False, "같은 기술끼리는 이을 수 없습니다"
    if relation_type in SYMMETRIC and source_id > target_id:
        source_id, target_id = target_id, source_id
    if not _active(conn, source_id, target_id):
        return False, "보관한 기술은 이을 수 없습니다. 먼저 되돌리세요"
    if not _types_known(conn, source_id, target_id):
        return False, "유형이 없는 기술은 이을 수 없습니다. Monitor의 분류 보류에서 유형을 먼저 정하세요"
    relation_id = _insert(conn, Proposed(source_id, target_id, relation_type, ""), "manual", None)
    if relation_id is None:
        return False, f"두 기술의 유형 조합에는 {relation_type} 관계를 쓸 수 없습니다(허용 매트릭스, ADR-0002)"
    conn.execute("update technology_relations set user_verified = true, hidden_at = null where id = %s", (relation_id,))
    return True, "관계를 이었습니다"


_SETTERS = {
    "verify": "user_verified = true", "unverify": "user_verified = false",
    "hide": "hidden_at = now()", "unhide": "hidden_at = null",
}


def change(conn: psycopg.Connection, relation_id: int, action: str) -> bool:
    """확인·확인 취소·숨기기·되돌리기. AI 분석은 이 값을 바꾸지 않는다. 바꾼 관계가 있으면 True."""
    if action not in _SETTERS:
        return False
    return conn.execute(f"update technology_relations set {_SETTERS[action]} where id = %s", (relation_id,)).rowcount == 1


def discard(conn: psycopg.Connection, proposal_id: int) -> bool:
    return conn.execute("update relation_proposals set status = 'discarded' where id = %s and status = 'open'",
                        (proposal_id,)).rowcount == 1


def open_proposals(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "select p.id, p.relation_type, p.reason, p.quote, p.source_item_id, i.url, s.id as source_id, s.canonical_name as source_name, "
        "s.type as source_type, t.id as target_id, t.canonical_name as target_name, t.type as target_type "
        "from relation_proposals p join technologies s on s.id = p.source_technology_id "
        "join technologies t on t.id = p.target_technology_id left join source_items i on i.id = p.source_item_id "
        "where p.status = 'open' order by p.id"
    ).fetchall()


def for_technology(conn: psycopg.Connection, technology_id: int) -> list[dict[str, Any]]:
    """기술 상세의 관련 기술 표. 숨긴 관계도 돌려주고(화면이 접어 둔다), 근거는 (항목 id, URL, 문장) 목록이다."""
    return conn.execute(
        "select r.id, r.relation_type, r.origin, r.user_verified, r.hidden_at, r.source_technology_id, r.target_technology_id, "
        "s.canonical_name as source_name, t.canonical_name as target_name, "
        "coalesce(jsonb_agg(jsonb_build_object('item', e.source_item_id, 'url', i.url, 'quote', e.quote)) "
        "filter (where e.source_item_id is not null), '[]') as evidence "
        "from technology_relations r join technologies s on s.id = r.source_technology_id "
        "join technologies t on t.id = r.target_technology_id "
        "left join relation_evidence e on e.relation_id = r.id left join source_items i on i.id = e.source_item_id "
        "where %s in (r.source_technology_id, r.target_technology_id) and s.archived_at is null and t.archived_at is null "
        "group by r.id, s.canonical_name, t.canonical_name order by r.hidden_at nulls first, r.user_verified desc, r.id",
        (technology_id,),
    ).fetchall()
