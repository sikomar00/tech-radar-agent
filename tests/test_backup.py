"""백업·복원(3단계 게이트): 기술·이벤트·개인 메모가 행 수와 체크섬까지 그대로 돌아오고, 원래 스키마는 건드리지 않는다."""
import uuid
from datetime import datetime, timezone

from psycopg import sql

from radar.backup import TABLES, backup, restore_check
from radar.store.knowledge import save_profile
from radar.store.technologies import TechnologyStore
from radar.store.views import add_note, set_state, user_id
from tests.conftest import add_item

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def _fill(db) -> None:
    tech, _ = TechnologyStore(db).create("Ollama", "PRODUCT")
    item = add_item(db, title='제목, "따옴표"와\n줄바꿈', metadata={"kind": "new_repo", "topics": ["llm"]})
    TechnologyStore(db).link(tech, item, "official")
    db.execute("insert into technology_events (technology_id, event_type, occurred_at, evidence_key) "
               "values (%s, 'release', '2026-11-01T03:04:05.123456Z', 'v1')", (tech,))
    save_profile(db, tech, {"one_liner": "로컬 LLM"}, item)
    user = user_id(db)
    set_state(db, user, tech, "learned")
    add_note(db, user, tech, "메모, 쉼표와 \\ 역슬래시")


def _schema_exists(db, name: str) -> bool:
    return db.execute("select 1 from pg_namespace where nspname = %s", (name,)).fetchone() is not None


def test_a_backup_restores_with_the_same_rows_and_checksums(db, db_env, tmp_path) -> None:
    _fill(db)
    folder = backup(db, NOW, tmp_path)
    target = f"r_{uuid.uuid4().hex[:10]}"
    ok, lines = restore_check(db_env[0], folder, target)
    assert ok, lines
    assert len(lines) == len(TABLES) and "technology_events: 1행 같음" in lines and "user_notes: 1행 같음" in lines
    assert not _schema_exists(db, target)  # 확인용 스키마는 지운다


def test_a_tampered_backup_is_reported_as_different(db, db_env, tmp_path) -> None:
    _fill(db)
    folder = backup(db, NOW, tmp_path)
    notes = folder / "user_notes.csv"
    notes.write_text(notes.read_text(encoding="utf-8").replace("메모", "바뀐 메모"), encoding="utf-8")
    ok, lines = restore_check(db_env[0], folder, f"r_{uuid.uuid4().hex[:10]}")
    assert not ok and any(line.startswith("user_notes") and "다름" in line for line in lines)


def test_restoring_never_touches_the_source_schema(db, db_env, tmp_path) -> None:
    _fill(db)
    before = {t: db.execute(sql.SQL("select count(*) as n from {}").format(sql.Identifier(t))).fetchone()["n"] for t in TABLES}
    restore_check(db_env[0], backup(db, NOW, tmp_path), f"r_{uuid.uuid4().hex[:10]}")
    after = {t: db.execute(sql.SQL("select count(*) as n from {}").format(sql.Identifier(t))).fetchone()["n"] for t in TABLES}
    assert before == after
