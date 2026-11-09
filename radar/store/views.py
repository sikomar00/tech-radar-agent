"""웹 화면(IA v1.0)이 읽고 쓰는 조회. 화면은 DB를 이 모듈과 radar/store/의 다른 모듈로만 만진다(ADR-0006)."""
from datetime import datetime
from typing import Any

import psycopg

STATES = ("unread", "saved", "learned")
SOURCE_KINDS = ("github", "hackernews", "rss", "manual")
SEARCH_TYPES = ("PRODUCT", "FRAMEWORK_LIBRARY", "PROTOCOL_STANDARD", "MODEL")
FUZZY_SEARCH_MIN = 0.3  # 검색은 매칭(0.6)보다 넓게 보여 준다. 사람이 고르므로 틀린 후보가 섞여도 된다
ROW_LIMIT = 200

# Feed와 검색이 같은 모양의 줄을 쓴다: 기술 하나가 한 줄이고, 같은 기술의 출처 여러 개는 묶인다(IA 결정 3)
_ROWS = """
select t.id, t.canonical_name, t.type, t.created_at, max(i.first_discovered_at) as last_seen,
       min(i.first_discovered_at) as first_seen, count(distinct i.id)::int as sources,
       (array_agg(i.title order by i.first_discovered_at desc))[1] as last_title,
       coalesce(k.state::text, 'unread') as state, k.importance,
       (select c.content from technology_claims c where c.technology_id = t.id and c.field = 'one_liner' and c.is_current
        order by c.id desc limit 1) as one_liner
from technologies t
join technology_sources ts on ts.technology_id = t.id
join source_items i on i.id = ts.source_item_id
left join user_knowledge k on k.technology_id = t.id and k.user_id = %(user)s
where t.archived_at is null and {where}
group by t.id, k.state, k.importance
having {having}
order by last_seen desc, t.id desc limit {limit}
"""


def user_id(conn: psycopg.Connection) -> str:
    """사용자는 한 명이다(ADR-0006). 마이그레이션 0003이 넣은 행을 쓴다."""
    return str(conn.execute("select id from users order by created_at limit 1").fetchone()["id"])


def feed(conn: psycopg.Connection, user: str, since: datetime, state: str | None) -> list[dict[str, Any]]:
    """기간 안에 출처가 새로 붙은 기술(IA-1). 기술이 기간 안에 생겼으면 새 기술, 아니면 업데이트다."""
    query = _ROWS.format(where="true", limit=ROW_LIMIT,
                         having="max(i.first_discovered_at) >= %(since)s and (%(state)s::text is null "
                                "or coalesce(k.state::text, 'unread') = %(state)s)")
    rows = conn.execute(query, {"user": user, "since": since, "state": state}).fetchall()
    return [{**row, "is_new": row["created_at"] >= since} for row in rows]


def _like(text: str) -> str:
    """LIKE 패턴의 특수 문자(%, _, 역슬래시)를 글자 그대로 찾게 바꾼다."""
    return "%" + text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(conn: psycopg.Connection, user: str, q: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    """기술명·별칭(부분 일치와 유사도), 프로필 본문(부분 일치)으로 찾는다(IA-2, S3-07). 별칭으로 찾아도 기술은 한 줄이다."""
    where = """(%(q)s = '' or lower(t.canonical_name) like %(like)s escape '\\'
        or similarity(lower(t.canonical_name), lower(%(q)s)) >= %(fuzzy)s
        or exists (select 1 from technology_aliases a where a.technology_id = t.id
                   and (lower(a.alias) like %(like)s escape '\\' or similarity(lower(a.alias), lower(%(q)s)) >= %(fuzzy)s))
        or exists (select 1 from technology_claims c where c.technology_id = t.id and c.is_current
                   and lower(c.content) like %(like)s escape '\\'))
      and (%(type)s::text is null or t.type::text = %(type)s)
      and (%(kind)s::text is null or exists (select 1 from technology_sources s2 join source_items i2 on i2.id = s2.source_item_id
           join sources so on so.id = i2.source_id where s2.technology_id = t.id and so.kind = %(kind)s))"""
    having = ("(%(since)s::timestamptz is null or min(i.first_discovered_at) >= %(since)s) "
              "and (%(state)s::text is null or coalesce(k.state::text, 'unread') = %(state)s)")
    params = {"user": user, "q": q.strip(), "like": _like(q.strip()), "fuzzy": FUZZY_SEARCH_MIN, **filters}
    return conn.execute(_ROWS.format(where=where, having=having, limit=ROW_LIMIT), params).fetchall()


def detail(conn: psycopg.Connection, user: str, technology_id: int) -> dict[str, Any] | None:
    """기술 상세(IA-3): 머리, 별칭, 출처, 변화 이력, 내 상태, 내 메모. 프로필은 knowledge.current_claims로 읽는다."""
    tech = conn.execute(
        "select t.*, coalesce(k.state::text, 'unread') as state, k.importance from technologies t "
        "left join user_knowledge k on k.technology_id = t.id and k.user_id = %s where t.id = %s", (user, technology_id),
    ).fetchone()
    if tech is None:
        return None
    aliases = conn.execute("select alias, lang from technology_aliases where technology_id = %s order by lang, alias",
                           (technology_id,)).fetchall()
    sources = conn.execute(
        "select i.id, i.title, i.url, i.first_discovered_at, s.kind as source_kind, s.name as source_name, ts.evidence_type "
        "from technology_sources ts join source_items i on i.id = ts.source_item_id join sources s on s.id = i.source_id "
        "where ts.technology_id = %s order by i.first_discovered_at desc", (technology_id,),
    ).fetchall()
    events = conn.execute("select event_type, occurred_at, date_precision, evidence_key, summary from technology_events "
                          "where technology_id = %s order by occurred_at desc", (technology_id,)).fetchall()
    notes = conn.execute("select id, body, created_at from user_notes where user_id = %s and technology_id = %s "
                         "order by created_at desc", (user, technology_id)).fetchall()
    return {"tech": tech, "aliases": aliases, "sources": sources, "events": events, "notes": notes}


def set_state(conn: psycopg.Connection, user: str, technology_id: int, state: str) -> None:
    conn.execute(
        "insert into user_knowledge (user_id, technology_id, state) values (%s, %s, %s) "
        "on conflict (user_id, technology_id) do update set state = excluded.state, updated_at = now()",
        (user, technology_id, state),
    )


def set_importance(conn: psycopg.Connection, user: str, technology_id: int, importance: int | None) -> None:
    conn.execute(
        "insert into user_knowledge (user_id, technology_id, importance) values (%s, %s, %s) "
        "on conflict (user_id, technology_id) do update set importance = excluded.importance, updated_at = now()",
        (user, technology_id, importance),
    )


def add_note(conn: psycopg.Connection, user: str, technology_id: int, body: str) -> None:
    conn.execute("insert into user_notes (user_id, technology_id, body) values (%s, %s, %s)", (user, technology_id, body))


def archive(conn: psycopg.Connection, technology_id: int) -> bool:
    return conn.execute("update technologies set archived_at = now() where id = %s and archived_at is null",
                        (technology_id,)).rowcount == 1


def unarchive(conn: psycopg.Connection, technology_id: int) -> tuple[bool, str]:
    """되돌리기. 같은 이름의 활성 기술이 이미 있으면 소문자 정규명 유일 제약에 걸리므로 되돌리지 않는다(SPEC 3단계 보관)."""
    clash = conn.execute(
        "select o.id from technologies t join technologies o on lower(o.canonical_name) = lower(t.canonical_name) "
        "and o.id <> t.id and o.archived_at is null where t.id = %s", (technology_id,),
    ).fetchone()
    if clash:
        return False, f"같은 이름의 기술 #{clash['id']}이 이미 있어 되돌리지 않았습니다"
    changed = conn.execute("update technologies set archived_at = null where id = %s and archived_at is not null",
                           (technology_id,)).rowcount == 1
    return changed, "되돌렸습니다" if changed else "보관된 기술이 아닙니다"
