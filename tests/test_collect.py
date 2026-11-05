import pytest

from radar import collect
from radar.adapters.http import HttpResponse
from radar.domain.models import FetchError
from radar.gate import gate_report
from radar.store import runs
from radar.store.jobs import COLLECT, JobQueue
from radar.worker import run_once
from tests.conftest import Clock, FakeHttp, fixture_json, ok

BASE = "https://hacker-news.firebaseio.com/v0"
IDS = fixture_json("hn_topstories.json")
HN_CONFIG = '{"lists": ["topstories"], "limit": 3}'


def _hn_routes() -> dict:
    routes = {f"{BASE}/topstories.json": ok(IDS)}
    routes.update({f"{BASE}/item/{i}.json": ok(fixture_json(f"hn_item_{i}.json")) for i in IDS})
    return routes


def _hn_source(db) -> int:
    return db.execute(
        "insert into sources (name, kind, config) values ('hackernews', 'hackernews', %s::jsonb) returning id", (HN_CONFIG,)
    ).fetchone()["id"]


def _items(db) -> int:
    return db.execute("select count(*) as n from source_items").fetchone()["n"]


def _last_run(db) -> dict:
    return db.execute("select * from collection_runs order by id desc").fetchone()


def _job(db) -> dict:
    return db.execute("select status, attempts, last_error from jobs order by id desc").fetchone()


def test_a_due_source_is_collected_and_recorded(db) -> None:
    _hn_source(db)
    clock = Clock()
    assert run_once(db, FakeHttp(_hn_routes()), clock) is True
    run = _last_run(db)
    assert (run["status"], run["requests_total"], run["requests_failed"], run["error"]) == ("succeeded", 4, 0, None)
    assert _items(db) == 3 and _job(db)["status"] == "succeeded"
    assert run_once(db, FakeHttp({}), clock) is False  # 주기가 지나기 전에는 할 일이 없다


def test_recollecting_the_same_input_creates_no_new_rows(db) -> None:
    _hn_source(db)
    clock = Clock()
    run_once(db, FakeHttp(_hn_routes()), clock)
    clock.advance(hours=2)
    assert run_once(db, FakeHttp(_hn_routes()), clock) is True
    assert _items(db) == 3
    assert db.execute("select count(*) as n from collection_runs").fetchone()["n"] == 2


def test_partial_failure_keeps_saved_items_and_records_the_cause(db) -> None:
    _hn_source(db)
    routes = _hn_routes()
    routes[f"{BASE}/item/{IDS[1]}.json"] = HttpResponse(500, b"", {})
    run_once(db, FakeHttp(routes), Clock())
    run = _last_run(db)
    assert (run["status"], run["requests_failed"]) == ("succeeded", 1)
    assert run["error"].startswith("http_status ") and f"item/{IDS[1]}" in run["error"]
    assert _items(db) == 2 and _job(db)["status"] == "succeeded"


def test_total_failure_fails_the_run_and_retries_the_job_until_it_gives_up(db) -> None:
    _hn_source(db)
    clock = Clock()
    down = FakeHttp({f"{BASE}/topstories.json": FetchError("network", "unreachable")})
    for wait_minutes in (2, 4, 0):
        assert run_once(db, down, clock) is True
        assert _last_run(db)["status"] == "failed"
        clock.advance(minutes=wait_minutes)
    job = _job(db)
    assert (job["status"], job["attempts"]) == ("failed", 3) and "network" in job["last_error"]
    assert db.execute("select count(*) as n from collection_runs where status = 'failed'").fetchone()["n"] == 3


def test_adapter_exception_counts_as_a_failed_request_so_the_success_rate_is_not_inflated(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _hn_source(db)

    def explode(config, http, now):
        raise RuntimeError("adapter bug")

    monkeypatch.setitem(collect.ADAPTERS, "hackernews", explode)
    run_once(db, FakeHttp({}), Clock())
    run = _last_run(db)
    assert (run["status"], run["requests_total"], run["requests_failed"]) == ("failed", 1, 1)
    assert run["error"].startswith("exception ") and "adapter bug" in run["error"]


def test_storage_exception_keeps_the_requests_already_made_and_adds_one_failure(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _hn_source(db)

    def explode(self, source_id, items):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(collect.ItemStore, "save", explode)
    run_once(db, FakeHttp(_hn_routes()), Clock())
    run = _last_run(db)
    assert (run["status"], run["requests_total"], run["requests_failed"]) == ("failed", 5, 1)


def test_job_for_a_missing_source_fails_at_once_without_retrying(db) -> None:
    clock = Clock()
    JobQueue(db, clock).enqueue(COLLECT, {"source_id": 999})
    assert run_once(db, FakeHttp({}), clock) is True
    job = _job(db)
    assert (job["status"], job["attempts"]) == ("failed", 1) and "999" in job["last_error"]


def test_job_with_a_malformed_payload_fails_at_once(db) -> None:
    clock = Clock()
    JobQueue(db, clock).enqueue(COLLECT, {"source_id": "abc"})
    assert run_once(db, FakeHttp({}), clock) is True
    assert _job(db)["status"] == "failed"


def test_gate_report_counts_requests_failures_and_causes(db) -> None:
    _hn_source(db)
    routes = _hn_routes()
    routes[f"{BASE}/item/{IDS[1]}.json"] = HttpResponse(500, b"", {})
    run_once(db, FakeHttp(routes), Clock())
    lines, passed = gate_report(db)
    text = "\n".join(lines)
    assert passed is False  # 소스 종류가 1개뿐이고 성공률이 75%다
    assert "hackernews: 활성 소스 1개, 요청 4건 중 실패 1건, 성공률 75.0%" in text
    assert "http_status 1건" in text


def test_list_runs_returns_latest_first(db) -> None:
    _hn_source(db)
    clock = Clock()
    run_once(db, FakeHttp(_hn_routes()), clock)
    clock.advance(hours=2)
    run_once(db, FakeHttp(_hn_routes()), clock)
    listed = runs.list_runs(db, 10)
    assert [r["id"] for r in listed] == sorted((r["id"] for r in listed), reverse=True) and len(listed) == 2
