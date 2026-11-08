"""0002_stage2.sql이 DB에서 직접 막는 규칙(ERD "DB가 직접 막는 것")과 pg_trgm 동작을 확인한다."""
import psycopg
import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb


def _item(db) -> int:
    source = db.execute("insert into sources (name, kind) values ('s', 'rss') returning id").fetchone()["id"]
    return db.execute(
        "insert into source_items (source_id, url, normalized_url, title, content_hash) "
        "values (%s, 'https://a.example/1', 'a.example/1', 't', 'h1') returning id", (source,)
    ).fetchone()["id"]


def _technology(db, name: str, type_: str | None = "PRODUCT") -> int:
    return db.execute(
        "insert into technologies (canonical_name, type) values (%s, %s) returning id", (name, type_)
    ).fetchone()["id"]


def _analysis(db, item: int, *, status: str = "succeeded", current: bool = False, result: dict | None = None) -> None:
    db.execute(
        "insert into analysis_runs (kind, source_item_id, model, input_hash, status, result, is_current) "
        "values ('classify', %s, 'm', 'h', %s, %s, %s)",
        (item, status, Jsonb(result) if result is not None else None, current),
    )


def test_pg_trgm_similarity_works_in_the_test_schema(db) -> None:
    tech = _technology(db, "LangChain")
    db.execute("insert into technology_aliases (technology_id, alias, lang) values (%s, 'langchain', 'en')", (tech,))
    near = db.execute("select alias from technology_aliases where alias % 'langchan'").fetchall()
    assert [r["alias"] for r in near] == ["langchain"]
    # 한 글자가 빠진 오타도 0.58이라 SPEC의 초기 임계값 0.6에는 못 미친다. 이 시험은 함수가 보이는지만 본다(0.6은 튜닝 대상)
    assert db.execute("select similarity('langchain', 'langchan') as s").fetchone()["s"] > 0.5


def test_canonical_names_are_unique_ignoring_case(db) -> None:
    _technology(db, "Next.js")
    with pytest.raises(UniqueViolation):
        _technology(db, "next.JS")


def test_only_one_current_analysis_per_item_and_kind(db) -> None:
    item = _item(db)
    _analysis(db, item, current=True, result={"class": "NOISE"})
    _analysis(db, item, current=False, result={"class": "ENTITY"})  # 이전 결과는 얼마든지 쌓인다
    with pytest.raises(UniqueViolation):
        _analysis(db, item, current=True, result={"class": "ENTITY"})


def test_failed_analysis_cannot_be_current_and_success_needs_a_result(db) -> None:
    item = _item(db)
    with pytest.raises(CheckViolation):
        _analysis(db, item, status="failed", current=True, result={"x": 1})
    with pytest.raises(CheckViolation):
        _analysis(db, item, status="succeeded", result=None)


def test_check_views_find_pending_and_sourceless_technologies(db) -> None:
    pending = _technology(db, "Held", None)
    with_source = _technology(db, "Linked")
    db.execute(
        "insert into technology_sources (technology_id, source_item_id, evidence_type) values (%s, %s, 'mention')",
        (with_source, _item(db)),
    )
    assert [r["id"] for r in db.execute("select id from pending_classification")] == [pending]
    assert [r["id"] for r in db.execute("select id from technologies_without_source")] == [pending]


def test_row_level_security_is_on_for_stage2_tables(db) -> None:
    rows = db.execute(
        "select relname from pg_class where relnamespace = current_schema()::regnamespace and relrowsecurity"
    ).fetchall()
    enabled = {r["relname"] for r in rows}
    assert {"technologies", "technology_aliases", "analysis_runs", "technology_sources", "schema_migrations"} <= enabled


def test_technology_type_must_be_a_known_enum_value(db) -> None:
    """유형은 enum이라 오타가 저장되지 않는다."""
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        _technology(db, "Bad", "PRODUCTT")
