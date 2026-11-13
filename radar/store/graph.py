"""그래프 화면(S4-08, IA-5)과 관계 정확도 표본(4단계 게이트)의 조회. 쓰기는 radar/store/relations.py에 있다."""
from collections import deque
from typing import Any

import psycopg

from radar.domain.listing import description
from radar.domain.relations import RELATION_LABELS, SYMMETRIC

SAMPLE_SIZE = 50

_EDGES = (
    "select r.id, r.source_technology_id as source, r.target_technology_id as target, r.relation_type as type, "
    "(r.user_verified or r.origin = 'manual') as verified from technology_relations r "
    "join technologies s on s.id = r.source_technology_id join technologies t on t.id = r.target_technology_id "
    "where r.hidden_at is null and s.archived_at is null and t.archived_at is null order by r.id"
)
NODES = (
    "select t.id, t.canonical_name as name, t.type, "
    "(select count(*)::int from technology_sources s where s.technology_id = t.id) as sources, "
    "(select c.content from technology_claims c where c.technology_id = t.id and c.is_current and c.field = 'one_liner' "
    " order by c.id desc limit 1) as one_liner, "
    "(select i.excerpt from technology_sources s join source_items i on i.id = s.source_item_id "
    " where s.technology_id = t.id and coalesce(i.excerpt, '') <> '' order by i.id limit 1) as excerpt "
    "from technologies t where t.archived_at is null and (%(all)s or t.id = any(%(ids)s)) order by t.id"
)


def _reach(edges: list[dict[str, Any]], center: int, depth: int) -> set[int]:
    """center에서 선을 따라 depth 단계까지 닿는 기술(방향은 보지 않는다)."""
    neighbors: dict[int, set[int]] = {}
    for e in edges:
        neighbors.setdefault(e["source"], set()).add(e["target"])
        neighbors.setdefault(e["target"], set()).add(e["source"])
    seen, queue = {center}, deque([(center, 0)])
    while queue:
        node, level = queue.popleft()
        for other in neighbors.get(node, ()) if level < depth else ():
            if other not in seen:
                seen.add(other)
                queue.append((other, level + 1))
    return seen


def graph(conn: psycopg.Connection, center: int | None = None, depth: int = 1, all_nodes: bool = False) -> dict[str, list]:
    """점(보관하지 않은 기술)과 선(숨기지 않은 관계). center가 있으면 그 이웃만, 없으면 관계가 있는 기술(all_nodes면 전부)."""
    edges = conn.execute(_EDGES).fetchall()
    if center is not None:
        ids = _reach(edges, center, depth)
        edges = [e for e in edges if e["source"] in ids and e["target"] in ids]
    else:
        ids = {e["source"] for e in edges} | {e["target"] for e in edges}
    rows = conn.execute(NODES, {"all": all_nodes and center is None, "ids": list(ids)}).fetchall()
    nodes = [{"id": r["id"], "name": r["name"], "type": r["type"], "sources": r["sources"],
              "what": description(r["one_liner"], r["excerpt"])[0]} for r in rows]
    return {"nodes": nodes,
            "edges": [{**e, "symmetric": e["type"] in SYMMETRIC, "label": RELATION_LABELS[e["type"]]} for e in edges]}


def freeze_sample(conn: psycopg.Connection, size: int = SAMPLE_SIZE) -> tuple[bool, str]:
    """정확도 표본을 한 번만 고정한다: 지금 있는 AI 관계를 md5(id) 순서(무작위와 같다)로 size개. 이미 있으면 바꾸지 않는다."""
    if conn.execute("select count(*)::int as n from relation_sample").fetchone()["n"]:
        return False, "표본이 이미 고정돼 있습니다"
    ai = conn.execute("select count(*)::int as n from technology_relations where origin = 'ai'").fetchone()["n"]
    if ai < size:
        return False, f"AI 관계가 {ai}개라 {size}개 표본을 만들 수 없습니다"
    conn.execute("insert into relation_sample (relation_id, position) select id, row_number() over (order by md5(id::text)) "
                 "from technology_relations where origin = 'ai' order by md5(id::text) limit %s", (size,))
    return True, f"표본 {size}개를 고정했습니다(/relations/sample)"


def sample(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """고정한 정확도 표본(숨긴 관계 포함). 판정해도, 새 관계가 생겨도 표본은 그대로다."""
    return conn.execute(
        "select r.id, r.relation_type, r.user_verified, r.hidden_at, r.source_technology_id, r.target_technology_id, "
        "s.canonical_name as source_name, t.canonical_name as target_name, "
        "(select e.quote from relation_evidence e where e.relation_id = r.id order by e.source_item_id limit 1) as quote, "
        "(select i.url from relation_evidence e join source_items i on i.id = e.source_item_id where e.relation_id = r.id "
        " order by e.source_item_id limit 1) as url "
        "from relation_sample x join technology_relations r on r.id = x.relation_id "
        "join technologies s on s.id = r.source_technology_id join technologies t on t.id = r.target_technology_id "
        "order by x.position"
    ).fetchall()


def precision(rows: list[dict[str, Any]]) -> tuple[int, int, float | None]:
    """(확인 수, 판정 수, Precision). 확인과 숨김이 둘 다면 숨김을 따른다(틀렸다고 본 것). 판정이 없으면 None."""
    hidden = sum(1 for r in rows if r["hidden_at"] is not None)
    verified = sum(1 for r in rows if r["user_verified"] and r["hidden_at"] is None)
    judged = hidden + verified
    return verified, judged, (verified / judged if judged else None)
