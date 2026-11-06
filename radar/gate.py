"""게이트 점검. docs/PRD.md의 완료 기준 중 DB로 확인할 수 있는 것을 숫자로 출력한다(1단계 전부, 2단계는 DB 항목만)."""
from datetime import datetime
from typing import Callable

import psycopg

from radar.analyze import DAILY_CAP
from radar.store.analysis import AnalysisStore
from radar.store.holds import held_items

MIN_SUCCESS_RATE = 0.95
MIN_REQUESTS_FOR_CONFIDENCE = 20

_PER_KIND = (
    "select s.kind, count(distinct s.id) as sources, coalesce(sum(r.requests_total), 0)::int as total, "
    "       coalesce(sum(r.requests_failed), 0)::int as failed, "
    "       (select count(*) from source_items i join sources s2 on s2.id = i.source_id where s2.kind = s.kind)::int as items "
    "from sources s left join collection_runs r on r.source_id = s.id where s.is_active group by s.kind order by s.kind"
)
_CAUSES = (
    "select split_part(line, ' ', 1) as cause, count(*)::int as n "
    "from collection_runs r, unnest(string_to_array(r.error, E'\\n')) as line "
    "where r.error is not null group by 1 order by 2 desc"
)
_KEY_DUPLICATES = (
    "select (count(*) - count(distinct normalized_url))::int as dup_url, (count(*) - count(distinct content_hash))::int as dup_hash "
    "from source_items"
)


def gate_report(conn: psycopg.Connection) -> tuple[list[str], bool]:
    """(출력할 줄들, 게이트 통과 여부). 같은 URL 재수집 시 새 행 0은 테스트로 확인하고, 여기서는 키 중복 행 수만 본다."""
    lines, passed = ["== 1단계 게이트 =="], True
    kinds = conn.execute(_PER_KIND).fetchall()
    with_data = [k for k in kinds if k["items"] > 0]
    lines.append(f"수집 데이터가 있는 소스 종류: {len(with_data)}개 (목표 3개 이상)")
    passed &= len(with_data) >= 3
    for k in kinds:
        rate = 1 - k["failed"] / k["total"] if k["total"] else 0.0
        ok = k["total"] > 0 and rate >= MIN_SUCCESS_RATE
        passed &= ok
        small = " [표본이 작음: 더 오래 측정 필요]" if k["total"] < MIN_REQUESTS_FOR_CONFIDENCE else ""
        lines.append(f"{k['kind']}: 활성 소스 {k['sources']}개, 요청 {k['total']}건 중 실패 {k['failed']}건, "
                     f"성공률 {rate:.1%} (목표 95% 이상) {'통과' if ok else '미달'}, 저장 항목 {k['items']}건{small}")
    dup = conn.execute(_KEY_DUPLICATES).fetchone()
    lines.append(f"중복 방지 키가 겹치는 행: normalized_url {dup['dup_url']}건, content_hash {dup['dup_hash']}건 (목표 0)")
    passed &= dup["dup_url"] == 0 and dup["dup_hash"] == 0
    causes = conn.execute(_CAUSES).fetchall()
    lines.append("실패 원인: " + (", ".join(f"{c['cause']} {c['n']}건" for c in causes) if causes else "기록된 실패 없음"))
    return lines, bool(passed)


_FAILED_ANALYSES = (
    "select count(*)::int as n, count(*) filter (where retryable)::int as retryable, "
    "count(*) filter (where coalesce(error, '') = '')::int as no_cause from analysis_runs where status = 'failed'"
)


def stage2_report(conn: psycopg.Connection, clock: Callable[[], datetime]) -> tuple[list[str], bool]:
    """2단계 게이트 중 DB로 확인하는 항목. 후보 판별·유형·매칭·중복률은 `radar eval --holdout`으로 따로 잰다."""
    lines = ["== 2단계 게이트(DB로 확인하는 항목) =="]
    failed = conn.execute(_FAILED_ANALYSES).fetchone()
    lines.append(f"분석 실패 기록 {failed['n']}건(재시도 가능 {failed['retryable']}건, 불가 {failed['n'] - failed['retryable']}건), "
                 f"원인이 비어 있는 기록 {failed['no_cause']}건 (목표 0)")
    sourceless = conn.execute("select count(*)::int as n from technologies_without_source").fetchone()["n"]
    lines.append(f"출처 없는 기술 {sourceless}건 (목표 0)")
    untyped = conn.execute("select count(*)::int as n from pending_classification").fetchone()["n"]
    lines.append(f"분류 보류: 유형 없는 기술 {untyped}건, 기존 기술과 같은지 모르는 항목 {len(held_items(conn))}건")
    jobs = conn.execute("select status, count(*)::int as n from jobs where kind = 'analyze_item' group by status order by status").fetchall()
    lines.append("분석 잡: " + (", ".join(f"{j['status']} {j['n']}건" for j in jobs) or "없음"))
    lines.append(f"오늘(태평양 시간) 모델 호출 {AnalysisStore(conn, clock).calls_today()}회 (상한 {DAILY_CAP})")
    lines.append("정확도 지표(후보 P·R, 유형, 매칭, 중복률)와 지시문 시험은 `radar eval --holdout`으로 따로 잰다")
    return lines, failed["no_cause"] == 0 and sourceless == 0
