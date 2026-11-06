"""골든셋 평가(S2-07). 임시 스키마에서 항목을 하나씩 실제 분석 흐름에 태우고 정답과 비교한다.

운영 DB는 읽기만 한다(실제 제목·발췌·메타데이터를 URL로 찾으려고). 기술은 임시 스키마에만 만들고 끝나면 지운다.
"""
import csv
import hashlib
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from radar.analyze import AnalyzeEnv, AnalyzeFailed, run_analyze_job
from radar.domain.analysis import parse_classification
from radar.domain.metrics import GoldRow, Metrics, Outcome, evaluate
from radar.domain.normalize import normalize_url
from radar.llm import LlmClient
from radar.llm.cache import FileCachedLlm
from radar.store.analysis import AnalysisStore
from radar.store.db import apply_migrations, connect
from radar.store.holds import held_items
from radar.worker import utc_now

GOLDEN_PATH = Path("docs/golden/golden-set-draft.csv")
CACHE_PATH = Path("logs/eval-cache.json")
EVAL_BUDGET = 450  # 콘솔에 표시된 하루 한도 500회의 90%. 평가 호출은 운영 호출 수에 잡히지 않아서 따로 막는다
ATTEMPTS, RETRY_WAIT = 3, 20.0


@dataclass(frozen=True)
class EvalReport:
    metrics: Metrics
    outcomes: list[Outcome]
    schema_violations: int
    holds_without_type: int
    holds_unknown: int
    missing_in_production: int

    @property
    def failures(self) -> int:
        return sum(1 for o in self.outcomes if o.label is None)


def load_golden(path: Path, split: str, reviewed_only: bool = True) -> list[GoldRow]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == split]
    keep = [r for r in rows if r["reviewed"].strip().upper() == "Y" or not reviewed_only]
    return [GoldRow(r["id"], r["split"], r["source"], r["title"], r["url"], r["excerpt"], r["class"],
                    r["canonical_name"].strip(), r["type"].strip() or None) for r in keep]


def _seed(conn: psycopg.Connection, prod: psycopg.Connection, rows: list[GoldRow]) -> tuple[dict[str, int], int]:
    """골든셋 항목을 임시 스키마의 수집 항목으로 넣는다. 운영 DB에 같은 URL이 있으면 그 제목·발췌·메타데이터를 쓴다."""
    ids, missing = {}, 0
    for row in rows:
        nurl = normalize_url(row.url)
        real = prod.execute("select title, excerpt, metadata from source_items where normalized_url = %s", (nurl,)).fetchone()
        missing += real is None
        title, excerpt, meta = (real["title"], real["excerpt"], real["metadata"]) if real else (row.title, row.excerpt, {})
        kind = "rss" if row.source.startswith("rss") else row.source
        source = conn.execute(
            "insert into sources (name, kind, is_active) values (%s, %s, false) on conflict (name) do update set name = excluded.name "
            "returning id", (f"eval-{kind}", kind),
        ).fetchone()["id"]
        ids[row.id] = conn.execute(
            "insert into source_items (source_id, url, normalized_url, title, excerpt, content_hash, metadata) "
            "values (%s, %s, %s, %s, %s, %s, %s) returning id",
            (source, row.url, nurl, title, excerpt, hashlib.sha256(nurl.encode()).hexdigest(), Jsonb(meta)),
        ).fetchone()["id"]
    return ids, missing


def _process(conn: psycopg.Connection, llm: LlmClient, item_id: int, sleep: Callable[[float], None]) -> None:
    """항목 하나를 분석한다. 재시도할 수 있는 실패는 몇 번 더 해 보고, 그래도 안 되면 실패로 둔다(지표에서 따로 센다)."""
    for attempt in range(ATTEMPTS):
        try:
            run_analyze_job(AnalyzeEnv(conn, llm, utc_now), {"id": 0, "payload": {"source_item_id": item_id}})
            return
        except AnalyzeFailed as err:
            if not err.retryable or attempt == ATTEMPTS - 1:
                return
            sleep(RETRY_WAIT)


def _outcome(conn: psycopg.Connection, row: GoldRow, item_id: int, known: set[int]) -> Outcome:
    raw = AnalysisStore(conn, utc_now).current_result("classify", item_id)
    if raw is None:
        return Outcome(row, None)
    found = parse_classification(raw)
    link = conn.execute("select technology_id from technology_sources where source_item_id = %s limit 1", (item_id,)).fetchone()
    tech = link["technology_id"] if link else None
    created = tech is not None and tech not in known
    if tech is not None:
        known.add(tech)
    return Outcome(row, found.label, found.name, found.type, tech, created)


def run_eval(url: str, prod: psycopg.Connection, llm: LlmClient, rows: list[GoldRow],
             sleep: Callable[[float], None] = time.sleep) -> EvalReport:
    schema = f"eval_{uuid.uuid4().hex[:10]}"
    with connect(url) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:  # 실패해도 임시 스키마는 지운다
            with connect(url, schema) as conn:
                apply_migrations(conn)
                ids, missing = _seed(conn, prod, rows)
                known: set[int] = set()
                outcomes = []
                for row in rows:
                    _process(conn, llm, ids[row.id], sleep)
                    outcomes.append(_outcome(conn, row, ids[row.id], known))
                violations = conn.execute("select count(*)::int as n from analysis_runs where error like 'schema:%'").fetchone()["n"]
                untyped = conn.execute("select count(*)::int as n from pending_classification").fetchone()["n"]
                return EvalReport(evaluate(outcomes), outcomes, violations, untyped, len(held_items(conn)), missing)
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def _mismatches(outcomes: list[Outcome]) -> list[str]:
    lines = []
    for o in outcomes:
        gold, got = o.gold, (o.label or "실패")
        if (gold.label in ("ENTITY", "UPDATE")) != (got in ("ENTITY", "UPDATE")):
            kind = "후보 판별"
        elif o.label and gold.label in ("ENTITY", "UPDATE") and o.type != gold.type:
            kind = "유형"
        elif o.technology_id and not o.created and gold.label in ("ENTITY", "UPDATE") and o.name.lower() != gold.name.lower():
            kind = "이름·매칭 확인"
        else:
            continue
        lines.append(f"[{kind}] #{gold.id} {gold.title[:45]} | 정답 {gold.label}/{gold.name}/{gold.type} → 예측 {got}/{o.name}/{o.type}")
    return lines


def format_report(report: EvalReport, split: str, new_calls: int, cached: int, production_today: int) -> list[str]:
    m = report.metrics
    lines = [
        f"== 골든셋 평가 (split={split}, {len(report.outcomes)}건) ==",
        f"후보 판별 Precision {m.candidate_precision} | Recall {m.candidate_recall}",
        f"유형 분류 정확도 {m.type_accuracy}  (후보로 맞춘 항목 중 유형이 정답과 같은 비율, 빈 유형은 보류가 정답)",
        f"기존 기술 매칭 Precision {m.match_precision}  (이미 만든 기술에 잇기로 한 항목 중 정답이 같은 기술인 비율)",
        f"중복 기술 생성률 {m.duplicate_rate}  (정답 이름이 같은데 따로 만든 기술 수 / 만든 기술 수)",
        f"분석 실패 {report.failures}건 | 스키마 위반 {report.schema_violations}건 | 보류: 유형 없음 {report.holds_without_type}, 매칭 모름 {report.holds_unknown}",
        f"모델 새 호출 {new_calls}회, 캐시 {cached}회 | 운영 오늘 호출 {production_today}회 | 운영 DB에 없어 CSV 값을 쓴 항목 {report.missing_in_production}건",
        "-- 틀린 항목 --",
    ]
    return lines + (_mismatches(report.outcomes) or ["(없음)"])


def eval_command(conn: psycopg.Connection, url: str, llm: LlmClient | None, holdout: bool, unreviewed: bool,
                 golden: Path = GOLDEN_PATH, cache: Path = CACHE_PATH) -> int:
    split = "holdout" if holdout else "tune"
    rows = load_golden(golden, split, reviewed_only=not unreviewed)
    if llm is None:
        print("GEMINI_API_KEY가 없어 평가할 수 없습니다")
        return 1
    if not rows:
        print(f"{split} 줄 중 검토 완료(reviewed=Y)한 것이 없습니다. 골든셋을 검토한 뒤 다시 하세요")
        return 1
    used, needed = AnalysisStore(conn, utc_now).calls_today(), math.ceil(len(rows) * 1.5)
    if needed > EVAL_BUDGET - used:
        print(f"운영 분석이 오늘 이미 {used}회 호출했고 평가에는 최대 {needed}회가 필요해 하루 한도를 넘길 수 있습니다. 워커를 멈추고 내일 다시 하세요")
        return 1
    print("평가하는 동안 운영 워커를 멈춰 두세요(같은 무료 한도를 씁니다).")
    if holdout:
        print("보류용(holdout) 결과는 게이트 판정에만 씁니다. 이 결과를 보고 프롬프트나 규칙을 고치지 마세요.")
    if unreviewed:
        print("검토 전 라벨입니다. 파이프라인 점검용이며 정확도 수치를 믿지 마세요.")
    cached = FileCachedLlm(llm, cache)
    report = run_eval(url, conn, cached, rows)
    print("\n".join(format_report(report, split, cached.calls, cached.hits, used)))
    return 0
