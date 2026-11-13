"""4단계 관계 뽑기(SPEC v1.7): 후보 고르기, 모델 출력 검사, 저장·검토 목록, 확인·숨김, 직접 잇기."""
from collections import deque

from radar.adapters.page import Page
from radar.domain.relations import MAX_CANDIDATES, Proposed, find_candidates, parse_relations
from radar.holds import set_type
from radar.profile import ProfileEnv, run_profile_job
from radar.store import relations
from radar.store.technologies import TechnologyStore
from tests.conftest import Clock, FakeLlm, add_item
from tests.test_profile import _answer, _job

TEXT = "Ollama is built on llama.cpp. It works with Open WebUI.\nGo bindings exist."
NAMES = {2: ["llama.cpp"], 3: ["Open WebUI"]}


def _tech(db, name: str, type_: str | None = "PRODUCT", url: str | None = None) -> tuple[int, int]:
    tech, _ = TechnologyStore(db).create(name, type_)
    item = add_item(db, kind="hackernews", url=url or f"https://{name.lower().replace(' ', '')}.example/", title=name)
    TechnologyStore(db).link(tech, item, "announcement")
    return tech, item


def _rel(other: int, type_: str = "BUILT_ON", subject: bool = True, quote: str = "Ollama is built on llama.cpp.") -> dict:
    return {"other_id": other, "relation_type": type_, "this_is_subject": subject, "quote": quote}


def test_candidates_are_whole_word_mentions_most_frequent_first() -> None:
    known = [(1, ["Ollama"]), (2, ["llama.cpp", "llamacpp"]), (3, ["Open WebUI"]), (4, ["Go"]), (5, ["go"]), (6, ["Llama"])]
    found = find_candidates(TEXT + " llama.cpp again", known, self_id=1)  # 'go'는 대소문자가 달라 뺀다, Llama는 낱말이 아니다
    assert list(found) == [2, 3, 4] and sorted(found[2]) == ["llama.cpp", "llamacpp"]
    assert list(find_candidates("Built with Claude\nCode today", [(7, ["Claude Code"])], self_id=1)) == [7]  # 줄바꿈은 공백
    many = [(i, [f"name{i}"]) for i in range(30)]
    assert len(find_candidates(" ".join(f"name{i}" for i in range(30)), many, self_id=-1)) == MAX_CANDIDATES


def test_only_candidate_ids_and_quotes_really_on_the_page_survive() -> None:
    raw = [_rel(2), _rel(9), _rel(2, "EXTENDS", quote="Ollama extends llama.cpp."), {"other_id": 3}, "junk",
           _rel(3, "INTEGRATES_WITH", quote="It   works with\nOpen WebUI.")]
    kept, dropped = parse_relations(raw, 5, NAMES, TEXT)
    assert kept == [Proposed(5, 2, "BUILT_ON", "Ollama is built on llama.cpp."),
                    Proposed(3, 5, "INTEGRATES_WITH", "It works with Open WebUI.")]  # 대칭은 작은 id가 주어
    assert dropped == 4


def test_a_quote_must_be_a_real_sentence_that_names_the_other_technology() -> None:
    page = "Ollama is built on llama.cpp. Fast and private local inference for everyone."
    short, unnamed = _rel(2, quote="llama.cpp"), _rel(2, quote="Fast and private local inference for everyone.")
    assert parse_relations([short, unnamed], 5, NAMES, page) == ([], 2)


def test_an_install_command_is_not_evidence_of_a_relation() -> None:
    page = ("git clone https://github.com/a/ollama-ui && cd ollama-ui\n$ pip install llama.cpp-python bindings\n"
            "Run it with git clone first, then point it at llama.cpp for inference.")
    names = {2: ["llama.cpp"], 4: ["Git"]}
    raw = [_rel(4, "INTEGRATES_WITH", quote="git clone https://github.com/a/ollama-ui && cd ollama-ui"),
           _rel(2, quote="$ pip install llama.cpp-python bindings"),
           _rel(2, quote="Run it with git clone first, then point it at llama.cpp for inference.")]
    kept, dropped = parse_relations(raw, 5, names, page)
    assert [k.target_id for k in kept] == [2] and dropped == 2  # 명령으로 시작하는 줄만 버린다


def test_candidates_carry_what_the_technology_is(db) -> None:
    tech, _ = _tech(db, "LLM")
    assert relations.describe(db, [tech, 999999]) == [{"id": tech, "name": "LLM", "type": "PRODUCT", "what": ""}]


def test_the_candidate_is_the_subject_when_the_model_says_so() -> None:
    kept, _ = parse_relations([_rel(2, "REPLACES", subject=False)], 5, NAMES, TEXT)
    assert (kept[0].source_id, kept[0].target_id) == (2, 5)


def test_a_profile_saves_relations_with_evidence_and_queues_rule_breakers(db) -> None:
    ollama, item = _tech(db, "Ollama")
    llama, _ = _tech(db, "llama.cpp", "FRAMEWORK_LIBRARY")
    webui, _ = _tech(db, "Open WebUI", None)  # 유형 없음 → 검토 목록
    page = Page("https://ollama.example/", "Ollama", "", TEXT)
    answer = _answer([_rel(llama), _rel(webui, "INTEGRATES_WITH", quote="It works with Open WebUI."),
                      _rel(llama, "IMPLEMENTS")], one_liner="로컬 LLM 실행 도구")
    llm = FakeLlm(answer)
    run_profile_job(ProfileEnv(db, llm, Clock(), lambda url: page, lambda repo: page, deque()), _job(ollama))
    sent = llm.calls[0][1]
    assert f'"id": {llama}' in sent and f'"id": {webui}' in sent and '"type": "NONE"' in sent and f'"id": {ollama}' not in sent
    rows = relations.for_technology(db, ollama)
    assert [(r["relation_type"], r["origin"], r["user_verified"]) for r in rows] == [("BUILT_ON", "ai", False)]
    assert rows[0]["evidence"] == [{"item": item, "url": "https://ollama.example/", "quote": "Ollama is built on llama.cpp."}]
    queued = {(p["relation_type"], p["reason"]) for p in relations.open_proposals(db)}
    assert queued == {("INTEGRATES_WITH", "untyped"), ("IMPLEMENTS", "matrix")}
    assert set_type(db, webui, "PRODUCT")  # 유형을 정하면 다시 검사해 관계가 된다
    assert {r["relation_type"] for r in relations.for_technology(db, ollama)} == {"BUILT_ON", "INTEGRATES_WITH"}
    assert {p["relation_type"] for p in relations.open_proposals(db)} == {"IMPLEMENTS"}


def test_extracting_again_adds_evidence_but_keeps_the_users_choices(db) -> None:
    a, item_a = _tech(db, "Ollama")
    b, item_b = _tech(db, "llama.cpp", "FRAMEWORK_LIBRARY")
    p = Proposed(a, b, "BUILT_ON", "Ollama is built on llama.cpp.")
    relations.save_ai(db, [p], item_a, None)
    rid = relations.for_technology(db, a)[0]["id"]
    assert relations.change(db, rid, "verify") and relations.change(db, rid, "hide")
    relations.save_ai(db, [p], item_b, None)
    row = relations.for_technology(db, a)[0]
    assert (row["user_verified"], row["hidden_at"] is not None, len(row["evidence"])) == (True, True, 2)
    assert not relations.change(db, rid, "delete")


def test_manual_links_are_verified_need_no_evidence_and_respect_the_matrix(db) -> None:
    app, _ = _tech(db, "App")
    lib, _ = _tech(db, "Lib", "FRAMEWORK_LIBRARY")
    held, _ = _tech(db, "Held", None)
    assert relations.add_manual(db, lib, app, "IMPLEMENTS")[0] is False  # 대상이 표준이 아니다
    assert relations.add_manual(db, app, held, "RELATED_TO")[0] is False
    assert relations.add_manual(db, app, app, "RELATED_TO")[0] is False
    assert relations.add_manual(db, max(app, lib), min(app, lib), "COMPETES_WITH") == (True, "관계를 이었습니다")
    row = relations.for_technology(db, app)[0]
    assert (row["origin"], row["user_verified"], row["evidence"], row["source_technology_id"]) == ("manual", True, [], min(app, lib))
    assert db.execute("select count(*)::int as n from relations_without_evidence").fetchone()["n"] == 0


def test_a_discarded_proposal_leaves_the_review_list_and_does_not_come_back(db) -> None:
    a, item = _tech(db, "A")
    b, _ = _tech(db, "B", None)
    relations.save_ai(db, [Proposed(a, b, "EXTENDS", "q")], item, None)
    proposal = relations.open_proposals(db)[0]["id"]
    assert relations.discard(db, proposal) and not relations.discard(db, proposal)
    relations.save_ai(db, [Proposed(a, b, "EXTENDS", "q")], item, None)  # 다시 프로필을 만들어도
    assert relations.open_proposals(db) == []


def test_a_proposal_that_still_breaks_the_matrix_after_typing_says_so(db) -> None:
    lib, item = _tech(db, "Lib", "FRAMEWORK_LIBRARY")
    held, _ = _tech(db, "Held", None)
    relations.save_ai(db, [Proposed(lib, held, "IMPLEMENTS", "q")], item, None)
    assert relations.open_proposals(db)[0]["reason"] == "untyped"
    assert set_type(db, held, "PRODUCT")  # IMPLEMENTS의 대상은 표준이어야 한다
    assert relations.open_proposals(db)[0]["reason"] == "matrix"


def test_archived_technologies_cannot_be_linked_by_hand(db) -> None:
    a, _ = _tech(db, "A")
    b, _ = _tech(db, "B")
    db.execute("update technologies set archived_at = now() where id = %s", (a,))
    assert relations.add_manual(db, a, b, "RELATED_TO")[0] is False
