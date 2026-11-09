"""워커가 분석 잡을 만들고, 한도와 장애를 다루고, 수집 잡을 먼저 집는지 확인한다."""
from datetime import timedelta

import pytest

from radar.llm import LlmError
from radar.store.jobs import ANALYZE, COLLECT, JobQueue
from radar.worker import run_once
from tests.conftest import Clock, FakeHttp, FakeLlm, add_item

NOISE = {"class": "NOISE", "canonical_name": "", "type": "NONE", "aliases_en": [], "aliases_ko": [], "reason": "시험"}


def jobs(db, kind: str = ANALYZE) -> list[dict]:
    return db.execute("select id, status, attempts, run_after, last_error from jobs where kind = %s order by id", (kind,)).fetchall()


def step(db, clock: Clock, llm: FakeLlm | None) -> bool:
    return run_once(db, FakeHttp({}), clock, llm)


def test_each_item_gets_one_analysis_job_and_it_runs(db) -> None:
    clock, llm = Clock(), FakeLlm(NOISE, NOISE)
    add_item(db), add_item(db)
    assert step(db, clock, llm) and step(db, clock, llm)
    assert not step(db, clock, llm)  # 새로 만들 잡도 집을 잡도 없다
    assert [j["status"] for j in jobs(db)] == ["succeeded", "succeeded"] and len(llm.calls) == 2


def test_without_a_model_nothing_is_scheduled_and_queued_analysis_waits(db) -> None:
    clock, queue = Clock(), JobQueue(db, Clock())
    add_item(db)
    assert not step(db, clock, None) and jobs(db) == []
    waiting = queue.enqueue(ANALYZE, {"source_item_id": 1})
    assert not step(db, clock, None)
    assert [(j["id"], j["status"], j["attempts"]) for j in jobs(db)] == [(waiting, "queued", 0)]


def test_the_daily_cap_defers_analysis_to_the_next_provider_day_without_using_attempts(db, monkeypatch) -> None:
    monkeypatch.setattr("radar.analyze.DAILY_CAP", 1)
    clock = Clock()
    clock.now = clock.now.replace(day=6, hour=7, minute=30)  # 태평양 표준시 11-05 23:30
    llm = FakeLlm(NOISE, NOISE)
    add_item(db), add_item(db)
    assert step(db, clock, llm)
    assert not step(db, clock, llm)  # 한도에 닿아서 남은 잡을 집지 않는다
    assert sorted((j["status"], j["attempts"]) for j in jobs(db)) == [("queued", 0), ("succeeded", 1)]
    clock.advance(hours=1)  # 11-06 00:30 PST, 새 하루
    assert step(db, clock, llm)
    assert [j["status"] for j in jobs(db)] == ["succeeded", "succeeded"] and not llm.script


def test_collection_jobs_are_taken_before_older_analysis_jobs(db) -> None:
    queue = JobQueue(db, Clock())
    for n in range(3):
        queue.enqueue(ANALYZE, {"source_item_id": n})
    collect = queue.enqueue(COLLECT, {"source_id": 1})
    assert queue.claim_next()["id"] == collect
    assert queue.claim_next()["kind"] == ANALYZE


def test_skipped_kinds_stay_queued_without_using_an_attempt(db) -> None:
    queue = JobQueue(db, Clock())
    queue.enqueue(ANALYZE, {"source_item_id": 1})
    assert queue.claim_next(skip_kinds=(ANALYZE,)) is None
    assert [(j["status"], j["attempts"]) for j in jobs(db)] == [("queued", 0)]


def test_a_retryable_failure_is_requeued_with_backoff_then_fails_after_three_attempts(db) -> None:
    clock = Clock()
    add_item(db)
    for attempt in (1, 2):
        assert step(db, clock, FakeLlm(LlmError("http_status", "HTTP 503", True)))
        job = jobs(db)[0]
        assert (job["status"], job["attempts"]) == ("queued", attempt)
        assert job["run_after"] == clock.now + timedelta(minutes=2 ** attempt)
        assert not step(db, clock, FakeLlm())  # 기다리는 동안은 집지 않는다
        clock.advance(minutes=2 ** attempt)
    assert step(db, clock, FakeLlm(LlmError("http_status", "HTTP 503", True)))
    assert (jobs(db)[0]["status"], jobs(db)[0]["attempts"]) == ("failed", 3)
    assert not step(db, clock, FakeLlm()) and len(jobs(db)) == 1  # 실패한 항목의 잡을 매 주기 다시 만들지 않는다
    failed = db.execute("select count(*) as n from analysis_runs where status = 'failed' and retryable").fetchone()["n"]
    assert failed == 3


def test_failed_jobs_of_one_kind_can_be_revived_together(db) -> None:
    clock, queue = Clock(), JobQueue(db, Clock())
    a, b = queue.enqueue(ANALYZE, {"source_item_id": 1}), queue.enqueue(ANALYZE, {"source_item_id": 2})
    c = queue.enqueue(COLLECT, {"source_id": 1})
    db.execute("update jobs set status = 'failed', attempts = 3 where id = any(%s)", ([a, b, c],))
    assert queue.retry_failed_kind(ANALYZE) == 2
    states = {j["id"]: (j["status"], j["attempts"]) for j in jobs(db) + jobs(db, COLLECT)}
    assert states == {a: ("queued", 0), b: ("queued", 0), c: ("failed", 3)}


@pytest.mark.parametrize("llm", [None, FakeLlm()])
def test_an_unknown_job_kind_fails_without_retry(db, llm) -> None:
    queue = JobQueue(db, Clock())
    queue.enqueue("mystery", {})
    assert step(db, Clock(), llm)
    job = jobs(db, "mystery")[0]
    assert (job["status"], job["attempts"]) == ("failed", 1) and "mystery" in job["last_error"]
