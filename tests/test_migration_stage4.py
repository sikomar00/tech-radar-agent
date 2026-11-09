"""0005_stage4.sql이 DB에서 직접 막는 규칙(매트릭스, 대칭 방향, 근거 점검 뷰, RLS)을 확인한다."""
import pytest
from psycopg.errors import CheckViolation, RaiseException, UniqueViolation

from tests.test_migration_stage2 import _item, _technology

INSERT = ("insert into technology_relations (source_technology_id, target_technology_id, relation_type, origin) "
          "values (%s, %s, %s, %s) returning id")


def _relation(db, source: int, target: int, type_: str, origin: str = "ai") -> int:
    return db.execute(INSERT, (source, target, type_, origin)).fetchone()["id"]


def test_the_adr_0002_matrix_is_seeded(db) -> None:
    assert db.execute("select count(*)::int as n from relation_type_rules").fetchone()["n"] == 61


def test_the_matrix_rejects_disallowed_type_pairs_and_untyped_technologies(db) -> None:
    lib, product, held = _technology(db, "Lib", "FRAMEWORK_LIBRARY"), _technology(db, "App"), _technology(db, "Held", None)
    _relation(db, lib, product, "EXTENDS")
    with pytest.raises(RaiseException):
        _relation(db, lib, product, "IMPLEMENTS")  # 대상이 표준이 아니다
    with pytest.raises(RaiseException):
        _relation(db, min(lib, held), max(lib, held), "RELATED_TO")  # 유형 없는 기술


def test_symmetric_relations_are_stored_low_id_first_once(db) -> None:
    a, b = _technology(db, "A"), _technology(db, "B")
    _relation(db, a, b, "COMPETES_WITH")
    with pytest.raises(CheckViolation):
        _relation(db, b, a, "RELATED_TO")
    with pytest.raises(UniqueViolation):
        _relation(db, a, b, "COMPETES_WITH")


def test_the_evidence_view_checks_only_visible_ai_relations(db) -> None:
    a, b, c = _technology(db, "A"), _technology(db, "B"), _technology(db, "C")
    ai, manual, hidden = _relation(db, a, b, "RELATED_TO"), _relation(db, a, c, "RELATED_TO", "manual"), _relation(db, b, c, "RELATED_TO")
    db.execute("update technology_relations set hidden_at = now() where id = %s", (hidden,))
    assert [r["id"] for r in db.execute("select id from relations_without_evidence").fetchall()] == [ai]
    db.execute("insert into relation_evidence (relation_id, source_item_id, quote) values (%s, %s, 'A works with B')", (ai, _item(db)))
    assert db.execute("select count(*)::int as n from relations_without_evidence").fetchone()["n"] == 0
    assert manual


def test_quotes_are_short_and_only_one_open_proposal_per_relation(db) -> None:
    a, b = _technology(db, "A"), _technology(db, "B", None)
    insert = ("insert into relation_proposals (source_technology_id, target_technology_id, relation_type, reason, quote) "
              "values (%s, %s, 'BUILT_ON', 'untyped', %s)")
    db.execute(insert, (a, b, "short"))
    with pytest.raises(UniqueViolation):
        db.execute(insert, (a, b, "again"))
    with pytest.raises(CheckViolation):
        db.execute(insert.replace("'BUILT_ON'", "'EXTENDS'"), (a, b, "x" * 201))


def test_the_matrix_function_has_a_fixed_search_path_and_rls_is_on(db) -> None:
    config = db.execute("select proconfig from pg_proc where proname = 'check_relation_matrix' "
                        "and pronamespace = current_schema()::regnamespace").fetchone()["proconfig"]
    assert config and config[0].startswith("search_path=")
    rows = db.execute("select relname from pg_class where relnamespace = current_schema()::regnamespace and relrowsecurity").fetchall()
    assert {"relation_type_rules", "technology_relations", "relation_evidence", "relation_proposals"} <= {r["relname"] for r in rows}
