from radar.store.analysis import AnalysisStore
from radar.store.items import get_item
from tests.conftest import Clock, add_item

import pytest


def _store(db) -> tuple[AnalysisStore, Clock]:
    clock = Clock()
    return AnalysisStore(db, clock), clock


def test_success_replaces_the_current_row_and_keeps_the_history(db) -> None:
    store, clock = _store(db)
    item = add_item(db)
    store.record_success("classify", item, "m", "h1", {"class": "NOISE"}, 1, 1)
    clock.advance(minutes=1)
    store.record_success("classify", item, "m", "h2", {"class": "ENTITY"}, 1, 1)
    rows = db.execute("select input_hash, is_current from analysis_runs order by id").fetchall()
    assert [(r["input_hash"], r["is_current"]) for r in rows] == [("h1", False), ("h2", True)]


def test_current_rows_are_kept_per_item_and_kind(db) -> None:
    store, _ = _store(db)
    a, b = add_item(db), add_item(db)
    for item in (a, b):
        store.record_success("classify", item, "m", "h", {"x": 1}, None, None)
    store.record_success("match", a, "m", "h", {"x": 1}, None, None)
    assert db.execute("select count(*) as n from analysis_runs where is_current").fetchone()["n"] == 3


def test_failure_is_recorded_as_retryable_and_leaves_the_current_result(db) -> None:
    store, _ = _store(db)
    item = add_item(db)
    store.record_success("classify", item, "m", "h1", {"class": "NOISE"}, 1, 1)
    store.record_failure("classify", item, "m", "h2", "rate_limited: HTTP 429", retryable=True)
    failed = db.execute("select status, retryable, error, is_current from analysis_runs where status = 'failed'").fetchone()
    assert (failed["status"], failed["retryable"], failed["is_current"]) == ("failed", True, False)
    assert failed["error"] == "rate_limited: HTTP 429"
    assert store.find_reusable("classify", item, "h1") == {"class": "NOISE"}


def test_a_result_is_reused_only_for_the_same_hash_and_while_it_is_current(db) -> None:
    store, _ = _store(db)
    item = add_item(db)
    store.record_success("classify", item, "m", "h1", {"v": 1}, None, None)
    assert store.find_reusable("classify", item, "h1") == {"v": 1}
    assert store.find_reusable("classify", item, "other") is None
    assert store.find_reusable("match", item, "h1") is None
    store.record_success("classify", item, "m", "h2", {"v": 2}, None, None)
    assert store.find_reusable("classify", item, "h1") is None


def test_calls_today_counts_failures_and_resets_at_pacific_midnight(db) -> None:
    store, clock = _store(db)
    item = add_item(db)
    clock.now = clock.now.replace(day=6, hour=7, minute=30)  # 태평양 표준시 11-05 23:30
    store.record_success("classify", item, "m", "h", {"v": 1}, None, None)
    store.record_failure("match", item, "m", "h", "timeout", retryable=True)
    assert store.calls_today() == 2
    clock.advance(hours=1)  # 11-06 00:30 PST, 새 하루
    assert store.calls_today() == 0
    store.record_failure("classify", item, "m", "h9", "http_status: HTTP 503", retryable=True)
    assert store.calls_today() == 1


def test_get_item_returns_the_source_kind_and_fails_for_a_missing_item(db) -> None:
    item = add_item(db, kind="github", title="owner/tool")
    row = get_item(db, item)
    assert (row["source_kind"], row["title"]) == ("github", "owner/tool")
    with pytest.raises(LookupError):
        get_item(db, item + 999)
