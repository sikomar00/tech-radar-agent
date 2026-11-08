"""`analysis_runs` 접근. AI 분석은 덮어쓰지 않고 쌓고, 현재 값만 is_current로 표시한다(S2-05)."""
from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

PROVIDER_FAILURES = ("timeout", "network", "rate_limited", "http_status")  # LlmError.kind 중 공급자 쪽 문제
PROVIDER_TZ = "America/Los_Angeles"  # 하루 호출 한도는 공급자 기준(태평양 시간 자정)으로 초기화된다
TECHNOLOGY_KINDS = ("profile", "retype")  # 수집 항목이 아니라 기술을 대상으로 하는 분석


def _target(kind: str) -> str:
    """분석 종류에 따라 대상 칸 이름. 고정된 두 값 중 하나라 SQL에 그대로 넣어도 안전하다."""
    return "technology_id" if kind in TECHNOLOGY_KINDS else "source_item_id"


class AnalysisStore:
    def __init__(self, conn: psycopg.Connection, clock: Callable[[], datetime]) -> None:
        self._conn = conn
        self._clock = clock

    def find_reusable(self, kind: str, item_id: int, input_hash: str) -> dict[str, Any] | None:
        """같은 입력 해시의 현재 성공 결과. 있으면 모델을 다시 부르지 않는다(무료 한도를 아끼려고). item_id는 종류에 따라 기술 id다."""
        row = self._conn.execute(
            f"select result from analysis_runs where kind = %s and {_target(kind)} = %s and input_hash = %s "
            "and is_current and status = 'succeeded'", (kind, item_id, input_hash),
        ).fetchone()
        return row["result"] if row else None

    def current_result(self, kind: str, item_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"select result from analysis_runs where kind = %s and {_target(kind)} = %s and is_current", (kind, item_id)
        ).fetchone()
        return row["result"] if row else None

    def record_success(self, kind: str, item_id: int, model: str, input_hash: str, result: dict[str, Any],
                       tokens_in: int | None, tokens_out: int | None) -> None:
        """이전 현재 행을 내리고 새 행을 올린다. 둘이 한 트랜잭션이라 현재 행이 0개나 2개인 순간이 없다."""
        with self._conn.transaction():
            self._conn.execute(
                f"update analysis_runs set is_current = false where kind = %s and {_target(kind)} = %s and is_current",
                (kind, item_id),
            )
            self._conn.execute(
                f"insert into analysis_runs (kind, {_target(kind)}, model, input_hash, status, result, tokens_in, tokens_out, "
                "is_current, created_at) values (%s, %s, %s, %s, 'succeeded', %s, %s, %s, true, %s)",
                (kind, item_id, model, input_hash, Jsonb(result), tokens_in, tokens_out, self._clock()),
            )

    def record_failure(self, kind: str, item_id: int, model: str, input_hash: str, error: str, retryable: bool) -> None:
        """실패도 쌓는다. 실패한 행은 현재 값이 되지 못하므로 이전 성공 결과는 그대로 남는다."""
        self._conn.execute(
            f"insert into analysis_runs (kind, {_target(kind)}, model, input_hash, status, error, retryable, created_at) "
            "values (%s, %s, %s, %s, 'failed', %s, %s, %s)",
            (kind, item_id, model, input_hash, error[:500], retryable, self._clock()),
        )

    def failing_streak(self, count: int, within: timedelta) -> bool:
        """가장 최근 호출 count개가 모두 공급자 쪽 실패이고 마지막 실패가 within 안이면 True. 공급자 장애의 신호다.

        스키마 위반·해석 실패는 항목 때문에 생길 수 있어(악의적인 입력이 일부러 만들 수도 있다) 세지 않고 연속을 끊는다.
        """
        rows = self._conn.execute(
            "select status, error, created_at from analysis_runs order by id desc limit %s", (count,)
        ).fetchall()
        provider_side = all(r["status"] == "failed" and (r["error"] or "").split(":")[0] in PROVIDER_FAILURES for r in rows)
        return len(rows) == count and provider_side and rows[0]["created_at"] > self._clock() - within

    def calls_today(self) -> int:
        """공급자 기준 오늘(태평양 시간 자정부터) 기록된 호출 수. 실패한 호출도 한도에 세어지므로 함께 센다."""
        row = self._conn.execute(
            "select count(*)::int as n from analysis_runs where created_at >= "
            "date_trunc('day', %(now)s at time zone %(tz)s) at time zone %(tz)s",
            {"now": self._clock(), "tz": PROVIDER_TZ},
        ).fetchone()
        return row["n"]
