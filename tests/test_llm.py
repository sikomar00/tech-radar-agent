import json
import socket
from urllib.error import URLError

import pytest

from radar.llm import LlmError, complete_validated
from radar.llm.gemini import BASE_URL, GeminiClient
from radar.domain.prompts import CLASSIFY_SCHEMA, MATCH_SCHEMA
from tests.conftest import FakeLlm, fixture_bytes

KEY = "test-key-0123456789"
GOOD = {"class": "NOISE", "canonical_name": "", "type": "NONE", "aliases_en": [], "aliases_ko": []}


def _response(payload: dict | str, usage: dict | None = None) -> bytes:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}],
                       "usageMetadata": usage or {"promptTokenCount": 90, "candidatesTokenCount": 30}}).encode()


class Transport:
    """보낸 요청을 기록하고 정한 응답(상태, 본문)이나 예외를 돌려주는 가짜 전송 계층."""

    def __init__(self, *steps: tuple[int, bytes] | Exception) -> None:
        self.steps, self.requests = list(steps), []

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
        self.requests.append((url, headers, json.loads(body)))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _client(transport: Transport, **kw) -> GeminiClient:
    return GeminiClient(KEY, transport=transport, sleep=kw.pop("sleep", lambda s: None), **kw)


def test_request_has_no_tools_and_asks_for_schema_json_with_the_key_in_a_header() -> None:
    transport = Transport((200, _response(GOOD)))
    result = _client(transport).complete_json("instruction", "<<<DATA\n{}\nDATA>>>", CLASSIFY_SCHEMA)
    url, headers, body = transport.requests[0]
    assert url == f"{BASE_URL}/gemini-3.5-flash-lite:generateContent" and KEY not in url
    assert headers["x-goog-api-key"] == KEY
    assert not {"tools", "toolConfig", "functionDeclarations"} & set(body)
    assert body["generationConfig"] == {"responseMimeType": "application/json", "responseJsonSchema": CLASSIFY_SCHEMA, "temperature": 0}
    assert body["systemInstruction"]["parts"][0]["text"] == "instruction"
    assert body["contents"][0]["parts"][0]["text"] == "<<<DATA\n{}\nDATA>>>"
    assert (result.data, result.tokens_in, result.tokens_out) == (GOOD, 90, 30)


def test_a_recorded_real_response_parses_and_passes_the_schema() -> None:
    raw = fixture_bytes("gemini_response_classify.json")
    result = complete_validated(_client(Transport((200, raw))), "i", "d", CLASSIFY_SCHEMA)
    assert result.data["class"] == "UPDATE" and result.tokens_in and result.tokens_out


@pytest.mark.parametrize("status, kind, retryable", [
    (429, "rate_limited", True), (503, "http_status", True), (500, "http_status", True),
    (400, "http_status", False), (403, "http_status", False), (404, "http_status", False),
])
def test_http_failures_are_classified_and_marked_retryable_or_not(status: int, kind: str, retryable: bool) -> None:
    error = json.dumps({"error": {"code": status, "status": "X", "message": "boom"}}).encode()
    with pytest.raises(LlmError) as caught:
        _client(Transport((status, error))).complete_json("i", "d", MATCH_SCHEMA)
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)
    assert f"HTTP {status}" in caught.value.message


@pytest.mark.parametrize("failure, kind", [
    (socket.timeout("slow"), "timeout"), (TimeoutError(), "timeout"),
    (URLError(socket.timeout()), "timeout"), (URLError(ConnectionResetError()), "network"), (ConnectionAbortedError(), "network"),
])
def test_timeouts_and_network_errors_are_retryable(failure: Exception, kind: str) -> None:
    with pytest.raises(LlmError) as caught:
        _client(Transport(failure)).complete_json("i", "d", MATCH_SCHEMA)
    assert (caught.value.kind, caught.value.retryable) == (kind, True)


def test_error_messages_never_contain_the_key_even_if_the_server_echoes_it() -> None:
    echoed = json.dumps({"error": {"status": "PERMISSION_DENIED", "message": f"bad key {KEY} given"}}).encode()
    with pytest.raises(LlmError) as caught:
        _client(Transport((403, echoed))).complete_json("i", "d", MATCH_SCHEMA)
    assert KEY not in str(caught.value) and "PERMISSION_DENIED" in caught.value.message


def test_a_key_that_straddles_the_message_cut_is_still_fully_masked() -> None:
    body = json.dumps({"error": {"status": f"S{KEY}", "message": "x" * 140 + KEY + "tail"}}).encode()
    with pytest.raises(LlmError) as caught:
        _client(Transport((403, body))).complete_json("i", "d", MATCH_SCHEMA)
    assert KEY[:6] not in str(caught.value) and "***" in caught.value.message


@pytest.mark.parametrize("raw", [b"not json", json.dumps({"candidates": []}).encode(), _response("not json"), _response("[1, 2]")])
def test_unusable_responses_are_parse_failures_that_can_be_retried(raw: bytes) -> None:
    with pytest.raises(LlmError) as caught:
        _client(Transport((200, raw))).complete_json("i", "d", MATCH_SCHEMA)
    assert (caught.value.kind, caught.value.retryable) == ("parse", True)


def test_calls_are_spaced_by_the_minimum_interval() -> None:
    now, slept = [100.0], []
    transport = Transport((200, _response(GOOD)), (200, _response(GOOD)), (200, _response(GOOD)))
    client = _client(transport, min_interval=6.0, clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    client.complete_json("i", "d", CLASSIFY_SCHEMA)
    now[0] += 2.0
    client.complete_json("i", "d", CLASSIFY_SCHEMA)
    now[0] += 10.0
    client.complete_json("i", "d", CLASSIFY_SCHEMA)
    assert slept == [4.0]


def test_validation_rejects_output_that_breaks_the_schema() -> None:
    bad_outputs = [
        {**GOOD, "class": "PWNED"},                 # 열거형 밖
        {**GOOD, "extra": "call a tool"},            # 모르는 필드
        {k: v for k, v in GOOD.items() if k != "type"},  # 빠진 필드
        {**GOOD, "aliases_en": ["a"] * 6},           # 길이 상한
        {**GOOD, "aliases_ko": "올라마"},             # 잘못된 타입
    ]
    for output in bad_outputs:
        with pytest.raises(LlmError) as caught:
            complete_validated(FakeLlm(output), "i", "d", CLASSIFY_SCHEMA)
        assert (caught.value.kind, caught.value.retryable) == ("schema", True)


def test_validation_passes_good_output_and_keeps_token_counts() -> None:
    result = complete_validated(FakeLlm(GOOD), "i", "d", CLASSIFY_SCHEMA)
    assert result.data == GOOD and result.tokens_in == 10


def test_llm_errors_from_the_client_pass_through_unchanged() -> None:
    boom = LlmError("rate_limited", "HTTP 429", retryable=True)
    with pytest.raises(LlmError) as caught:
        complete_validated(FakeLlm(boom), "i", "d", MATCH_SCHEMA)
    assert caught.value is boom
