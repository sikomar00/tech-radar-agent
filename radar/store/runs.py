from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import psycopg

ERROR_TEXT_LIMIT = 4000


@dataclass(frozen=True)
class RunOutcome:
    status: str  # succeeded 또는 failed
    requests_total: int
    requests_failed: int
    error: str | None = None


def get_source(conn: psycopg.Connection, source_id: int) -> dict[str, Any]:
    row = conn.execute("select id, name, kind, config from sources where id = %s", (source_id,)).fetchone()
    if row is None:
        raise LookupError(f"source {source_id}가 없습니다")
    return row


def list_runs(conn: psycopg.Connection, limit: int) -> list[dict[str, Any]]:
    return conn.execute(
        "select r.id, s.name as source, r.status, r.started_at, r.requests_total, r.requests_failed, r.error "
        "from collection_runs r join sources s on s.id = r.source_id order by r.id desc limit %s",
        (limit,),
    ).fetchall()


class RunLog:
    """`collection_runs`에 잡 시도마다 한 줄을 남긴다."""

    def __init__(self, conn: psycopg.Connection, clock: Callable[[], datetime]) -> None:
        self._conn = conn
        self._clock = clock

    def start(self, source_id: int, job_id: int) -> int:
        row = self._conn.execute(
            "insert into collection_runs (source_id, job_id, started_at) values (%s, %s, %s) returning id",
            (source_id, job_id, self._clock()),
        ).fetchone()
        return row["id"]

    def finish(self, run_id: int, outcome: RunOutcome) -> None:
        self._conn.execute(
            "update collection_runs set finished_at = %s, status = %s, requests_total = %s, requests_failed = %s, "
            "error = %s where id = %s",
            (self._clock(), outcome.status, outcome.requests_total, outcome.requests_failed,
             outcome.error and outcome.error[:ERROR_TEXT_LIMIT], run_id),
        )
