"""run_analyze_job: 분류 → 매칭 → 기술 연결 흐름. 모델은 FakeLlm이다(네트워크 없음)."""
import pytest

from radar.analyze import AnalyzeEnv, AnalyzeFailed, run_analyze_job
from radar.domain.analysis import DATA_END, DATA_START
from radar.domain.prompts import CLASSIFY_INSTRUCTION
from radar.llm import LlmError
from radar.store.technologies import TechnologyStore
from tests.conftest import Clock, FakeLlm, add_item, fixture_json


def classify(label: str = "ENTITY", name: str = "Ollama", type_: str = "PRODUCT", en=(), ko=()) -> dict:
    return {"class": label, "canonical_name": name, "type": type_, "aliases_en": list(en), "aliases_ko": list(ko),
            "reason": "시험용 이유"}


def decide(decision: str, technology_id: int = 0) -> dict:
    return {"decision": decision, "technology_id": technology_id}


def run(db, llm: FakeLlm, item: int, clock: Clock | None = None) -> None:
    run_analyze_job(AnalyzeEnv(db, llm, clock or Clock()), {"id": 1, "payload": {"source_item_id": item}})
    assert not llm.script, "쓰이지 않은 모델 응답이 남았다"


def technologies(db) -> list[tuple]:
    return [(r["canonical_name"], r["type"]) for r in db.execute("select canonical_name, type from technologies order by id")]


def runs(db, **where) -> list[dict]:
    clauses = " and ".join(f"{k} = %s" for k in where) or "true"
    query = f"select kind, status, retryable, is_current, error from analysis_runs where {clauses} order by id"
    return db.execute(query, tuple(where.values())).fetchall()


def sources(db) -> int:
    return db.execute("select count(*) as n from technology_sources").fetchone()["n"]


def test_a_non_candidate_is_recorded_and_creates_nothing(db) -> None:
    run(db, FakeLlm(classify("NOISE", "none")), add_item(db))
    assert technologies(db) == [] and sources(db) == 0
    assert [(r["kind"], r["status"], r["is_current"]) for r in runs(db)] == [("classify", "succeeded", True)]


def test_a_new_entity_creates_a_technology_with_aliases_and_an_unverified_link(db) -> None:
    item = add_item(db, kind="hackernews", title="Show HN: Ollama")
    llm = FakeLlm(classify("ENTITY", "Ollama", "PRODUCT", en=["ollama-cli"], ko=["올라마"]))
    run(db, llm, item)
    assert len(llm.calls) == 1  # 비교할 기술이 없으면 매칭 호출을 하지 않는다
    assert technologies(db) == [("Ollama", "PRODUCT")]
    aliases = {(r["alias"], r["lang"]) for r in db.execute("select alias, lang from technology_aliases")}
    assert aliases == {("ollama-cli", "en"), ("올라마", "ko")}
    link = db.execute("select evidence_type, confidence from technology_sources").fetchone()
    assert (link["evidence_type"], link["confidence"]) == ("announcement", "Unverified")


def test_a_candidate_without_a_type_becomes_a_hold(db) -> None:
    run(db, FakeLlm(classify("UPDATE", "ERNIE 5.5", "NONE")), add_item(db))
    assert technologies(db) == [("ERNIE 5.5", None)]
    assert db.execute("select count(*) as n from pending_classification").fetchone()["n"] == 1


def test_the_same_name_is_matched_by_rule_without_asking_the_model(db) -> None:
    existing, _ = TechnologyStore(db).create("Ollama", "PRODUCT")
    llm = FakeLlm(classify("UPDATE", "ollama", "PRODUCT", ko=["올라마"]))
    run(db, llm, add_item(db, kind="rss"))
    assert len(llm.calls) == 1 and technologies(db) == [("Ollama", "PRODUCT")]
    assert db.execute("select technology_id from technology_sources").fetchone()["technology_id"] == existing
    assert [r["alias"] for r in db.execute("select alias from technology_aliases")] == ["올라마"]


def test_a_release_of_a_linked_github_repo_joins_that_technology_even_under_another_name(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("Claude Code", "PRODUCT")
    repo = add_item(db, kind="github", url="https://github.com/anthropics/claude-code", metadata={"kind": "new_repo"})
    store.link(tech, repo, "official")
    release = add_item(db, kind="github", url="https://github.com/anthropics/claude-code/releases/tag/v1",
                       metadata={"kind": "release", "repo": "anthropics/claude-code", "tag": "v1", "prerelease": False})
    llm = FakeLlm(classify("UPDATE", "Claude Code CLI", "PRODUCT"))
    run(db, llm, release)
    assert len(llm.calls) == 1 and technologies(db) == [("Claude Code", "PRODUCT")]
    linked = db.execute("select evidence_type from technology_sources where source_item_id = %s", (release,)).fetchone()
    assert linked["evidence_type"] == "release"
    assert [r["alias"] for r in db.execute("select alias from technology_aliases")] == ["Claude Code CLI"]


def _langchain(db) -> int:
    return TechnologyStore(db).create("LangChain", "FRAMEWORK_LIBRARY")[0]


def _core(decision: dict) -> FakeLlm:
    return FakeLlm(classify("UPDATE", "langchain-core", "FRAMEWORK_LIBRARY", en=["LangChain"]), decision)


def test_a_fuzzy_candidate_is_sent_to_the_model_and_same_links_to_it(db) -> None:
    tech = _langchain(db)
    llm = _core(decide("SAME", tech))
    run(db, llm, add_item(db))
    assert f'"id": {tech}' in llm.calls[1][1] and DATA_START in llm.calls[1][1]
    assert technologies(db) == [("LangChain", "FRAMEWORK_LIBRARY")]
    assert [r["alias"] for r in db.execute("select alias from technology_aliases")] == ["langchain-core"]


def test_different_creates_a_separate_technology(db) -> None:
    _langchain(db)
    run(db, _core(decide("DIFFERENT")), add_item(db))
    assert technologies(db) == [("LangChain", "FRAMEWORK_LIBRARY"), ("langchain-core", "FRAMEWORK_LIBRARY")]
    assert sources(db) == 1


def test_unknown_creates_no_technology_and_no_link(db) -> None:
    _langchain(db)
    run(db, _core(decide("UNKNOWN")), add_item(db))
    assert technologies(db) == [("LangChain", "FRAMEWORK_LIBRARY")] and sources(db) == 0


def test_an_id_outside_the_candidate_list_is_rejected_and_recorded_as_a_retryable_failure(db) -> None:
    _langchain(db)
    item = add_item(db)
    with pytest.raises(AnalyzeFailed) as caught:
        run(db, _core(decide("SAME", 999999)), item)
    assert caught.value.retryable
    assert [(r["kind"], r["status"], r["retryable"]) for r in runs(db, source_item_id=item)] == [
        ("classify", "succeeded", False), ("match", "failed", True)]
    assert sources(db) == 0 and len(technologies(db)) == 1


@pytest.mark.parametrize("bad", [{**classify(), "class": "PWNED"}, {**classify(), "tool_call": "delete_all"}, {"class": "ENTITY"}])
def test_output_that_breaks_the_schema_is_not_stored_as_a_result(db, bad: dict) -> None:
    with pytest.raises(AnalyzeFailed) as caught:
        run(db, FakeLlm(bad), add_item(db))
    assert caught.value.retryable and technologies(db) == []
    assert [(r["status"], r["retryable"], r["is_current"]) for r in runs(db)] == [("failed", True, False)]
    assert runs(db)[0]["error"].startswith("schema")


@pytest.mark.parametrize("error, retryable", [
    (LlmError("rate_limited", "HTTP 429", True), True), (LlmError("http_status", "HTTP 503", True), True),
    (LlmError("timeout", "TimeoutError", True), True), (LlmError("http_status", "HTTP 403", False), False),
])
def test_call_failures_are_recorded_with_their_retryability(db, error: LlmError, retryable: bool) -> None:
    with pytest.raises(AnalyzeFailed) as caught:
        run(db, FakeLlm(error), add_item(db))
    assert caught.value.retryable is retryable
    assert [(r["status"], r["retryable"]) for r in runs(db)] == [("failed", retryable)]


def test_a_candidate_with_an_empty_name_is_a_failure_not_a_technology(db) -> None:
    with pytest.raises(AnalyzeFailed):
        run(db, FakeLlm(classify("ENTITY", "   ")), add_item(db))
    assert technologies(db) == [] and runs(db)[0]["error"].startswith("invalid")


def test_running_the_same_job_again_does_not_call_the_model_or_add_rows(db) -> None:
    item = add_item(db)
    run(db, FakeLlm(classify("ENTITY", "Ollama", "PRODUCT")), item)
    before = (len(runs(db)), len(technologies(db)), sources(db))
    run(db, FakeLlm(), item)  # 응답이 없는 가짜라 호출하면 시험이 실패한다
    assert (len(runs(db)), len(technologies(db)), sources(db)) == before == (1, 1, 1)


def test_a_failed_match_resumes_without_asking_to_classify_again(db) -> None:
    tech = _langchain(db)
    item = add_item(db)
    with pytest.raises(AnalyzeFailed):
        run(db, _core(LlmError("http_status", "HTTP 503", True)), item)
    llm = FakeLlm(decide("SAME", tech))  # 분류 응답은 없다. 다시 부르면 시험이 실패한다
    run(db, llm, item)
    assert len(llm.calls) == 1 and sources(db) == 1
    assert [(r["status"], r["is_current"]) for r in runs(db, kind="match")] == [("failed", False), ("succeeded", True)]


def test_a_new_result_replaces_the_current_one_when_the_input_changes(db) -> None:
    item = add_item(db)
    run(db, FakeLlm(classify("NOISE", "")), item)
    db.execute("update source_items set title = 'a different title' where id = %s", (item,))
    run(db, FakeLlm(classify("USAGE", "")), item)
    assert [(r["status"], r["is_current"]) for r in runs(db)] == [("succeeded", False), ("succeeded", True)]


@pytest.mark.parametrize("payload", [{}, {"source_item_id": "x"}, {"source_item_id": 999999}])
def test_a_job_for_a_missing_item_is_not_retried(db, payload: dict) -> None:
    with pytest.raises(AnalyzeFailed) as caught:
        run_analyze_job(AnalyzeEnv(db, FakeLlm(), Clock()), {"id": 1, "payload": payload})
    assert caught.value.retryable is False


INJECTIONS = fixture_json("injection/items.json")


@pytest.mark.parametrize("item", INJECTIONS, ids=[i["name"] for i in INJECTIONS])
def test_injected_items_reach_the_model_only_as_a_delimited_data_section(db, item: dict) -> None:
    row = add_item(db, kind=item["source"], title=item["title"], excerpt=item["excerpt"], metadata=item["metadata"])
    llm = FakeLlm({**classify("ENTITY", "PWNED"), "tool_call": "delete_all_data"})  # 모델이 속았다고 가정한 출력
    with pytest.raises(AnalyzeFailed):
        run(db, llm, row)
    instruction, data, _ = llm.calls[0]
    assert instruction == CLASSIFY_INSTRUCTION and data.startswith(DATA_START) and data.endswith(DATA_END)
    assert data.count(DATA_START) == 1 and data.count(DATA_END) == 1
    assert technologies(db) == [] and runs(db)[0]["status"] == "failed"


def test_an_alias_alone_never_decides_the_match_the_model_is_asked(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("Claude Code", "PRODUCT")
    store.add_aliases(tech, ["cc-cli"], [])
    llm = FakeLlm(classify("UPDATE", "cc-cli", "PRODUCT"), decide("SAME", tech))
    run(db, llm, add_item(db))
    assert len(llm.calls) == 2 and f'"id": {tech}' in llm.calls[1][1]
    assert technologies(db) == [("Claude Code", "PRODUCT")]


def test_a_poisoned_alias_cannot_swallow_the_real_item_that_later_uses_that_name(db) -> None:
    """악의적인 글이 아직 없는 이름(Zed)을 다른 기술(Foo)의 별칭으로 심어도, 진짜 Zed 글은 자동으로 Foo에 합쳐지지 않는다."""
    foo, _ = TechnologyStore(db).create("Foo", "PRODUCT")
    db.execute("insert into technology_aliases (technology_id, alias, lang) values (%s, 'Zed', 'en')", (foo,))
    llm = FakeLlm(classify("ENTITY", "Zed", "PRODUCT"), decide("DIFFERENT"))
    run(db, llm, add_item(db, title="Zed, a high-performance code editor"))
    assert len(llm.calls) == 2  # 별칭이 같다는 이유로 확정하지 않고 모델에게 물었다
    assert technologies(db) == [("Foo", "PRODUCT"), ("Zed", "PRODUCT")]
