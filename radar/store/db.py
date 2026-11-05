from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def connect(url: str, schema: str | None = None) -> psycopg.Connection:
    """autocommit 연결을 연다. 여러 문장이 한 덩어리여야 하면 호출하는 쪽이 conn.transaction()을 쓴다."""
    conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    if schema:
        conn.execute(sql.SQL("set search_path = {}").format(sql.Identifier(schema)))
    return conn


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """아직 적용하지 않은 SQL 파일을 이름순으로 하나씩, 각각 한 트랜잭션으로 적용한다."""
    conn.execute(
        "create table if not exists schema_migrations "
        "(name text primary key, applied_at timestamptz not null default now())"
    )
    done = {row["name"] for row in conn.execute("select name from schema_migrations")}
    applied = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        applied.append(path.name)
    return applied
