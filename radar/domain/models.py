from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ERROR_KINDS = ("http_status", "timeout", "network", "parse", "rate_limited")


class FetchError(Exception):
    """요청 하나가 실패한 이유. kind는 ERROR_KINDS 중 하나다."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class RawItem:
    external_id: str | None
    url: str
    title: str
    excerpt: str
    published_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestOutcome:
    url: str
    ok: bool
    error_kind: str | None = None
    message: str | None = None


@dataclass
class FetchResult:
    items: list[RawItem] = field(default_factory=list)
    requests: list[RequestOutcome] = field(default_factory=list)

    @property
    def failed_requests(self) -> list[RequestOutcome]:
        return [r for r in self.requests if not r.ok]
