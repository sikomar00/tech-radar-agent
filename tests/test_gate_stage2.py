from radar.gate import stage2_report
from radar.store.analysis import AnalysisStore
from radar.store.technologies import TechnologyStore
from tests.conftest import Clock, add_item


def test_a_clean_database_passes_and_reports_zero_counts(db) -> None:
    lines, passed = stage2_report(db, Clock())
    text = "\n".join(lines)
    assert passed and "분석 실패 기록 0건" in text and "출처 없는 기술 0건" in text and "분석 잡: 없음" in text


def test_failures_are_counted_with_their_retryability_and_calls_are_counted(db) -> None:
    store, item = AnalysisStore(db, Clock()), add_item(db)
    store.record_failure("classify", item, "m", "h1", "http_status: HTTP 503", retryable=True)
    store.record_failure("classify", item, "m", "h2", "http_status: HTTP 403", retryable=False)
    lines, passed = stage2_report(db, Clock())
    text = "\n".join(lines)
    assert passed and "분석 실패 기록 2건(재시도 가능 1건, 불가 1건)" in text and "원인이 비어 있는 기록 0건" in text
    assert "모델 호출 2회" in text


def test_a_failure_without_a_cause_or_a_technology_without_a_source_fails_the_check(db) -> None:
    store, item = AnalysisStore(db, Clock()), add_item(db)
    store.record_failure("classify", item, "m", "h1", "", retryable=True)
    assert stage2_report(db, Clock())[1] is False
    db.execute("delete from analysis_runs")
    TechnologyStore(db).create("Orphan", "PRODUCT")
    lines, passed = stage2_report(db, Clock())
    assert passed is False and "출처 없는 기술 1건" in "\n".join(lines)
