from datetime import timedelta

from radar.store.db import connect
from radar.store.jobs import COLLECT, JobQueue
from tests.conftest import Clock


def _job_row(db, job_id: int) -> dict:
    return db.execute("select * from jobs where id = %s", (job_id,)).fetchone()


def _source(db, name: str = "s1", interval: str = "1 hour", active: bool = True) -> int:
    return db.execute(
        "insert into sources (name, kind, collect_interval, is_active) values (%s, 'rss', %s::interval, %s) returning id",
        (name, interval, active),
    ).fetchone()["id"]


def test_claim_returns_oldest_due_job_and_marks_it_running(db) -> None:
    clock = Clock()
    queue = JobQueue(db, clock)
    later = queue.enqueue(COLLECT, {"source_id": 1}, clock.now + timedelta(minutes=5))
    first = queue.enqueue(COLLECT, {"source_id": 2})
    claimed = queue.claim_next()
    assert claimed["id"] == first and claimed["attempts"] == 1
    assert _job_row(db, first)["status"] == "running"
    assert queue.claim_next() is None  # 남은 잡은 아직 때가 아니다
    assert _job_row(db, later)["status"] == "queued"


def test_two_workers_never_claim_the_same_job(db, db_env) -> None:
    queue = JobQueue(db, Clock())
    queue.enqueue(COLLECT, {"source_id": 1})
    with connect(*db_env) as other, other.transaction():
        # 다른 워커가 그 잡을 잠근 채로 있으면 건너뛰어야 한다(SKIP LOCKED)
        other.execute("select id from jobs where status = 'queued' for update")
        assert queue.claim_next() is None


def test_failed_job_is_retried_with_backoff_then_given_up(db) -> None:
    clock = Clock()
    queue = JobQueue(db, clock)
    job_id = queue.enqueue(COLLECT, {"source_id": 1})
    for attempt, wait_minutes in [(1, 2), (2, 4)]:
        job = queue.claim_next()
        assert job["attempts"] == attempt
        assert queue.finish_failed(job, "boom") == "queued"
        assert _job_row(db, job_id)["run_after"] == clock.now + timedelta(minutes=wait_minutes)
        assert queue.claim_next() is None
        clock.advance(minutes=wait_minutes)
    assert queue.finish_failed(queue.claim_next(), "boom again") == "failed"
    row = _job_row(db, job_id)
    assert row["status"] == "failed" and row["last_error"] == "boom again" and row["finished_at"] is not None


def test_non_retryable_failure_is_final_immediately(db) -> None:
    queue = JobQueue(db, Clock())
    job_id = queue.enqueue(COLLECT, {"source_id": 999})
    assert queue.finish_failed(queue.claim_next(), "no such source", retryable=False) == "failed"
    assert _job_row(db, job_id)["status"] == "failed"


def test_stale_running_jobs_return_to_the_queue_and_orphan_runs_are_closed(db) -> None:
    clock = Clock()
    queue = JobQueue(db, clock)
    source_id = _source(db)
    stale, fresh = queue.enqueue(COLLECT, {"source_id": 1}), queue.enqueue(COLLECT, {"source_id": 2})
    queue.claim_next()
    queue.claim_next()
    db.execute("insert into collection_runs (source_id, job_id, started_at) values (%s, %s, %s)", (source_id, stale, clock.now))
    db.execute("update jobs set locked_at = %s where id = %s", (clock.now - timedelta(minutes=20), stale))
    assert queue.recover_stale() == 1
    assert _job_row(db, stale)["status"] == "queued" and _job_row(db, fresh)["status"] == "running"
    run = db.execute("select status, requests_total, requests_failed, error from collection_runs").fetchone()
    assert (run["status"], run["requests_total"], run["requests_failed"]) == ("failed", 1, 1)
    assert run["error"].startswith("worker_died")


def test_stale_job_that_used_all_attempts_is_failed_not_requeued_forever(db) -> None:
    clock = Clock()
    queue = JobQueue(db, clock)
    job_id = queue.enqueue(COLLECT, {"source_id": 1})
    db.execute("update jobs set status = 'running', attempts = 3, locked_at = %s where id = %s",
               (clock.now - timedelta(minutes=20), job_id))
    assert queue.recover_stale() == 1
    row = _job_row(db, job_id)
    assert row["status"] == "failed" and row["finished_at"] is not None


def test_manual_retry_only_revives_failed_jobs(db) -> None:
    queue = JobQueue(db, Clock())
    job_id = queue.enqueue(COLLECT, {"source_id": 1})
    assert queue.retry_failed(job_id) is False
    db.execute("update jobs set status = 'failed', attempts = 3 where id = %s", (job_id,))
    assert queue.retry_failed(job_id) is True
    row = _job_row(db, job_id)
    assert (row["status"], row["attempts"]) == ("queued", 0)


def test_schedule_creates_one_job_per_due_active_source(db) -> None:
    queue = JobQueue(db, Clock())
    a, b = _source(db, "a"), _source(db, "b")
    _source(db, "off", active=False)
    assert len(queue.schedule_due()) == 2
    assert queue.schedule_due() == []  # 이미 대기 중인 잡이 있으면 또 만들지 않는다
    payloads = {r["payload"]["source_id"] for r in db.execute("select payload from jobs").fetchall()}
    assert payloads == {a, b}


def test_schedule_waits_for_the_collection_interval(db) -> None:
    clock = Clock()
    queue = JobQueue(db, clock)
    source_id = _source(db, interval="1 hour")
    job_id = queue.schedule_due()[0]
    queue.claim_next()
    db.execute("insert into collection_runs (source_id, job_id, started_at) values (%s, %s, %s)", (source_id, job_id, clock.now))
    queue.finish_ok(job_id)
    clock.advance(minutes=59)
    assert queue.schedule_due() == []
    clock.advance(minutes=1)
    assert len(queue.schedule_due()) == 1
