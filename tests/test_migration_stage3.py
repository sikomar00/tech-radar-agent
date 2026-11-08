"""0003_stage3.sql이 DB에서 직접 막는 규칙(이벤트 수정·삭제 금지, 값 목록, RLS)과 기본 행을 확인한다."""
import psycopg
import pytest
from psycopg.errors import CheckViolation, RaiseException, UniqueViolation

from tests.test_migration_stage2 import _item, _technology


def test_one_user_and_an_inactive_manual_source_exist(db) -> None:
    assert db.execute("select count(*)::int as n from users").fetchone()["n"] == 1
    manual = db.execute("select kind, is_active from sources where name = 'manual'").fetchone()
    assert (manual["kind"], manual["is_active"]) == ("manual", False)


def test_retype_is_an_allowed_analysis_kind(db) -> None:
    tech = _technology(db, "Held", None)
    db.execute("insert into analysis_runs (kind, technology_id, model, input_hash, status, result) "
               "values ('retype', %s, 'm', 'h', 'succeeded', '{}')", (tech,))
    with pytest.raises(CheckViolation):
        db.execute("insert into analysis_runs (kind, technology_id, model, input_hash, status) "
                   "values ('guess', %s, 'm', 'h', 'failed')", (tech,))


def test_events_are_append_only_and_never_duplicated(db) -> None:
    tech = _technology(db, "Ollama")
    insert = ("insert into technology_events (technology_id, event_type, occurred_at, evidence_key) "
              "values (%s, 'release', '2026-11-01', 'v1') returning id")
    event = db.execute(insert, (tech,)).fetchone()["id"]
    with pytest.raises(UniqueViolation):
        db.execute(insert, (tech,))
    for change in ("update technology_events set summary = 'x' where id = %s", "delete from technology_events where id = %s"):
        with pytest.raises(RaiseException):
            db.execute(change, (event,))


def test_the_append_only_trigger_function_has_a_fixed_search_path(db) -> None:
    config = db.execute("select proconfig from pg_proc where proname = 'forbid_change' "
                        "and pronamespace = current_schema()::regnamespace").fetchone()["proconfig"]
    assert config == ['search_path=""']


def test_a_claim_field_outside_the_profile_is_rejected(db) -> None:
    tech = _technology(db, "Ollama")
    with pytest.raises(CheckViolation):
        db.execute("insert into technology_claims (technology_id, field, content) values (%s, 'rumor', 'x')", (tech,))


def test_importance_is_one_to_five_and_knowledge_is_one_row_per_user_and_technology(db) -> None:
    tech, user = _technology(db, "Ollama"), db.execute("select id from users").fetchone()["id"]
    db.execute("insert into user_knowledge (user_id, technology_id, state, importance) values (%s, %s, 'saved', 5)", (user, tech))
    with pytest.raises(UniqueViolation):
        db.execute("insert into user_knowledge (user_id, technology_id) values (%s, %s)", (user, tech))
    with pytest.raises(CheckViolation):
        db.execute("update user_knowledge set importance = 6 where technology_id = %s", (tech,))


def test_row_level_security_is_on_for_stage3_tables(db) -> None:
    rows = db.execute(
        "select relname from pg_class where relnamespace = current_schema()::regnamespace and relrowsecurity"
    ).fetchall()
    stage3 = {"users", "technology_claims", "claim_evidence", "technology_events", "user_knowledge", "user_notes"}
    assert stage3 <= {r["relname"] for r in rows}


def test_claim_evidence_points_at_a_real_item(db) -> None:
    tech = _technology(db, "Ollama")
    claim = db.execute("insert into technology_claims (technology_id, field, content) values (%s, 'one_liner', 'x') "
                       "returning id", (tech,)).fetchone()["id"]
    db.execute("insert into claim_evidence (claim_id, source_item_id) values (%s, %s)", (claim, _item(db)))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("insert into claim_evidence (claim_id, source_item_id) values (%s, 999999)", (claim,))
