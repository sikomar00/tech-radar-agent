from radar.adapters.http import HttpResponse, call, failure_kind, get_json
from radar.domain.models import FetchError
from tests.conftest import FakeHttp, ok


def test_failure_kind_classification() -> None:
    assert failure_kind(HttpResponse(200, b"", {})) is None
    assert failure_kind(HttpResponse(204, b"", {})) is None
    assert failure_kind(HttpResponse(404, b"", {})) == "http_status"
    assert failure_kind(HttpResponse(503, b"", {})) == "http_status"
    assert failure_kind(HttpResponse(429, b"", {})) == "rate_limited"
    assert failure_kind(HttpResponse(403, b"", {"x-ratelimit-remaining": "0"})) == "rate_limited"
    assert failure_kind(HttpResponse(403, b"", {"x-ratelimit-remaining": "5"})) == "http_status"


def test_call_records_success_failure_and_network_errors() -> None:
    http = FakeHttp({
        "u/ok": ok("x"), "u/500": HttpResponse(500, b"", {}), "u/slow": FetchError("timeout", "too slow"),
    })
    outcomes: list = []
    assert call(http, "u/ok", outcomes) is not None
    assert call(http, "u/500", outcomes) is None
    assert call(http, "u/slow", outcomes) is None
    assert [(o.ok, o.error_kind) for o in outcomes] == [(True, None), (False, "http_status"), (False, "timeout")]
    assert outcomes[1].message == "HTTP 500"


def test_get_json_marks_invalid_json_as_parse_failure() -> None:
    outcomes: list = []
    assert get_json(FakeHttp({"u": ok("not json")}), "u", outcomes) is None
    assert [(o.ok, o.error_kind) for o in outcomes] == [(False, "parse")]


def test_get_json_returns_data_on_success() -> None:
    outcomes: list = []
    assert get_json(FakeHttp({"u": ok({"a": 1})}), "u", outcomes) == {"a": 1}
    assert outcomes[0].ok
