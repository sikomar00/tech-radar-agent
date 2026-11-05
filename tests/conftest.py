import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from psycopg import sql

from radar.adapters.http import HttpResponse
from radar.config import load_env
from radar.domain.models import FetchError
from radar.store.db import apply_migrations, connect

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


def ok(body: bytes | str | Any, status: int = 200, headers: dict[str, str] | None = None) -> HttpResponse:
    if not isinstance(body, bytes):
        body = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    return HttpResponse(status, body, headers or {})


class FakeHttp:
    """URL별로 미리 정한 응답이나 FetchError를 돌려주는 가짜 클라이언트. 등록되지 않은 URL은 테스트 실패다."""

    def __init__(self, routes: dict[str, HttpResponse | FetchError]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"등록되지 않은 URL 호출: {url}")
        route = self.routes[url]
        if isinstance(route, FetchError):
            raise route
        return route


class Clock:
    """시간을 직접 넘길 수 있는 가짜 시계."""

    def __init__(self, start: datetime = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def db_env() -> Iterator[tuple[str, str]]:
    """테스트마다 임시 스키마를 만들고 마이그레이션을 적용한 뒤, 끝나면 지운다. (연결 문자열, 스키마 이름)."""
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL이 없어 DB 테스트를 건너뜁니다")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with connect(url) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:  # 마이그레이션이 실패해도 임시 스키마를 지워야 공유 DB에 남지 않는다
            with connect(url, schema) as conn:
                apply_migrations(conn)
            yield url, schema
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


@pytest.fixture
def db(db_env: tuple[str, str]) -> Iterator[Any]:
    with connect(*db_env) as conn:
        yield conn
