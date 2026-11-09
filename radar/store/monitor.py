"""System Monitor(IA-4) 조회: 수집 상태, 오늘 모델 호출, 분석 실패와 원인, 잡, 분류 보류(3단계 게이트: 실패 원인 확인)."""
from datetime import datetime, timedelta
from typing import Any

import psycopg

from radar.store.holds import held_items, held_technologies

STALE_COLLECTION = timedelta(hours=3)  # Feed 위에 경고를 띄우는 기준(UJ-1). 가장 짧은 수집 주기(1시간)의 세 배


def collection(conn: psycopg.Connection, now: datetime) -> list[dict[str, Any]]:
    """소스별 최근 24시간 요청·실패 수와 마지막 실행."""
    return conn.execute(
        "select s.name, s.kind, coalesce(sum(r.requests_total), 0)::int as requests, "
        "coalesce(sum(r.requests_failed), 0)::int as failed, max(r.started_at) as last_run, "
        "(array_agg(r.error order by r.started_at desc) filter (where r.error is not null))[1] as last_error "
        "from sources s left join collection_runs r on r.source_id = s.id and r.started_at >= %s "
        "where s.is_active group by s.id, s.name, s.kind order by s.name", (now - timedelta(hours=24),),
    ).fetchall()


def last_collection(conn: psycopg.Connection) -> datetime | None:
    return conn.execute("select max(started_at) as at from collection_runs").fetchone()["at"]


def recent_failures(conn: psycopg.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """최근 분석 실패와 원인, 재시도 가능 여부."""
    return conn.execute(
        "select id, kind, source_item_id, technology_id, error, retryable, created_at from analysis_runs "
        "where status = 'failed' order by id desc limit %s", (limit,),
    ).fetchall()


def job_counts(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "select kind, status::text as status, count(*)::int as n from jobs group by kind, status order by kind, status"
    ).fetchall()


def failed_jobs(conn: psycopg.Connection, limit: int = 30) -> list[dict[str, Any]]:
    return conn.execute(
        "select id, kind, attempts, last_error, finished_at from jobs where status = 'failed' order by id desc limit %s",
        (limit,),
    ).fetchall()


def holds(conn: psycopg.Connection) -> dict[str, list[dict[str, Any]]]:
    return {"technologies": held_technologies(conn), "items": held_items(conn)}
