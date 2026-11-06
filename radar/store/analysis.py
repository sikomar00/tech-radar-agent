"""`analysis_runs` 접근. AI 분석은 덮어쓰지 않고 쌓고, 현재 값만 is_current로 표시한다(S2-05)."""
from datetime import datetime
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

PROVIDER_TZ = "America/Los_Angeles"  # 하루 호출 한도는 공급자 기준(태평양 시간 자정)으로 초기화된다


class AnalysisStore:
    def __init__(self, conn: psycopg.Connection, clock: Callable[[], datetime]) -> None:
        self._conn = conn
        self._clock = clock

    def find_reusable(self, kind: str, item_id: int, input_hash: str) -> dict[str, Any] | None:
        """같은 입력 해시의 현재 성공 결과. 있으면 모델을 다시 부르지 않는다(무료 한도를 아끼려고)."""
        row = self._conn.execute(
            "select result from analysis_runs where kind = %s and source_item_id = %s and input_hash = %s "
            "and is_current and status = 'succeeded'", (kind, item_id, input_hash),
        ).fetchone()
        return row["result"] if row else None

    def current_result(self, kind: str, item_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "select result from analysis_runs where kind = %s and source_item_id = %s and is_current", (kind, item_id)
        ).fetchone()
        return row["result"] if row else None

    def record_success(self, kind: str, item_id: int, model: str, input_hash: str, result: dict[str, Any],
                       tokens_in: int | None, tokens_out: int | None) -> None:
        """이전 현재 행을 내리고 새 행을 올린다. 둘이 한 트랜잭션이라 현재 행이 0개나 2개인 순간이 없다."""
        with self._conn.transaction():
            self._conn.execute(
                "update analysis_runs set is_current = false where kind = %s and source_item_id = %s and is_current",
                (kind, item_id),
            )
            self._conn.execute(
                "insert into analysis_runs (kind, source_item_id, model, input_hash, status, result, tokens_in, tokens_out, "
                "is_current, created_at) values (%s, %s, %s, %s, 'succeeded', %s, %s, %s, true, %s)",
                (kind, item_id, model, input_hash, Jsonb(result), tokens_in, tokens_out, self._clock()),
            )

    def record_failure(self, kind: str, item_id: int, model: str, input_hash: str, error: str, retryable: bool) -> None:
        """실패도 쌓는다. 실패한 행은 현재 값이 되지 못하므로 이전 성공 결과는 그대로 남는다."""
        self._conn.execute(
            "insert into analysis_runs (kind, source_item_id, model, input_hash, status, error, retryable, created_at) "
            "values (%s, %s, %s, %s, 'failed', %s, %s, %s)",
            (kind, item_id, model, input_hash, error[:500], retryable, self._clock()),
        )

    def calls_today(self) -> int:
        """공급자 기준 오늘(태평양 시간 자정부터) 기록된 호출 수. 실패한 호출도 한도에 세어지므로 함께 센다."""
        row = self._conn.execute(
            "select count(*)::int as n from analysis_runs where created_at >= "
            "date_trunc('day', %(now)s at time zone %(tz)s) at time zone %(tz)s",
            {"now": self._clock(), "tz": PROVIDER_TZ},
        ).fetchone()
        return row["n"]
