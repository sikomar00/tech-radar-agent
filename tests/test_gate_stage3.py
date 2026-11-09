"""3단계 게이트 점검(DB 항목): 출처 연결률, 출처 3개 연결 기술, 별칭 검색, 실패 원인, 근거 없는 주장."""
from radar.gate import stage3_report
from radar.store.knowledge import save_profile
from radar.store.technologies import TechnologyStore
from tests.conftest import add_item


def _three_sourced(db) -> int:
    store = TechnologyStore(db)
    tech, _ = store.create("LangChain", "FRAMEWORK_LIBRARY")
    for kind in ("github", "hackernews", "rss"):
        store.link(tech, add_item(db, kind=kind), "mention")
    db.execute("insert into technology_aliases (technology_id, alias, lang) values (%s, '랭체인', 'ko')", (tech,))
    return tech


def test_a_technology_with_three_sources_and_a_searchable_alias_passes(db) -> None:
    tech = _three_sourced(db)
    save_profile(db, tech, {"one_liner": "프레임워크"}, add_item(db))
    lines, passed = stage3_report(db)
    text = "\n".join(lines)
    assert passed, text
    assert "3개 이상 연결된 기술 1개" in text and "표본 1/1" in text and "프로필이 있는 기술 1/1개" in text


def test_without_aliases_or_three_sources_the_gate_is_not_met(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("Solo", "PRODUCT")
    store.link(tech, add_item(db), "mention")
    lines, passed = stage3_report(db)
    assert not passed and "표본 0/0" in "\n".join(lines)


def test_a_claim_without_evidence_fails_the_gate(db) -> None:
    tech = _three_sourced(db)
    db.execute("insert into technology_claims (technology_id, field, content) values (%s, 'one_liner', 'x')", (tech,))
    lines, passed = stage3_report(db)
    assert not passed and "근거 없는 주장 1건" in "\n".join(lines)
