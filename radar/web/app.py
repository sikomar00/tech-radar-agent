"""웹 화면(ADR-0006, IA v1.0). 이 PC(127.0.0.1)에서만 열고, 다른 웹사이트가 브라우저를 통해 보내는 요청을 막는다.

- Host가 허용 목록이 아니면 거절한다(DNS 리바인딩: 남의 도메인이 127.0.0.1을 가리키게 해서 이 서버를 읽는 공격).
- 바꾸는 요청(POST)은 Origin이 이 서버일 때만 받는다(CSRF: 다른 사이트의 폼이 이 서버로 제출되는 공격).
"""
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, ContextManager, Iterator
from urllib.parse import urlsplit

import psycopg
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.templating import Jinja2Templates

from radar.store.db import connect

HOST, PORT = "127.0.0.1", 8765
ALLOWED_HOSTS = frozenset({f"127.0.0.1:{PORT}", f"localhost:{PORT}"})
MAX_BODY_BYTES = 64_000  # 폼은 메모 한 개가 가장 크다
TEMPLATES = Path(__file__).parent / "templates"
DbFactory = Callable[[], ContextManager[psycopg.Connection]]


class HostOriginGuard:
    """순수 ASGI 미들웨어. 요청 본문을 읽기 전에 머리글만 보고 거절한다."""

    def __init__(self, app, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = frozenset(f"http://{host}" for host in allowed_hosts)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
            if headers.get("host") not in self.allowed_hosts:
                return await PlainTextResponse("허용하지 않는 Host입니다", status_code=400)(scope, receive, send)
            if scope["method"] not in ("GET", "HEAD") and headers.get("origin") not in self.allowed_origins:
                return await PlainTextResponse("허용하지 않는 Origin입니다", status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


class SharedConnection:
    """사용자 한 명의 로컬 화면이라 연결 하나를 잠금으로 나눠 쓴다. 끊기면 다음 요청에서 다시 연결한다."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    @contextmanager
    def __call__(self) -> Iterator[psycopg.Connection]:
        with self._lock:
            if self._conn is None or self._conn.closed:
                self._conn = connect(self._url)
            try:
                yield self._conn
            except psycopg.OperationalError:
                self._conn.close()
                self._conn = None
                raise


def safe_url(url: str | None) -> str | None:
    """외부 글이 준 링크는 http·https만 링크로 만든다(javascript: 같은 주소를 막는다)."""
    return url if url and urlsplit(url).scheme in ("http", "https") else None


def templates() -> Jinja2Templates:
    found = Jinja2Templates(directory=TEMPLATES)
    found.env.autoescape = True  # 외부 글의 제목·발췌가 그대로 HTML이 되지 않게 한다(기본값에 기대지 않는다)
    found.env.filters["safe_url"] = safe_url
    return found


def create_app(db: DbFactory, allowed_hosts: frozenset[str] = ALLOWED_HOSTS) -> Starlette:
    from radar.web.routes import ROUTES  # routes가 이 모듈의 templates·safe_url을 쓴다

    app = Starlette(routes=ROUTES, middleware=[Middleware(HostOriginGuard, allowed_hosts=allowed_hosts)],
                    max_body_size=MAX_BODY_BYTES)
    app.state.db = db
    app.state.templates = templates()
    return app


def serve(url: str) -> None:
    import uvicorn

    uvicorn.run(create_app(SharedConnection(url)), host=HOST, port=PORT, log_level="warning")
