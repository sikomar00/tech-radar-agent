import logging
import time
from datetime import datetime, timezone
from typing import Callable

import psycopg

from radar.adapters.http import HttpClient
from radar.analyze import AnalyzeEnv, daily_cap_reached, run_analyze_job, schedule_analysis
from radar.collect import Env, run_collect_job
from radar.job_errors import JobFailed
from radar.llm import LlmClient
from radar.store.jobs import ANALYZE, COLLECT, JobQueue

log = logging.getLogger(__name__)
IDLE_SECONDS = 30
RECONNECT_SECONDS = 30


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _execute(conn: psycopg.Connection, http: HttpClient, llm: LlmClient | None, clock: Callable[[], datetime],
             job: dict) -> None:
    if job["kind"] == COLLECT:
        run_collect_job(Env(conn, http, clock), job)
    elif job["kind"] == ANALYZE and llm is not None:
        run_analyze_job(AnalyzeEnv(conn, llm, clock), job)
    else:
        raise JobFailed(f"처리할 수 없는 잡 종류 {job['kind']}", retryable=False)


def run_once(conn: psycopg.Connection, http: HttpClient, clock: Callable[[], datetime] = utc_now,
             llm: LlmClient | None = None) -> bool:
    """스케줄과 잠금 복구를 하고 잡 하나를 실행한다. 실행한 잡이 없으면 False.

    llm이 없거나 오늘 호출 한도에 닿았으면 분석 잡은 집지 않고 queued로 남겨 다음에 이어서 한다.
    """
    queue = JobQueue(conn, clock)
    recovered = queue.recover_stale()
    if recovered:
        log.warning("멈춘 잡 %d개를 정리했습니다", recovered)
    queue.schedule_due()
    if llm is not None:
        schedule_analysis(queue)
    blocked = llm is None or daily_cap_reached(conn, clock)
    job = queue.claim_next(skip_kinds=(ANALYZE,) if blocked else ())
    if job is None:
        return False
    try:
        _execute(conn, http, llm, clock, job)
        queue.finish_ok(job["id"])
    except JobFailed as err:
        outcome = queue.finish_failed(job, str(err), err.retryable)
        log.warning("잡 %d(%s) 실패(%s), 시도 %d/%d: %s", job["id"], job["kind"], outcome, job["attempts"], job["max_attempts"], err)
    return True


def _connect_with_retry(connect: Callable[[], psycopg.Connection]) -> psycopg.Connection:
    while True:
        try:
            return connect()
        except psycopg.OperationalError as err:
            log.error("DB에 연결하지 못했습니다. %d초 뒤 다시 시도합니다: %s", RECONNECT_SECONDS, err)
            time.sleep(RECONNECT_SECONDS)


def run_forever(connect: Callable[[], psycopg.Connection], http: HttpClient, llm: LlmClient | None = None) -> None:
    """오래 도는 워커. DB 연결이 끊기면 연결될 때까지 다시 시도하고, 예상하지 못한 오류는 기록하고 계속 돈다.

    24시간 이상 도는 게이트 측정에서 연결 하나가 영원히 살아 있다고 믿을 수 없고, 한 번의 오류로 측정이 끊기면 안 된다.
    끝나지 못한 잡은 recover_stale이 시도 횟수 한도 안에서 정리한다.
    """
    conn = _connect_with_retry(connect)
    while True:
        try:
            if not run_once(conn, http, llm=llm):
                time.sleep(IDLE_SECONDS)
        except psycopg.OperationalError as err:
            log.error("DB 연결 오류: %s", err)
            conn.close()
            conn = _connect_with_retry(connect)
        except Exception:
            log.exception("예상하지 못한 오류. %d초 쉬고 계속합니다", IDLE_SECONDS)
            time.sleep(IDLE_SECONDS)
