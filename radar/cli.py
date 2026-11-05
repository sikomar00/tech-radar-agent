import argparse
import logging
import sys
from typing import Sequence

import psycopg

from radar.adapters.http import UrllibClient
from radar.config import database_url, load_env
from radar.gate import gate_report
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
    jobs_cmd.add_argument("job_id", type=int)
    sub.add_parser("gate-check", help="1단계 게이트를 숫자로 점검한다")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    url = database_url()
    if args.command == "worker" and not args.once:
        run_forever(lambda: connect(url), UrllibClient())
        return 0
    with connect(url) as conn:
        return _run(args, conn)


def _run(args: argparse.Namespace, conn: psycopg.Connection) -> int:
    if args.command == "migrate":
        print("적용:", ", ".join(apply_migrations(conn)) or "새로 적용할 것이 없습니다")
    elif args.command == "seed":
        print(f"새 소스 {seed_sources(conn)}개를 넣었습니다")
    elif args.command == "worker":
        http = UrllibClient()
        while run_once(conn, http):
            pass
    elif args.command == "runs":
        for r in runs.list_runs(conn, args.limit):
            first_cause = (r["error"] or "").split("\n")[0]
            print(f"#{r['id']} {r['source']} {r['status']} {r['started_at']:%Y-%m-%d %H:%M} "
                  f"요청 {r['requests_total']}건 중 실패 {r['requests_failed']}건 {first_cause}")
    elif args.command == "jobs":
        ok = JobQueue(conn, utc_now).retry_failed(args.job_id)
        print("다시 대기열에 넣었습니다" if ok else "failed 상태의 잡이 아니어서 아무것도 하지 않았습니다")
        return 0 if ok else 1
    elif args.command == "gate-check":
        lines, passed = gate_report(conn)
        print("\n".join(lines))
        print("결과:", "통과" if passed else "미달")
        return 0 if passed else 1
    return 0
