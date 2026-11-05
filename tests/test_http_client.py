"""UrllibClient를 실제 로컬 서버로 시험한다. 연결이 끊기거나 본문이 잘리는 경우도 요청 실패로 분류돼야 한다."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from radar.adapters.http import UrllibClient, call
from radar.domain.models import FetchError


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server가 정한 이름)
        if self.path == "/ok":
            self._send(200, b"hello")
        elif self.path == "/missing":
            self._send(404, b"nope")
        elif self.path == "/truncated":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"abc")  # 100바이트를 약속하고 3바이트만 보낸 뒤 끊는다
        elif self.path == "/slow":
            time.sleep(1.0)
            self._send(200, b"late")
        # /drop: 아무것도 보내지 않고 연결을 닫는다

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_success_returns_status_body_and_lowercase_headers(base_url: str) -> None:
    res = UrllibClient(pause=0).get(f"{base_url}/ok")
    assert (res.status, res.body) == (200, b"hello")
    assert res.headers["content-length"] == "5"


def test_error_status_is_returned_not_raised(base_url: str) -> None:
    res = UrllibClient(pause=0).get(f"{base_url}/missing")
    assert (res.status, res.body) == (404, b"nope")


@pytest.mark.parametrize("path", ["/truncated", "/drop"])
def test_cut_off_responses_are_network_failures(base_url: str, path: str) -> None:
    with pytest.raises(FetchError) as err:
        UrllibClient(pause=0).get(f"{base_url}{path}")
    assert err.value.kind == "network"


def test_slow_response_is_a_timeout(base_url: str) -> None:
    with pytest.raises(FetchError) as err:
        UrllibClient(pause=0, timeout=0.2).get(f"{base_url}/slow")
    assert err.value.kind == "timeout"


def test_refused_connection_is_a_network_failure() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    server.server_close()  # 포트를 열어 둔 서버가 없다
    outcomes: list = []
    assert call(UrllibClient(pause=0), f"http://127.0.0.1:{port}/", outcomes) is None
    assert [(o.ok, o.error_kind) for o in outcomes] == [(False, "network")]


def test_requests_are_spaced_by_the_pause(base_url: str) -> None:
    client = UrllibClient(pause=0.3)
    started = time.monotonic()
    client.get(f"{base_url}/ok")
    client.get(f"{base_url}/ok")
    # Windows의 시계 해상도(약 15ms)와 sleep 오차를 감안해 약간 여유를 둔다
    assert time.monotonic() - started >= 0.25
