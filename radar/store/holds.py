"""분류 보류 큐 조회(S2-03). 보류는 상태다: 유형이 없는 기술, 그리고 기존 기술과 같은지 모른 채 연결되지 않은 항목."""
from typing import Any

import psycopg


def held_technologies(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "select t.id, t.canonical_name, "
        "coalesce(array_agg(distinct a.alias) filter (where a.alias is not null), '{}') as aliases, "
        "(select count(*)::int from technology_sources s where s.technology_id = t.id) as sources "
        "from pending_classification t left join technology_aliases a on a.technology_id = t.id "
        "group by t.id, t.canonical_name order by t.id"  # 뷰라서 기본 키만으로는 다른 열이 종속되지 않는다
    ).fetchall()


def held_items(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """현재 match 결과가 UNKNOWN이고 어느 기술에도 연결되지 않은 항목과 모델의 분류 결과."""
    return conn.execute(
        "select i.id, i.title, i.url, c.result as classified from source_items i "
        "join analysis_runs m on m.source_item_id = i.id and m.kind = 'match' and m.is_current and m.result ->> 'decision' = 'UNKNOWN' "
        "join analysis_runs c on c.source_item_id = i.id and c.kind = 'classify' and c.is_current "
        "where not exists (select 1 from technology_sources s where s.source_item_id = i.id) order by i.id"
    ).fetchall()


def set_type(conn: psycopg.Connection, technology_id: int, type_: str) -> bool:
    """유형이 비어 있는 기술에만 유형을 정한다. 이미 정해진 기술은 바꾸지 않는다."""
    cur = conn.execute(
        "update technologies set type = %s where id = %s and type is null and archived_at is null", (type_, technology_id)
    )
    return cur.rowcount == 1
