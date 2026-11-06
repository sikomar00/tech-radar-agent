import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

import psycopg

from radar.adapters.http import UrllibClient
from radar import holds
from radar.config import database_url, gemini_api_key, load_env
from radar.eval import CACHE_PATH, GOLDEN_PATH, eval_command
from radar.gate import gate_report, stage2_report
from radar.llm import LlmClient
from radar.llm.gemini import GeminiClient
from radar.seed import seed_sources
from radar.store import runs
from radar.store.db import apply_migrations, connect
from radar.store.jobs import JobQueue
from radar.worker import run_forever, run_once, utc_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="아직 적용하지 않은 마이그레이션을 적용한다")
    sub.add_parser("seed", help="기본 소스를 넣는다")
    worker = sub.add_parser("worker", help="수집 워커를 실행한다")
    worker.add_argument("--once", action="store_true", help="지금 실행할 잡을 모두 처리하고 끝낸다")
    runs_cmd = sub.add_parser("runs", help="수집 실행 기록")
    runs_cmd.add_argument("action", choices=["list"])
    runs_cmd.add_argument("--limit", type=int, default=20)
    jobs_cmd = sub.add_parser("jobs", help="잡 관리")
    jobs_cmd.add_argument("action", choices=["retry"])
    jobs_cmd.add_argument("job_id", type=int, nargs="?", help="다시 실행할 failed 잡 하나")
    jobs_cmd.add_argument("--kind", help="이 종류의 failed 잡을 모두 다시 실행한다(예: analyze_item)")
    holds_cmd = sub.add_parser("holds", help="분류 보류 큐")
    holds_cmd.add_argument("action", choices=["list", "set", "link", "new"])
    holds_cmd.add_argument("ids", nargs="*", help="set: 기술 id와 유형(PRODUCT|FRAMEWORK_LIBRARY), link: 항목 id와 기술 id, new: 항목 id")
    eval_cmd = sub.add_parser("eval", help="골든셋으로 분석 품질을 잰다(운영 워커를 멈추고 실행)")
    eval_cmd.add_argument("--holdout", action="store_true", help="보류용 골든셋(게이트 판정 전용)")
    eval_cmd.add_argument("--unreviewed", action="store_true", help="검토 전 라벨로 점검만 한다(수치는 믿지 않는다)")
    eval_cmd.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    eval_cmd.add_argument("--cache", type=Path, default=CACHE_PATH)
    gate = sub.add_parser("gate-check", help="게이트를 숫자로 점검한다(2단계는 DB로 확인하는 항목만)")
    gate.add_argument("--stage", type=int, choices=[1, 2], default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    url = database_url()
    if args.command == "worker" and not args.once:
        run_forever(lambda: connect(url), UrllibClient(), _llm())
        return 0
    with connect(url) as conn:
        return _run(args, conn)


def _llm() -> LlmClient | None:
    """GEMINI_API_KEY가 있으면 분석도 한다. 없으면 수집만 한다."""
    key = gemini_api_key()
    logging.getLogger(__name__).info("분석 %s", "켜짐(Gemini)" if key else "꺼짐(GEMINI_API_KEY 없음)")
    return GeminiClient(key) if key else None


def _holds(args: argparse.Namespace, conn: psycopg.Connection) -> int:
    ids = args.ids
    if args.action == "list":
        print("\n".join(holds.list_holds(conn)))
        return 0
    wanted = {"set": 2, "link": 2, "new": 1}[args.action]
    if len(ids) != wanted or not ids[0].isdigit() or (args.action == "link" and not ids[1].isdigit()):
        print(f"holds {args.action}에는 인자가 {wanted}개 필요합니다. 도움말: python -m radar holds -h")
        return 2
    if args.action == "set":
        ok, message = holds.set_type(conn, int(ids[0]), ids[1])
    elif args.action == "link":
        ok, message = holds.link_item(conn, utc_now, int(ids[0]), int(ids[1]))
    else:
        ok, message = holds.new_from_item(conn, utc_now, int(ids[0]))
    print(message)
    return 0 if ok else 1


def _retry(args: argparse.Namespace, queue: JobQueue) -> int:
    if (args.job_id is None) == (args.kind is None):
        print("잡 id 하나나 --kind 중 하나만 정해 주세요")
        return 2
    if args.kind:
        count = queue.retry_failed_kind(args.kind)
        print(f"{args.kind} 잡 {count}개를 다시 대기열에 넣었습니다")
        return 0
    ok = queue.retry_failed(args.job_id)
    print("다시 대기열에 넣었습니다" if ok else "failed 상태의 잡이 아니어서 아무것도 하지 않았습니다")
    return 0 if ok else 1


def _run(args: argparse.Namespace, conn: psycopg.Connection) -> int:
    if args.command == "migrate":
        print("적용:", ", ".join(apply_migrations(conn)) or "새로 적용할 것이 없습니다")
    elif args.command == "seed":
        print(f"새 소스 {seed_sources(conn)}개를 넣었습니다")
    elif args.command == "worker":
        http, llm = UrllibClient(), _llm()
        while run_once(conn, http, llm=llm):
            pass
    elif args.command == "runs":
        for r in runs.list_runs(conn, args.limit):
            first_cause = (r["error"] or "").split("\n")[0]
            print(f"#{r['id']} {r['source']} {r['status']} {r['started_at']:%Y-%m-%d %H:%M} "
                  f"요청 {r['requests_total']}건 중 실패 {r['requests_failed']}건 {first_cause}")
    elif args.command == "jobs":
        return _retry(args, JobQueue(conn, utc_now))
    elif args.command == "holds":
        return _holds(args, conn)
    elif args.command == "eval":
        return eval_command(conn, database_url(), _llm(), args.holdout, args.unreviewed, args.golden, args.cache)
    elif args.command == "gate-check":
        lines, passed = gate_report(conn) if args.stage == 1 else stage2_report(conn, utc_now)
        print("\n".join(lines))
        print("결과:", "통과" if passed else "미달")
        return 0 if passed else 1
    return 0
