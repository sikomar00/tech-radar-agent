"""collect_source 잡 하나를 실행한다: 어댑터로 가져오고, 저장하고, 실행 기록을 남긴다."""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import psycopg

from radar.adapters import ADAPTERS
from radar.adapters.http import HttpClient
from radar.domain.models import FetchResult
from radar.job_errors import JobFailed
from radar.store.items import ItemStore
from radar.store.runs import RunLog, RunOutcome, get_source

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Env:
    conn: psycopg.Connection
    http: HttpClient
    clock: Callable[[], datetime]


class CollectFailed(JobFailed):
    """수집 잡을 실패로 처리해야 할 때 올린다."""


def format_failures(result: FetchResult) -> str | None:
    """실패 원인을 `kind url: message` 줄로 적는다. gate-check가 첫 단어로 원인을 센다."""
    lines = [f"{r.error_kind} {r.url}: {r.message}" for r in result.failed_requests]
    return "\n".join(lines) or None


def _load_source(env: Env, job: dict[str, Any]) -> dict[str, Any]:
    try:
        return get_source(env.conn, int(job["payload"]["source_id"]))
    except (LookupError, TypeError, ValueError) as err:  # KeyError는 LookupError다. 소스가 없거나 페이로드가 잘못된 잡은 재시도해도 같다
        raise CollectFailed(f"잘못된 잡: {err!r}", retryable=False) from err


def run_collect_job(env: Env, job: dict[str, Any]) -> None:
    source = _load_source(env, job)
    run_log = RunLog(env.conn, env.clock)
    run_id = run_log.start(source["id"], job["id"])
    result: FetchResult | None = None
    try:
        result = ADAPTERS[source["kind"]](source["config"], env.http, env.clock())
        stats = ItemStore(env.conn, env.clock).save(source["id"], result.items)
    except Exception as err:
        # 예외로 끝난 실행은 요청 1건 실패로 더 센다. 0건으로 기록하면 성공률 분모에서 빠져 지표가 부풀려진다
        made = result.requests if result else []
        failed = len(result.failed_requests) if result else 0
        run_log.finish(run_id, RunOutcome("failed", len(made) + 1, failed + 1, f"exception {source['name']}: {err!r}"))
        raise CollectFailed(repr(err)) from err
    total, failed = len(result.requests), len(result.failed_requests)
    # 모든 요청이 실패했을 때만 실행을 실패로 본다. 일부 실패는 성공한 항목을 저장했으므로 기록만 남긴다
    everything_failed = total > 0 and failed == total
    run_log.finish(run_id, RunOutcome("failed" if everything_failed else "succeeded", total, failed, format_failures(result)))
    log.info("%s: 요청 %d건(실패 %d), 새 항목 %d, 갱신 %d, 중복 %d, 충돌 %d, 저장 불가 %d",
             source["name"], total, failed, stats.inserted, stats.updated, stats.duplicates, stats.conflicts, stats.invalid)
    if everything_failed:
        raise CollectFailed(format_failures(result) or "모든 요청 실패")
