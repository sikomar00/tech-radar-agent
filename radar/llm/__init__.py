"""분석 모델 호출 경계. 공급자는 LlmClient 뒤에 있고, 모델 응답은 공급자와 상관없이 jsonschema로 다시 검증한다(ADR-0004)."""
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator

ERROR_KINDS = ("timeout", "network", "rate_limited", "http_status", "parse", "schema")


@dataclass(frozen=True)
class LlmResult:
    data: dict[str, Any]
    tokens_in: int | None = None
    tokens_out: int | None = None


class LlmError(Exception):
    """모델 호출 하나가 실패한 이유. retryable이 False면 같은 입력으로 다시 해도 소용없다."""

    def __init__(self, kind: str, message: str, retryable: bool) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.retryable = retryable


class LlmClient(Protocol):
    model: str

    def complete_json(self, instruction: str, data: str, schema: dict[str, Any]) -> LlmResult:
        """지시문과 데이터 구간 문자열을 보내 스키마에 맞는 JSON을 받는다. 도구 정의는 보내지 않는다."""
        ...


def complete_validated(client: LlmClient, instruction: str, data: str, schema: dict[str, Any]) -> LlmResult:
    """호출하고 응답을 스키마로 다시 검증한다. 공급자가 스키마 준수를 보장하지 않으므로 이 검증이 실제 방어선이다."""
    result = client.complete_json(instruction, data, schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(result.data), key=lambda e: list(e.path))
    if errors:
        where = "/".join(str(p) for p in errors[0].path) or "(root)"
        raise LlmError("schema", f"{where}: {errors[0].validator} 위반 ({len(errors)}건)", retryable=True)
    return result
