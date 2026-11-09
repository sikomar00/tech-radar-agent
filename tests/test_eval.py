import csv
from pathlib import Path

import pytest

from radar import eval as radar_eval
from radar.domain.metrics import GoldRow, Outcome, evaluate
from radar.eval import eval_command, format_report, load_golden, run_eval
from radar.llm import LlmError
from radar.llm.cache import FileCachedLlm
from tests.conftest import FakeLlm


def gold(n: int, label: str, name: str = "", type_: str | None = None, split: str = "tune") -> GoldRow:
    return GoldRow(str(n), split, "rss-x", f"title {n}", f"https://e.example/{n}", "excerpt", label, name, type_)


def cls(label: str, name: str = "", type_: str = "NONE") -> dict:
    return {"class": label, "canonical_name": name, "type": type_, "aliases_en": [], "aliases_ko": [], "reason": "시험"}


def out(row: GoldRow, label: str | None, name: str = "", type_: str | None = None, tech: int | None = None, created: bool = False) -> Outcome:
    return Outcome(row, label, name, type_, tech, created)


def test_candidate_precision_recall_and_type_accuracy() -> None:
    outcomes = [
        out(gold(1, "ENTITY", "A", "PRODUCT"), "ENTITY", "A", "PRODUCT", 1, True),      # 맞음
        out(gold(2, "UPDATE", "B", None), "UPDATE", "B", "PRODUCT", 2, True),           # 후보는 맞고 유형은 보류여야 했다
        out(gold(3, "NOISE"), "ENTITY", "C", "PRODUCT", 3, True),                         # 거짓 양성
        out(gold(4, "ENTITY", "D", "PRODUCT"), "NOISE"),                                  # 놓침
        out(gold(5, "ENTITY", "E", "PRODUCT"), None),                                     # 분석 실패는 놓친 것으로 센다
    ]
    m = evaluate(outcomes)
    assert (m.candidate_precision.hit, m.candidate_precision.total) == (2, 3)
    assert (m.candidate_recall.hit, m.candidate_recall.total) == (2, 4)
    assert (m.type_accuracy.hit, m.type_accuracy.total) == (1, 2)


def test_match_precision_counts_only_candidates_joined_to_a_technology_of_the_same_gold_name() -> None:
    first = out(gold(1, "ENTITY", "LangChain", "FRAMEWORK_LIBRARY"), "ENTITY", "LangChain", "FRAMEWORK_LIBRARY", 1, True)
    right = out(gold(2, "UPDATE", "langchain", "FRAMEWORK_LIBRARY"), "UPDATE", "LangChain", "FRAMEWORK_LIBRARY", 1)
    wrong = out(gold(3, "UPDATE", "Ollama", "PRODUCT"), "UPDATE", "LangChain", "PRODUCT", 1)
    noise_first = out(gold(4, "NOISE"), "ENTITY", "X", "PRODUCT", 2, True)
    noise_join = out(gold(5, "NOISE"), "UPDATE", "X", "PRODUCT", 2)  # 빈 이름끼리 같다고 세면 안 된다
    m = evaluate([first, right, wrong, noise_first, noise_join])
    assert (m.match_precision.hit, m.match_precision.total) == (1, 3)


def test_duplicate_rate_counts_technologies_that_share_a_gold_name() -> None:
    a = out(gold(1, "ENTITY", "LangChain", "FRAMEWORK_LIBRARY"), "ENTITY", "LangChain", "FRAMEWORK_LIBRARY", 1, True)
    b = out(gold(2, "UPDATE", "LangChain", "FRAMEWORK_LIBRARY"), "UPDATE", "langchain-core", "FRAMEWORK_LIBRARY", 2, True)
    c = out(gold(3, "ENTITY", "Ollama", "PRODUCT"), "ENTITY", "Ollama", "PRODUCT", 3, True)
    fp = out(gold(4, "NOISE"), "ENTITY", "Foo", "PRODUCT", 4, True)  # 거짓 양성은 중복이 아니다
    m = evaluate([a, b, c, fp])
    assert (m.duplicate_rate.hit, m.duplicate_rate.total) == (1, 4)


def test_empty_samples_do_not_divide_by_zero() -> None:
    assert "표본 없음" in str(evaluate([]).candidate_precision)


def _write_csv(path: Path, rows: list[dict]) -> Path:
    cols = ["id", "split", "source", "title", "url", "excerpt", "class", "canonical_name", "type", "important_change", "confidence", "note", "reviewed"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in cols})
    return path


def test_load_golden_keeps_the_split_and_reviewed_rows_and_reads_blank_type_as_none(tmp_path) -> None:
    base = {"source": "rss-x", "title": "t", "url": "https://e.example/1", "excerpt": "e", "class": "ENTITY", "canonical_name": " Name "}
    path = _write_csv(tmp_path / "g.csv", [
        {**base, "id": "1", "split": "tune", "reviewed": "Y", "type": ""},
        {**base, "id": "2", "split": "tune", "reviewed": "", "type": "PRODUCT"},
        {**base, "id": "3", "split": "holdout", "reviewed": "y", "type": "PRODUCT"},
    ])
    only = load_golden(path, "tune")
    assert [(r.id, r.type, r.name) for r in only] == [("1", None, "Name")]
    assert [r.id for r in load_golden(path, "tune", reviewed_only=False)] == ["1", "2"]
    assert [r.id for r in load_golden(path, "holdout")] == ["3"]


def _rows() -> list[GoldRow]:
    return [gold(1, "NOISE"), gold(2, "ENTITY", "Ollama", "PRODUCT"), gold(3, "UPDATE", "Ollama", "PRODUCT"),
            gold(4, "NOISE"), gold(5, "ENTITY", "Bar", "FRAMEWORK_LIBRARY")]


def _schemas(db) -> int:
    return db.execute("select count(*)::int as n from pg_namespace where nspname like 'eval\\_%'").fetchone()["n"]


def test_run_eval_scores_the_pipeline_in_a_temporary_schema_and_leaves_production_untouched(db_env, db) -> None:
    llm = FakeLlm(cls("NOISE"), cls("ENTITY", "Ollama", "PRODUCT"), cls("UPDATE", "ollama", "PRODUCT"),
                  cls("ENTITY", "Foo", "PRODUCT"), cls("NOISE"))
    before = _schemas(db)
    report = run_eval(db_env[0], db, llm, _rows())
    m = report.metrics
    assert [(m.candidate_precision.hit, m.candidate_precision.total), (m.candidate_recall.hit, m.candidate_recall.total)] == [(2, 3), (2, 3)]
    assert (m.type_accuracy.hit, m.type_accuracy.total) == (2, 2)
    assert (m.match_precision.hit, m.match_precision.total) == (1, 1)
    assert (m.duplicate_rate.hit, m.duplicate_rate.total) == (0, 2)
    assert (report.failures, report.schema_violations, report.missing_in_production) == (0, 0, 5)
    assert _schemas(db) == before and db.execute("select count(*) as n from technologies").fetchone()["n"] == 0
    text = "\n".join(format_report(report, "tune", 5, 0, 0))
    assert "후보 판별 Precision 2/3" in text and "[후보 판별] #4" in text and "[후보 판별] #5" in text


def test_run_eval_uses_the_real_title_and_metadata_from_production_when_the_url_matches(db_env, db) -> None:
    from tests.conftest import add_item
    add_item(db, kind="github", title="REAL TITLE", url="https://github.com/o/n", metadata={"kind": "new_repo", "stars": 7})
    llm = FakeLlm(cls("NOISE"))
    report = run_eval(db_env[0], db, llm, [GoldRow("1", "tune", "github", "csv title", "https://github.com/o/n", "csv", "NOISE", "", None)])
    assert report.missing_in_production == 0
    assert "REAL TITLE" in llm.calls[0][1] and '"stars": 7' in llm.calls[0][1] and "csv title" not in llm.calls[0][1]


def test_a_retryable_failure_is_tried_again_and_a_permanent_one_is_counted(db_env, db) -> None:
    waits: list[float] = []
    llm = FakeLlm(LlmError("http_status", "HTTP 503", True), cls("NOISE"), LlmError("http_status", "HTTP 403", False))
    report = run_eval(db_env[0], db, llm, [gold(1, "NOISE"), gold(2, "NOISE")], sleep=waits.append)
    assert waits == [radar_eval.RETRY_WAIT] and report.failures == 1
    assert [o.label for o in report.outcomes] == ["NOISE", None]


def test_the_file_cache_reuses_only_successful_answers(tmp_path) -> None:
    inner = FakeLlm(cls("NOISE"), LlmError("timeout", "x", True), cls("USAGE"))
    cached = FileCachedLlm(inner, tmp_path / "c.json")
    first = cached.complete_json("i", "d", {"a": 1})
    assert cached.complete_json("i", "d", {"a": 1}).data == first.data and (cached.calls, cached.hits) == (1, 1)
    with pytest.raises(LlmError):
        cached.complete_json("i", "other", {"a": 1})
    assert cached.complete_json("i", "other", {"a": 1}).data["class"] == "USAGE"  # 실패는 저장되지 않아 다시 불렀다
    again = FileCachedLlm(FakeLlm(), tmp_path / "c.json")  # 새 객체도 파일에서 읽는다
    assert again.complete_json("i", "d", {"a": 1}).data == first.data and again.calls == 0


def test_eval_command_refuses_without_a_key_reviewed_rows_or_quota(db_env, db, tmp_path, monkeypatch, capsys) -> None:
    path = _write_csv(tmp_path / "g.csv", [{"id": "1", "split": "tune", "source": "rss-x", "title": "t", "url": "https://e.example/1",
                                            "excerpt": "e", "class": "NOISE", "reviewed": ""}])
    assert eval_command(db, db_env[0], None, False, True, path, tmp_path / "c.json") == 1
    assert eval_command(db, db_env[0], FakeLlm(), False, False, path, tmp_path / "c.json") == 1
    assert "검토 완료" in capsys.readouterr().out
    monkeypatch.setattr(radar_eval, "EVAL_BUDGET", 1)
    assert eval_command(db, db_env[0], FakeLlm(), False, True, path, tmp_path / "c.json") == 1
    assert "하루 한도" in capsys.readouterr().out
