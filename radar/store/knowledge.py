"""3단계 지식 DB 접근: 프로필 주장과 근거(S3-01, S3-03). 화면용 조회는 radar/store/views.py에 있다."""
from typing import Any

import psycopg

from radar.domain.analysis import repo_key
from radar.domain.profile import ALL_FIELDS


def technology(conn: psycopg.Connection, technology_id: int) -> dict[str, Any] | None:
    return conn.execute(
        "select id, canonical_name, type, archived_at from technologies where id = %s", (technology_id,)
    ).fetchone()


def sources(conn: psycopg.Connection, technology_id: int) -> list[dict[str, Any]]:
    """기술에 붙은 수집 항목(오래된 순). 프로필을 만들 페이지를 고르고 제목을 모델에 보낼 때 쓴다."""
    return conn.execute(
        "select i.id, i.url, i.normalized_url, i.title, i.excerpt, i.metadata, s.kind as source_kind, ts.evidence_type "
        "from technology_sources ts join source_items i on i.id = ts.source_item_id join sources s on s.id = i.source_id "
        "where ts.technology_id = %s order by i.id", (technology_id,),
    ).fetchall()


def github_repo(rows: list[dict[str, Any]]) -> tuple[str, int] | None:
    """연결된 GitHub 항목 중 처음 것의 (owner/name, 항목 id)."""
    for row in rows:
        repo = repo_key(row["source_kind"], row["normalized_url"], row["metadata"] or {})
        if repo:
            return repo, row["id"]
    return None


def representative(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """대표 출처: 첫 announcement·official 근거, 없으면 가장 오래된 근거(ADR-0007)."""
    return next((r for r in rows if r["evidence_type"] in ("announcement", "official")), rows[0] if rows else None)


def save_profile(conn: psycopg.Connection, technology_id: int, fields: dict[str, str], evidence_item: int) -> None:
    """이전 현재 주장을 모두 내리고 새 주장을 올린다. 행을 지우지 않는다. 빈 칸은 행이 없어 "미확인"이 된다."""
    run = conn.execute(
        "select id from analysis_runs where kind = 'profile' and technology_id = %s and is_current", (technology_id,)
    ).fetchone()
    with conn.transaction():
        conn.execute("update technology_claims set is_current = false where technology_id = %s and is_current "
                     "and field = any(%s)", (technology_id, list(ALL_FIELDS)))
        for field, content in fields.items():
            claim = conn.execute(
                "insert into technology_claims (technology_id, field, content, analysis_run_id) values (%s, %s, %s, %s) "
                "returning id", (technology_id, field, content, run["id"] if run else None),
            ).fetchone()["id"]
            conn.execute("insert into claim_evidence (claim_id, source_item_id) values (%s, %s)", (claim, evidence_item))


def current_claims(conn: psycopg.Connection, technology_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "select c.field, c.content, c.confidence, array_agg(e.source_item_id) as evidence from technology_claims c "
        "left join claim_evidence e on e.claim_id = c.id where c.technology_id = %s and c.is_current "
        "group by c.id, c.field, c.content, c.confidence", (technology_id,),
    ).fetchall()
    return {row["field"]: row for row in rows}
