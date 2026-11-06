"""Gemini REST(`generateContent`) 어댑터. 표준 라이브러리 urllib만 쓰고, 키는 헤더로만 보낸다."""
import json
import socket
import time
from http.client import HTTPException
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from radar.llm import LlmError, LlmResult

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_RESPONSE_BYTES = 2_000_000
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _urllib_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    try:
        with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=timeout) as res:
            return res.status, res.read(MAX_RESPONSE_BYTES)
    except HTTPError as err:  # URLError의 하위 클래스라서 먼저 잡는다
        try:
            return err.code, err.read(MAX_RESPONSE_BYTES)
        finally:
            err.close()


class GeminiClient:
    """호출 사이에 min_interval초를 둔다(무료 한도 분당 15회 중 10회 이하). 재시도는 잡 단위로 하고 여기서는 하지 않는다."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 90.0, min_interval: float = 6.0,
                 transport: Transport = _urllib_post, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.model = model
        self._key = api_key
        self._timeout = timeout
        self._min_interval = min_interval
        self._transport, self._sleep, self._clock = transport, sleep, clock
        self._last_call: float | None = None

    def complete_json(self, instruction: str, data: str, schema: dict[str, Any]) -> LlmResult:
        body = {  # tools 키를 두지 않는다. 모델이 도구를 부를 길이 없다(S2-06)
            "systemInstruction": {"parts": [{"text": instruction}]},
            "contents": [{"role": "user", "parts": [{"text": data}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema, "temperature": 0},
        }
        status, raw = self._post(json.dumps(body).encode("utf-8"))
        if status != 200:
            raise self._status_error(status, raw)
        return self._parse(raw)

    def _post(self, body: bytes) -> tuple[int, bytes]:
        if self._last_call is not None:
            wait = self._last_call + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        try:
            return self._transport(f"{BASE_URL}/{self.model}:generateContent", headers, body, self._timeout)
        except (socket.timeout, TimeoutError) as err:
            raise LlmError("timeout", type(err).__name__, retryable=True) from err
        except URLError as err:
            timed_out = isinstance(err.reason, (socket.timeout, TimeoutError))
            raise LlmError("timeout" if timed_out else "network", type(err.reason).__name__, retryable=True) from err
        except (OSError, HTTPException) as err:  # 연결 끊김, SSL 오류 등. 오류 문구는 남기지 않는다
            raise LlmError("network", type(err).__name__, retryable=True) from err

    def _status_error(self, status: int, raw: bytes) -> LlmError:
        detail = ""
        try:
            error = json.loads(raw).get("error", {})
            detail = f" {error.get('status', '')} {str(error.get('message', ''))[:150]}".replace(self._key, "***")
        except (ValueError, AttributeError):
            pass
        kind = "rate_limited" if status == 429 else "http_status"
        return LlmError(kind, f"HTTP {status}{detail}".rstrip(), retryable=status in RETRYABLE_STATUS)

    @staticmethod
    def _parse(raw: bytes) -> LlmResult:
        try:
            response = json.loads(raw)
            text = response["candidates"][0]["content"]["parts"][0]["text"]
        except (ValueError, LookupError, TypeError) as err:  # 안전 필터로 막히면 candidates가 비어 있다
            raise LlmError("parse", "응답에서 결과 글을 찾지 못했다", retryable=True) from err
        try:
            data = json.loads(text)
        except ValueError as err:
            raise LlmError("parse", "결과 글이 JSON이 아니다", retryable=True) from err
        if not isinstance(data, dict):
            raise LlmError("parse", "결과 JSON이 객체가 아니다", retryable=True)
        usage = response.get("usageMetadata") or {}
        return LlmResult(data, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))
