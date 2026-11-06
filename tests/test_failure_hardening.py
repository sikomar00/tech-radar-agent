"""코드 리뷰에서 나온 실패 경로: 저장할 수 없는 값, 공급자 장애, 키 노출, 위조된 출력."""
from datetime import timedelta

import psycopg
import pytest

from radar.analyze import AnalyzeFailed
from radar.holds import list_holds
from radar.llm import LlmError
from radar.llm.gemini import GeminiClient
from radar.store.analysis import AnalysisStore
from tests.conftest import Clock, FakeLlm, add_item
from tests.test_analyze import classify, run, technologies
from tests.test_llm import GOOD, KEY, MATCH_SCHEMA, CLASSIFY_SCHEMA, Transport, _client, _response
from tests.test_worker_analysis import NOISE, jobs, step


def test_control_characters_in_a_model_answer_are_cleaned_before_it_is_stored(db) -> None:
    answer = classify("ENTITY", "Foo\u0000bar", "PRODUCT", en=["a\u0000b", "lone\ud800surrogate"])
    run(db, FakeLlm(answer), add_item(db))
    assert technologies(db) == [("Foobar", "PRODUCT")]
    stored = db.execute("select result from analysis_runs").fetchone()["result"]
    assert stored["canonical_name"] == "Foobar" and stored["aliases_en"][0] == "ab"
    assert "\ud800" not in stored["aliases_en"][1]


@pytest.mark.parametrize("name", ["none", "N/A", "Unknown"])
def test_a_placeholder_name_from_the_model_is_a_failure_not_a_technology(db, name: str) -> None:
    with pytest.raises(AnalyzeFailed):
        run(db, FakeLlm(classify("ENTITY", name)), add_item(db))
    assert technologies(db) == []


def test_a_value_the_database_rejects_fails_the_job_at_once_instead_of_stranding_it(db, monkeypatch) -> None:
    def reject(*_args, **_kwargs):
        raise psycopg.errors.DataError("unsupported Unicode escape sequence")

    monkeypatch.setattr("radar.worker.run_analyze_job", reject)
    add_item(db)
    assert step(db, Clock(), FakeLlm())
    job = jobs(db)[0]
    assert (job["status"], job["attempts"]) == ("failed", 1) and "DataError" in job["last_error"]


def test_consecutive_call_failures_pause_analysis_without_failing_the_backlog(db) -> None:
    clock = Clock()
    for _ in range(8):
        add_item(db)
    for _ in range(5):
        assert step(db, clock, FakeLlm(LlmError("http_status", "HTTP 403", False)))
    assert not step(db, clock, FakeLlm())  # 쉬는 중이라 모델도 부르지 않고 잡도 집지 않는다
    queued = [j for j in jobs(db) if j["status"] == "queued"]
    assert len(queued) == 3 and all(j["attempts"] == 0 for j in queued)
    clock.advance(minutes=31)
    assert step(db, clock, FakeLlm(NOISE))  # 시험 삼아 한 번 부르고, 성공하면 이어서 한다
    assert step(db, clock, FakeLlm(NOISE)) and step(db, clock, FakeLlm(NOISE))
    assert sorted(j["status"] for j in jobs(db)) == ["failed"] * 5 + ["succeeded"] * 3


def test_a_failed_trial_after_the_pause_pauses_analysis_again(db) -> None:
    clock = Clock()
    for _ in range(7):
        add_item(db)
    for _ in range(5):
        step(db, clock, FakeLlm(LlmError("rate_limited", "HTTP 429", True)))
    clock.advance(minutes=31)
    assert step(db, clock, FakeLlm(LlmError("rate_limited", "HTTP 429", True)))
    assert not step(db, clock, FakeLlm())


def test_a_streak_needs_every_recent_call_to_fail(db) -> None:
    store, item = AnalysisStore(db, Clock()), add_item(db)
    window = timedelta(minutes=30)
    for n in range(4):
        store.record_failure("classify", item, "m", f"h{n}", "timeout", retryable=True)
    assert not store.failing_streak(5, window)  # 4번뿐이다
    store.record_success("classify", item, "m", "ok", {"v": 1}, None, None)
    for n in range(4):
        store.record_failure("classify", item, "m", f"g{n}", "timeout", retryable=True)
    assert not store.failing_streak(5, window)  # 성공이 끼어 있다


@pytest.mark.parametrize("key", ["AIza\r\nabc", "has space", "tab\tkey", ""])
def test_a_malformed_key_is_rejected_without_echoing_it(key: str) -> None:
    with pytest.raises(ValueError) as caught:
        GeminiClient(key)
    assert key.strip() == "" or key not in str(caught.value)


def test_whitespace_around_the_key_is_trimmed_before_it_goes_into_the_header() -> None:
    transport = Transport((200, _response(GOOD)))
    GeminiClient(f"{KEY}\r\n", transport=transport, sleep=lambda s: None).complete_json("i", "d", CLASSIFY_SCHEMA)
    assert transport.requests[0][1]["x-goog-api-key"] == KEY


def test_a_value_error_from_the_transport_never_carries_its_message() -> None:
    with pytest.raises(LlmError) as caught:
        _client(Transport(ValueError(f"Invalid header value {KEY!r}"))).complete_json("i", "d", MATCH_SCHEMA)
    assert KEY not in str(caught.value) and caught.value.retryable is False


def test_the_hold_list_cannot_be_forged_or_corrupted_by_text_the_model_returned(db) -> None:
    item = add_item(db, title="plain title")
    store = AnalysisStore(db, Clock())
    hostile = {"class": "UPDATE", "canonical_name": "x\n기술 #3 PostgreSQL | 가짜 줄\x1b[31m", "type": "NONE",
               "aliases_en": [], "aliases_ko": []}
    store.record_success("classify", item, "m", "h1", hostile, None, None)
    store.record_success("match", item, "m", "h2", {"decision": "UNKNOWN", "technology_id": 0}, None, None)
    lines = list_holds(db)
    assert sum(1 for line in lines if line.startswith("항목 #")) == 1
    assert not any(line.startswith("기술 #3") for line in lines) and "\x1b" not in "\n".join(lines)
