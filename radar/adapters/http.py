"""HTTP 호출과 실패 분류. 어댑터는 이 모듈로만 네트워크에 접근한다."""
import json
import socket
import time
from dataclasses import dataclass, replace
from http.client import HTTPException
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from radar.domain.models import FetchError, RequestOutcome

USER_AGENT = "tech-radar-agent/0.1 (personal research, read-only)"
MAX_BODY_BYTES = 5_000_000


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]  # 키는 소문자


class HttpClient(Protocol):
    def get(self, url: str) -> HttpResponse: ...


class UrllibClient:
    """요청 사이에 pause초를 두는 표준 라이브러리 클라이언트. 재시도는 하지 않는다(성공률을 숨기지 않으려고)."""

    def __init__(self, pause: float = 1.0, timeout: float = 20.0) -> None:
        self._pause = pause
        self._timeout = timeout
        self._last_request = 0.0

    def get(self, url: str) -> HttpResponse:
        wait = self._last_request + self._pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=self._timeout) as res:
                return HttpResponse(res.status, _read_limited(res), _lower_keys(res.headers))
        except HTTPError as err:  # URLError의 하위 클래스라서 먼저 잡는다
            return _error_response(err)
        except (socket.timeout, TimeoutError) as err:
            raise FetchError("timeout", repr(err)) from err
        except URLError as err:
            timed_out = isinstance(err.reason, (socket.timeout, TimeoutError))
            raise FetchError("timeout" if timed_out else "network", repr(err)) from err
        except (OSError, HTTPException) as err:  # 연결 끊김, SSL 오류, 응답이 시작되기 전에 닫힌 연결 등
            raise FetchError("network", repr(err)) from err


def _read_limited(res: Any) -> bytes:
    body = res.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise FetchError("parse", f"응답이 {MAX_BODY_BYTES}바이트를 넘습니다")
    expected = res.headers.get("Content-Length")
    # read(amt)는 연결이 중간에 끊겨도 예외 없이 짧은 본문을 돌려주므로, 잘린 응답을 성공으로 세지 않게 길이를 맞춰 본다
    if expected and expected.isdigit() and int(expected) != len(body):
        raise FetchError("network", f"본문이 {expected}바이트여야 하는데 {len(body)}바이트만 받았습니다")
    return body


def _error_response(err: HTTPError) -> HttpResponse:
    try:
        body = err.read(MAX_BODY_BYTES)
    except (OSError, HTTPException) as read_err:
        raise FetchError("network", repr(read_err)) from read_err
    finally:
        err.close()
    return HttpResponse(err.code, body, _lower_keys(err.headers))


def _lower_keys(headers: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def failure_kind(res: HttpResponse) -> str | None:
    if 200 <= res.status < 300:
        return None
    limited = res.status == 429 or (res.status == 403 and res.headers.get("x-ratelimit-remaining") == "0")
    return "rate_limited" if limited else "http_status"


def call(http: HttpClient, url: str, outcomes: list[RequestOutcome]) -> HttpResponse | None:
    """호출하고 결과를 outcomes에 남긴다. 실패하면 None을 돌려준다."""
    try:
        res = http.get(url)
    except FetchError as err:
        outcomes.append(RequestOutcome(url, False, err.kind, err.message))
        return None
    kind = failure_kind(res)
    outcomes.append(RequestOutcome(url, kind is None, kind, f"HTTP {res.status}" if kind else None))
    return None if kind else res


def mark_parse_failure(outcomes: list[RequestOutcome], message: str) -> None:
    """마지막 요청의 응답을 해석하지 못했을 때, 그 요청을 parse 실패로 바꾼다."""
    outcomes[-1] = replace(outcomes[-1], ok=False, error_kind="parse", message=message)


def get_json(http: HttpClient, url: str, outcomes: list[RequestOutcome]) -> Any | None:
    res = call(http, url, outcomes)
    if res is None:
        return None
    try:
        return json.loads(res.body)
    except ValueError as err:
        mark_parse_failure(outcomes, repr(err))
        return None
