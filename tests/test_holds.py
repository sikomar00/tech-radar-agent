"""분류 보류 큐 명령: 보여 주기, 유형 지정, 기존 기술에 잇기, 새 기술 만들기."""
from radar.analyze import AnalyzeEnv, run_analyze_job
from radar.holds import link_item, list_holds, new_from_item, set_type
from radar.store.technologies import TechnologyStore
from tests.conftest import Clock, FakeLlm, add_item


def analyze(db, item: int, *script: dict) -> None:
    run_analyze_job(AnalyzeEnv(db, FakeLlm(*script), Clock()), {"id": 1, "payload": {"source_item_id": item}})


def classified(label: str, name: str, type_: str = "FRAMEWORK_LIBRARY", en=()) -> dict:
    return {"class": label, "canonical_name": name, "type": type_, "aliases_en": list(en), "aliases_ko": [], "reason": "시험"}


def unknown_item(db, title: str = "langchain-core 1.0") -> tuple[int, int]:
    """LangChain이 있는 상태에서 모델이 UNKNOWN이라고 한 항목. (항목 id, LangChain id)를 돌려준다."""
    tech = TechnologyStore(db).create("LangChain", "FRAMEWORK_LIBRARY")[0]
    item = add_item(db, title=title)
    analyze(db, item, classified("UPDATE", "langchain-core", en=["LangChain"]), {"decision": "UNKNOWN", "technology_id": 0})
    return item, tech


def text(db) -> str:
    return "\n".join(list_holds(db))


def test_the_list_shows_untyped_technologies_and_unknown_items(db) -> None:
    analyze(db, add_item(db), classified("UPDATE", "ERNIE 5.5", "NONE"))
    item, _ = unknown_item(db)
    out = text(db)
    assert "ERNIE 5.5" in out and f"항목 #{item}" in out and "langchain-core" in out


def test_an_empty_queue_says_so(db) -> None:
    assert text(db).count("(없음)") == 2


def test_set_type_only_fills_an_empty_type(db) -> None:
    analyze(db, add_item(db), classified("UPDATE", "ERNIE 5.5", "NONE"))
    tech = db.execute("select id from pending_classification").fetchone()["id"]
    assert set_type(db, tech, "SOMETHING")[0] is False
    assert set_type(db, tech, "PRODUCT")[0] is True
    assert db.execute("select type from technologies where id = %s", (tech,)).fetchone()["type"] == "PRODUCT"
    assert set_type(db, tech, "FRAMEWORK_LIBRARY")[0] is False  # 이미 정해진 유형은 바꾸지 않는다
    assert set_type(db, 999999, "PRODUCT")[0] is False
    assert "(없음)" in text(db)


def test_link_attaches_a_held_item_to_a_technology_and_removes_it_from_the_queue(db) -> None:
    item, tech = unknown_item(db)
    ok, _ = link_item(db, Clock(), item, tech)
    assert ok
    row = db.execute("select technology_id, evidence_type, confidence from technology_sources where source_item_id = %s", (item,)).fetchone()
    assert (row["technology_id"], row["evidence_type"], row["confidence"]) == (tech, "mention", "Unverified")
    assert [r["alias"] for r in db.execute("select alias from technology_aliases")] == ["langchain-core"]
    assert f"항목 #{item}" not in text(db)


def test_link_refuses_a_missing_technology_and_an_item_that_is_not_a_candidate(db) -> None:
    item, tech = unknown_item(db)
    assert link_item(db, Clock(), item, 999999)[0] is False
    noise = add_item(db)
    analyze(db, noise, classified("NOISE", "", "NONE"))
    assert link_item(db, Clock(), noise, tech)[0] is False
    assert link_item(db, Clock(), add_item(db), tech)[0] is False  # 분석 결과가 아직 없다


def test_new_creates_a_technology_from_the_held_item(db) -> None:
    item, _ = unknown_item(db)
    ok, message = new_from_item(db, Clock(), item)
    assert ok and "만들었습니다" in message
    names = [r["canonical_name"] for r in db.execute("select canonical_name from technologies order by id")]
    assert names == ["LangChain", "langchain-core"]
    assert f"항목 #{item}" not in text(db)


def test_new_attaches_to_the_existing_technology_when_the_name_is_taken(db) -> None:
    item, _ = unknown_item(db)
    taken, _ = TechnologyStore(db).create("LangChain-Core", "PRODUCT")  # 보류된 뒤 같은 이름의 기술이 먼저 생겼다
    ok, message = new_from_item(db, Clock(), item)
    assert ok and "찾아 연결" in message
    assert db.execute("select count(*) as n from technologies").fetchone()["n"] == 2
    assert db.execute("select technology_id from technology_sources where source_item_id = %s", (item,)).fetchone()["technology_id"] == taken
