"""profile_technology 잡: 공식 페이지 한 장을 읽어 프로필 주장을 만든다(S3-01, S3-03, ADR-0007).

읽은 본문은 모델 입력으로만 쓰고 저장하지 않는다. 주장은 모두 Unverified이고, 읽은 페이지에 대응하는 수집 항목을 근거로 건다.
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg

from radar.adapters.page import Page, PageError, read_page, read_readme
from radar.analyze import AnalyzeEnv, AnalyzeFailed, _ask
from radar.domain.profile import OFFICIAL_SOURCE, parse_profile
from radar.job_errors import JobDeferred
from radar.domain.prompts import PROFILE_INSTRUCTION, PROFILE_SCHEMA
from radar.llm import LlmClient
from radar.store import knowledge
from radar.store.analysis import AnalysisStore
from radar.store.jobs import PROFILE, JobQueue

log = logging.getLogger(__name__)
README_PER_HOUR = 30  # GitHub 비인증 한도(시간당 60회)를 수집과 나눠 쓴다(ADR-0007)
BACKFILL_SPACING_MINUTES = 2  # 쌓인 기술을 한꺼번에 넣으면 README 한도를 넘으므로 시간당 30개 간격으로 넣는다
MAX_TITLES = 5


@dataclass
class ProfileEnv:
    conn: psycopg.Connection
    llm: LlmClient
    clock: Callable[[], datetime]
    read: Callable[[str], Page] = read_page
    readme: Callable[[str], Page] = read_readme
    readme_calls: deque = field(default_factory=deque)  # 최근 한 시간의 README 호출 시각(워커는 하나라 프로세스 안에서 센다)


def _take_readme_slot(env: ProfileEnv) -> None:
    """README 한 번 읽을 자리를 잡는다. 한 시간 창이 차 있으면 가장 오래된 호출이 창을 벗어날 때까지 잡을 미룬다."""
    now = time.monotonic()
    while env.readme_calls and env.readme_calls[0] < now - 3600:
        env.readme_calls.popleft()
    if len(env.readme_calls) >= README_PER_HOUR:
        wait = env.readme_calls[0] + 3600 - now + 1
        raise JobDeferred("README 읽기가 시간당 한도에 닿았다", env.clock() + timedelta(seconds=wait))
    env.readme_calls.append(now)


def _source_page(env: ProfileEnv, rows: list[dict[str, Any]]) -> tuple[Page | None, int]:
    """(읽은 페이지 또는 None, 근거로 걸 항목 id). GitHub 저장소가 있으면 README, 없으면 대표 출처 한 장."""
    github, chosen = knowledge.github_repo(rows), knowledge.representative(rows)
    if github:
        _take_readme_slot(env)
    try:
        if github:
            return env.readme(github[0]), github[1]
        return env.read(chosen["url"]), chosen["id"]
    except PageError as err:
        log.info("프로필 페이지를 읽지 못해 한 줄 설명만 만듭니다(%s)", err.kind)
        return None, (github[1] if github else chosen["id"])


def run_profile_job(env: ProfileEnv, job: dict[str, Any]) -> None:
    try:
        technology_id = int(job["payload"]["technology_id"])
    except (KeyError, TypeError, ValueError) as err:
        raise AnalyzeFailed(f"잘못된 잡: {err!r}", retryable=False) from err
    tech, rows = knowledge.technology(env.conn, technology_id), knowledge.sources(env.conn, technology_id)
    if tech is None or tech["archived_at"] is not None or not rows:
        log.info("기술 %d: 없거나 보관됐거나 출처가 없어 프로필을 만들지 않습니다", technology_id)
        return
    page, evidence = _source_page(env, rows)
    payload = {"canonical_name": tech["canonical_name"], "titles": [r["title"] for r in rows[:MAX_TITLES]],
               "page_text": page.text if page else ""}
    analyze_env = AnalyzeEnv(env.conn, env.llm, env.clock)
    fields = _ask(analyze_env, AnalysisStore(env.conn, env.clock), technology_id, "profile", PROFILE_INSTRUCTION,
                  PROFILE_SCHEMA, payload, lambda raw: parse_profile(raw, page is not None))
    if page is not None:
        fields[OFFICIAL_SOURCE] = page.url
    knowledge.save_profile(env.conn, technology_id, fields, evidence)
    log.info("기술 %d %r: 프로필 %d칸(페이지 %s)", technology_id, tech["canonical_name"], len(fields), "읽음" if page else "못 읽음")


def backfill(conn: psycopg.Connection, clock: Callable[[], datetime]) -> int:
    """현재 프로필이 없고 프로필 잡도 없었던 기술마다 잡을 넣는다. README 한도 때문에 2분 간격으로 띄운다."""
    rows = conn.execute(
        "select t.id from technologies t where t.archived_at is null "
        "and not exists (select 1 from technology_claims c where c.technology_id = t.id and c.is_current) "
        "and not exists (select 1 from jobs j where j.kind = %s and (j.payload ->> 'technology_id')::bigint = t.id) "
        "order by t.id", (PROFILE,),
    ).fetchall()
    queue, now = JobQueue(conn, clock), clock()
    for n, row in enumerate(rows):
        queue.enqueue(PROFILE, {"technology_id": row["id"]}, now + timedelta(minutes=BACKFILL_SPACING_MINUTES * n))
    return len(rows)
