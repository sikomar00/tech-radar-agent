"""analyze_item 잡 하나를 실행한다: 분류하고, 후보면 기존 기술과 맞춰 보고, 기술을 만들거나 잇는다(S2-02~S2-05)."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg

from radar.domain.cleaning import sanitize_json
from radar.domain.analysis import (
    Classification, MatchDecision, classify_payload, evidence_type, input_hash, match_payload, parse_classification,
    parse_match, passes_prefilter, render_data, repo_key,
)
from radar.domain.prompts import CLASSIFY_INSTRUCTION, CLASSIFY_SCHEMA, MATCH_INSTRUCTION, MATCH_SCHEMA
from radar.events import record_event
from radar.job_errors import JobFailed
from radar.llm import LlmClient, LlmError, complete_validated
from radar.store.analysis import AnalysisStore
from radar.store.items import get_item
from radar.store.jobs import ANALYZE, JobQueue
from radar.store.technologies import CANDIDATE_LIMIT, TechnologyStore

log = logging.getLogger(__name__)
DAILY_CAP = 375  # 콘솔에 표시된 하루 한도 500회의 75%(ADR-0004)
SCHEDULE_BATCH = 100
BREAKER_FAILURES, BREAKER_COOLDOWN = 5, timedelta(minutes=30)
CLASSIFY, MATCH = "classify", "match"


@dataclass(frozen=True)
class AnalyzeEnv:
    conn: psycopg.Connection
    llm: LlmClient
    clock: Callable[[], datetime]


class AnalyzeFailed(JobFailed):
    """분석 잡을 실패로 처리해야 할 때 올린다."""


def schedule_analysis(queue: JobQueue) -> list[int]:
    """사전 필터를 통과한 항목마다 analyze_item 잡을 만든다."""
    made = []
    # shortcut: 거른 항목은 기록하지 않아 매 주기 다시 검사하고 한 번에 100건만 본다. 규칙이 100건 넘게 거르기 전에 거른 항목을 기록하게 바꾼다
    for item in queue.items_without_analysis_job(SCHEDULE_BATCH):
        if passes_prefilter(classify_payload(item["source_kind"], item["title"], item["excerpt"], item["metadata"])):
            made.append(queue.enqueue(ANALYZE, {"source_item_id": item["id"]}))
    return made


def analysis_paused(conn: psycopg.Connection, clock: Callable[[], datetime]) -> bool:
    """모델 호출이 연속으로 실패하는 중이면 잠시 분석 잡을 집지 않는다. 공급자 장애에 백로그가 한꺼번에 failed가 되지 않게 한다.

    쉬는 시간이 지나면 잡 하나가 시험 삼아 호출한다. 성공하면 이어서 하고 또 실패하면 다시 쉰다.
    """
    return AnalysisStore(conn, clock).failing_streak(BREAKER_FAILURES, BREAKER_COOLDOWN)


def daily_cap_reached(conn: psycopg.Connection, clock: Callable[[], datetime]) -> bool:
    return AnalysisStore(conn, clock).calls_today() >= DAILY_CAP


def _ask(env: AnalyzeEnv, store: AnalysisStore, item_id: int, kind: str, instruction: str, schema: dict[str, Any],
         payload: dict[str, Any], parse: Callable[[dict[str, Any]], Any]) -> Any:
    """같은 입력 해시의 현재 성공 결과가 있으면 재사용하고, 없으면 모델을 불러 검증한 뒤 쌓는다. 실패도 쌓는다."""
    digest = input_hash(env.llm.model, instruction, schema, payload)
    cached = store.find_reusable(kind, item_id, digest)
    if cached is not None:
        try:
            return parse(cached)
        except ValueError:
            pass  # 해석 규칙이 바뀌어 저장된 결과를 못 읽는다. 다시 묻는다
    try:
        reply = complete_validated(env.llm, instruction, render_data(payload), schema)
        answer = sanitize_json(reply.data)  # jsonb는 NUL을 받지 못한다. 저장할 값과 해석할 값을 같게 만든다
        parsed = parse(answer)
    except (LlmError, ValueError) as err:
        retryable = err.retryable if isinstance(err, LlmError) else True  # 해석 실패는 모델이 흔들린 것이라 다시 해 볼 수 있다
        error = str(err) if isinstance(err, LlmError) else f"invalid: {err}"
        store.record_failure(kind, item_id, env.llm.model, digest, error, retryable)
        raise AnalyzeFailed(error, retryable) from err
    store.record_success(kind, item_id, env.llm.model, digest, answer, reply.tokens_in, reply.tokens_out)
    return parsed


def _decide(env: AnalyzeEnv, store: AnalysisStore, techs: TechnologyStore, item: dict[str, Any],
            payload: dict[str, Any], found: Classification) -> MatchDecision:
    """규칙 → 퍼지 → 모델 순으로 기존 기술과 같은지 정한다(S2-04). 모델은 후보가 있을 때만 부른다."""
    sure = techs.definite_match(found.name, repo_key(item["source_kind"], item["normalized_url"], item["metadata"]))
    if len(sure) == 1:
        return MatchDecision("SAME", sure[0])
    archived = None if sure else techs.archived_match(found.name)
    if archived is not None:  # 사용자가 보관한 이름이면 새로 만들지 않고 출처로만 붙인다(SPEC 3단계 보관)
        return MatchDecision("SAME", archived)
    keys = [found.name, *found.aliases_en, *found.aliases_ko]
    ids = list(dict.fromkeys([*sure, *techs.fuzzy_candidates(keys)]))[:CANDIDATE_LIMIT]
    if not ids:
        return MatchDecision("DIFFERENT")
    request = match_payload(payload, found, techs.describe(ids))
    return _ask(env, store, item["id"], MATCH, MATCH_INSTRUCTION, MATCH_SCHEMA, request, lambda raw: parse_match(raw, set(ids)))


def attach(techs: TechnologyStore, item: dict[str, Any], found: Classification, technology_id: int) -> None:
    """기술에 새 별칭을 더하고 항목을 근거로 잇는다. 정규명이 다르면 항목의 이름도 별칭이 된다."""
    techs.add_aliases(technology_id, [found.name, *found.aliases_en], found.aliases_ko)
    techs.link(technology_id, item["id"], evidence_type(item["source_kind"], item["metadata"], found.label))
    record_event(techs.conn, technology_id, item)


def run_analyze_job(env: AnalyzeEnv, job: dict[str, Any]) -> None:
    try:
        item = get_item(env.conn, int(job["payload"]["source_item_id"]))
    except (LookupError, TypeError, ValueError) as err:  # 항목이 없거나 페이로드가 잘못된 잡은 다시 해도 같다
        raise AnalyzeFailed(f"잘못된 잡: {err!r}", retryable=False) from err
    store, techs = AnalysisStore(env.conn, env.clock), TechnologyStore(env.conn)
    payload = classify_payload(item["source_kind"], item["title"], item["excerpt"], item["metadata"])
    found = _ask(env, store, item["id"], CLASSIFY, CLASSIFY_INSTRUCTION, CLASSIFY_SCHEMA, payload, parse_classification)
    if not found.is_candidate and item["source_kind"] == "manual":  # 후보가 아니면 이름을 받지 못해 기술을 만들 수 없다
        raise AnalyzeFailed(f"invalid: 수동 등록 항목인데 {found.label}로 분류됐다", retryable=False)
    if not found.is_candidate:
        log.info("항목 %d: %s", item["id"], found.label)
        return
    decision = _decide(env, store, techs, item, payload, found)
    if decision.decision == "UNKNOWN":
        log.info("항목 %d: %s %r 보류(기존 기술과 같은지 모름)", item["id"], found.label, found.name)
        return
    with env.conn.transaction():  # 기술 만들기·별칭·연결은 한 덩어리다
        if decision.decision == "SAME":
            technology_id = decision.technology_id
        else:
            technology_id, _ = techs.create(found.name, found.type)
        attach(techs, item, found, technology_id)
    log.info("항목 %d: %s %r → 기술 %d(%s)", item["id"], found.label, found.name, technology_id, decision.decision)
