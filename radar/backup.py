"""백업과 복원 검증(3단계 게이트, SPEC 3단계). 새 도구 없이 psycopg의 COPY로 표마다 CSV를 만든다.

복원은 운영 스키마를 건드리지 않는다. 새 임시 스키마에 마이그레이션을 적용하고 되살린 뒤, 다시 내보내 만든 체크섬이
백업의 체크섬과 같은지 본다. 운영으로 되돌리는 일은 되돌리기 어려운 작업이라 자동으로 하지 않는다.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql

from radar.store.db import apply_migrations, connect

BACKUP_DIR = Path("backups")
# 외래 키 순서. schema_migrations는 복원할 때 마이그레이션이 새로 만든다
TABLES = (
    "sources", "jobs", "collection_runs", "source_items", "technologies", "technology_aliases", "analysis_runs",
    "technology_sources", "users", "technology_claims", "claim_evidence", "technology_events", "user_knowledge", "user_notes",
    "relation_type_rules", "technology_relations", "relation_evidence", "relation_proposals", "relation_sample",
)


def _export(conn: psycopg.Connection, table: str, path: Path) -> tuple[int, str]:
    """표를 모든 열 순서로 정렬해 CSV로 쓴다. 정렬이 같아야 복원 뒤 다시 내보낸 파일과 체크섬을 비교할 수 있다."""
    count = conn.execute(sql.SQL("select count(*) as n from {}").format(sql.Identifier(table))).fetchone()["n"]
    columns = conn.execute(sql.SQL("select * from {} limit 0").format(sql.Identifier(table))).description
    order = sql.SQL(", ").join(sql.Literal(n) for n in range(1, len(columns) + 1))
    query = sql.SQL("copy (select * from {} order by {}) to stdout (format csv, header)").format(sql.Identifier(table), order)
    digest = hashlib.sha256()
    with path.open("wb") as out, conn.cursor().copy(query) as copy:
        for chunk in copy:
            out.write(chunk)
            digest.update(chunk)
    return count, digest.hexdigest()


def backup(conn: psycopg.Connection, now: datetime, root: Path = BACKUP_DIR) -> Path:
    """표마다 CSV와 manifest.json(행 수, SHA-256)을 만든다. 한 트랜잭션의 스냅숏이라 표 사이가 어긋나지 않는다."""
    folder = root / now.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    manifest = {}
    conn.execute("set timezone = 'UTC'")  # 시각의 글자 모양을 복원 때와 같게 한다
    with conn.transaction():
        conn.execute("set transaction isolation level repeatable read")
        for table in TABLES:
            rows, checksum = _export(conn, table, folder / f"{table}.csv")
            manifest[table] = {"rows": rows, "sha256": checksum}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return folder


def _load(conn: psycopg.Connection, table: str, path: Path) -> None:
    query = sql.SQL("copy {} from stdin (format csv, header)").format(sql.Identifier(table))
    with path.open("rb") as source, conn.cursor().copy(query) as copy:
        while chunk := source.read(65536):
            copy.write(chunk)


def restore_check(url: str, folder: Path, schema: str, keep: bool = False) -> tuple[bool, list[str]]:
    """임시 스키마에 되살리고 표마다 행 수와 체크섬을 비교한다. (모두 같은지, 표별 결과 줄)."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    saved = [t for t in TABLES if t in manifest]  # 4단계 전에 만든 백업에는 관계 표가 없다
    lines, ok = [], True
    with connect(url) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:
            with connect(url, schema) as conn:
                conn.execute("set timezone = 'UTC'")
                apply_migrations(conn)
                tables = sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
                with conn.transaction():
                    conn.execute(sql.SQL("truncate {} restart identity cascade").format(tables))  # 마이그레이션이 넣은 기본 행을 비운다
                    for table in saved:
                        _load(conn, table, folder / f"{table}.csv")
                for table in saved:
                    rows, checksum = _export(conn, table, folder / f".check-{table}.csv")
                    (folder / f".check-{table}.csv").unlink()
                    same = rows == manifest[table]["rows"] and checksum == manifest[table]["sha256"]
                    ok &= same
                    lines.append(f"{table}: {rows}행 {'같음' if same else '다름(백업 ' + str(manifest[table]['rows']) + '행)'}")
        finally:
            if not keep:
                admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
    return ok, lines
