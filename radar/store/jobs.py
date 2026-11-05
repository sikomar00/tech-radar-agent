from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

COLLECT = "collect_source"
STALE_AFTER = timedelta(minutes=15)


class JobQueue:
    """PostgreSQL `jobs` 테이블로 만든 잡 큐. 시간은 주입한 clock에서 읽는다."""

    def __init__(self, conn: psycopg.Connection, clock: Callable[[], datetime]) -> None:
        self._conn = conn
        self._clock = clock

    def enqueue(self, kind: str, payload: dict[str, Any], run_after: datetime | None = None) -> int:
        row = self._conn.execute(
            "insert into jobs (kind, payload, run_after) values (%s, %s, %s) returning id",
            (kind, Jsonb(payload), run_after or self._clock()),
        ).fetchone()
        return row["id"]

    def claim_next(self) -> dict[str, Any] | None:
        """실행할 잡 하나를 잠그고 running으로 바꿔 돌려준다. 한 문장이라 다른 워커와 겹치지 않는다."""
        return self._conn.execute(
            "update jobs set status = 'running', locked_at = %(now)s, attempts = attempts + 1 "
            "where id = (select id from jobs where status = 'queued' and run_after <= %(now)s "
            "            order by run_after, id limit 1 for update skip locked) "
            "returning id, kind, payload, attempts, max_attempts",
            {"now": self._clock()},
        ).fetchone()

    def finish_ok(self, job_id: int) -> None:
        self._conn.execute(
            "update jobs set status = 'succeeded', finished_at = %s, locked_at = null where id = %s",
            (self._clock(), job_id),
        )

    def finish_failed(self, job: dict[str, Any], error: str, retryable: bool = True) -> str:
        """재시도 횟수가 남았으면 2^attempts분 뒤로 미루고(queued), 다 썼거나 재시도해도 소용없으면 failed로 남긴다."""
        now = self._clock()
        if not retryable or job["attempts"] >= job["max_attempts"]:
            self._conn.execute(
                "update jobs set status = 'failed', finished_at = %s, locked_at = null, last_error = %s where id = %s",
                (now, error, job["id"]),
            )
            return "failed"
        self._conn.execute(
            "update jobs set status = 'queued', run_after = %s, locked_at = null, last_error = %s where id = %s",
            (now + timedelta(minutes=2 ** job["attempts"]), error, job["id"]),
        )
        return "queued"

    def recover_stale(self) -> int:
        """워커가 죽어서 locked_at이 오래된 running 잡을 정리한다.

        시도 횟수가 남았으면 queued로 되돌리고, 다 썼으면 failed로 남긴다. 끝나지 못한 실행 기록도 실패로 닫고
        요청 1건 실패로 센다(성공률을 부풀리지 않으려고).
        """
        now = self._clock()
        with self._conn.transaction():
            stale = self._conn.execute(
                "select id from jobs where status = 'running' and locked_at < %s for update", (now - STALE_AFTER,)
            ).fetchall()
            ids = [row["id"] for row in stale]
            if not ids:
                return 0
            self._conn.execute(
                "update collection_runs set status = 'failed', finished_at = %s, requests_total = 1, requests_failed = 1, "
                "error = 'worker_died 워커가 끝내지 못한 실행' where status = 'running' and job_id = any(%s)",
                (now, ids),
            )
            self._conn.execute(
                "update jobs set status = (case when attempts >= max_attempts then 'failed' else 'queued' end)::job_status, "
                "locked_at = null, last_error = 'worker_died', "
                "finished_at = case when attempts >= max_attempts then %s end where id = any(%s)",
                (now, ids),
            )
        return len(ids)

    def retry_failed(self, job_id: int) -> bool:
        """수동 재실행. failed 잡만 시도 횟수를 0으로 되돌려 다시 queued로 만든다."""
        cur = self._conn.execute(
            "update jobs set status = 'queued', attempts = 0, run_after = %s, finished_at = null "
            "where id = %s and status = 'failed'",
            (self._clock(), job_id),
        )
        return cur.rowcount == 1

    def schedule_due(self) -> list[int]:
        """수집 주기가 지났고 대기·실행 중인 잡이 없는 활성 소스마다 collect_source 잡을 만든다."""
        now = self._clock()
        due = self._conn.execute(
            "select s.id from sources s where s.is_active "
            "and not exists (select 1 from jobs j where j.kind = %(kind)s and j.status in ('queued', 'running') "
            "                and (j.payload ->> 'source_id')::bigint = s.id) "
            "and coalesce((select max(r.started_at) from collection_runs r where r.source_id = s.id), '-infinity') "
            "    + s.collect_interval <= %(now)s order by s.id",
            {"kind": COLLECT, "now": now},
        ).fetchall()
        return [self.enqueue(COLLECT, {"source_id": row["id"]}, now) for row in due]
