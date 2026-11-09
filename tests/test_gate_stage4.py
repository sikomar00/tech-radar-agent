"""4단계 게이트 점검: 매트릭스 위반 0, 근거 없는 AI 관계 0, 그래프 선 수 일치, 판정 표본 50개 모두 판정."""
from radar.domain.relations import Proposed
from radar.gate import stage4_report
from radar.store import relations
from radar.store.graph import SAMPLE_SIZE, freeze_sample
from radar.store.technologies import TechnologyStore
from tests.conftest import add_item


def _many_relations(db, count: int) -> list[int]:
    store, item = TechnologyStore(db), add_item(db)
    hub, _ = store.create("Hub", "FRAMEWORK_LIBRARY")
    ids = [store.create(f"App {n}", "PRODUCT")[0] for n in range(count)]
    relations.save_ai(db, [Proposed(t, hub, "BUILT_ON", "q") for t in ids], item, None)
    return [r["id"] for r in db.execute("select id from technology_relations").fetchall()]


def test_the_gate_waits_until_the_whole_sample_is_judged(db) -> None:
    rows = _many_relations(db, SAMPLE_SIZE + 2)
    assert not stage4_report(db)[1]  # 표본을 고정하기 전
    freeze_sample(db)
    lines, passed = stage4_report(db)
    assert not passed and "판정 표본 0/50개" in "\n".join(lines)
    for n, rid in enumerate(rows):
        relations.change(db, rid, "hide" if n % 5 == 0 else "verify")
    lines, passed = stage4_report(db)
    report = "\n".join(lines)
    assert passed, report
    assert "허용 매트릭스를 어긴 관계 0건" in report and "근거 없는 AI 관계 0건" in report


def test_an_ai_relation_without_evidence_fails_the_gate(db) -> None:
    rows = _many_relations(db, SAMPLE_SIZE)
    freeze_sample(db)
    for rid in rows:
        relations.change(db, rid, "verify")
    db.execute("delete from relation_evidence where relation_id = %s", (rows[0],))
    lines, passed = stage4_report(db)
    assert not passed and "근거 없는 AI 관계 1건" in "\n".join(lines)
