"""화면 처리(IA-1~4, UJ-1~5). 읽기는 GET, 바꾸기는 POST 폼으로 받고 원래 화면으로 돌려보낸다(PRG)."""
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from radar import holds
from radar.analyze import DAILY_CAP, analysis_paused
from radar.domain.profile import ALL_FIELDS
from radar.store import knowledge, monitor, views
from radar.store.analysis import AnalysisStore
from radar.store.jobs import JobQueue
from radar.worker import utc_now

MAX_NOTE = 5000
DEFAULT_DAYS = 7


async def _form(request: Request) -> dict[str, str]:
    """python-multipart 없이 urlencoded 폼만 읽는다(ADR-0006)."""
    pairs = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[0] for key, values in pairs.items()}


def _back(form: dict[str, str], default: str, message: str | None = None) -> RedirectResponse:
    """폼의 next로 돌려보낸다. 이 사이트 안의 경로만 받는다(다른 사이트로 보내는 열린 리다이렉트 방지)."""
    target = form.get("next", "")
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        target = default
    if message:
        target += ("&" if "?" in target else "?") + urlencode({"msg": message})
    return RedirectResponse(target, status_code=303)


def _render(request: Request, name: str, context: dict[str, Any]) -> Response:
    context.setdefault("msg", request.query_params.get("msg"))
    return request.app.state.templates.TemplateResponse(request, name, context)


def _days(value: str | None, default: int | None) -> int | None:
    return min(max(int(value), 1), 365) if value and value.isdigit() else default


async def feed(request: Request) -> Response:
    params, now = request.query_params, utc_now()
    days = _days(params.get("days"), DEFAULT_DAYS)
    state = params.get("state") if params.get("state") in views.STATES else (None if params.get("state") == "all" else "unread")
    with request.app.state.db() as conn:
        rows = views.feed(conn, views.user_id(conn), now - timedelta(days=days), state)
        last = monitor.last_collection(conn)
    stale = last is None or now - last > monitor.STALE_COLLECTION
    return _render(request, "feed.html", {"rows": rows, "days": days, "state": state or "all", "stale": stale, "last": last})


async def search(request: Request) -> Response:
    params = request.query_params
    days = _days(params.get("days"), None)
    filters = {
        "type": params.get("type") if params.get("type") in views.SEARCH_TYPES else None,
        "kind": params.get("source") if params.get("source") in views.SOURCE_KINDS else None,
        "since": utc_now() - timedelta(days=days) if days else None,
        "state": params.get("state") if params.get("state") in views.STATES else None,
    }
    q = params.get("q", "")[:200]
    with request.app.state.db() as conn:
        rows = views.search(conn, views.user_id(conn), q, filters) if q.strip() or any(filters.values()) else []
    return _render(request, "search.html", {"rows": rows, "q": q, "filters": filters, "days": days,
                                            "types": views.SEARCH_TYPES, "kinds": views.SOURCE_KINDS})


async def detail(request: Request) -> Response:
    technology_id = request.path_params["technology_id"]
    with request.app.state.db() as conn:
        found = views.detail(conn, views.user_id(conn), technology_id)
        claims = knowledge.current_claims(conn, technology_id) if found else {}
    if found is None:
        return PlainTextResponse("없는 기술입니다", status_code=404)
    return _render(request, "detail.html", {**found, "claims": claims, "fields": ALL_FIELDS})


async def change(request: Request) -> Response:
    """기술 상세의 바꾸기: 상태, 중요도, 메모, 보관, 되돌리기."""
    technology_id, action, form = request.path_params["technology_id"], request.path_params["action"], await _form(request)
    message = None
    with request.app.state.db() as conn:
        user = views.user_id(conn)
        if action == "state" and form.get("state") in views.STATES:
            views.set_state(conn, user, technology_id, form["state"])
        elif action == "importance" and form.get("importance", "") in ("", "1", "2", "3", "4", "5"):
            views.set_importance(conn, user, technology_id, int(form["importance"]) if form["importance"] else None)
        elif action == "note" and form.get("body", "").strip():
            views.add_note(conn, user, technology_id, form["body"].strip()[:MAX_NOTE])
        elif action == "archive":
            message = "보관했습니다" if views.archive(conn, technology_id) else "이미 보관된 기술입니다"
        elif action == "unarchive":
            message = views.unarchive(conn, technology_id)[1]
        else:
            return PlainTextResponse("잘못된 요청입니다", status_code=400)
    return _back(form, f"/kb/{technology_id}", message)


async def monitor_page(request: Request) -> Response:
    now = utc_now()
    with request.app.state.db() as conn:
        context = {
            "collection": monitor.collection(conn, now), "last": monitor.last_collection(conn),
            "calls_today": AnalysisStore(conn, utc_now).calls_today(), "cap": DAILY_CAP,
            "paused": analysis_paused(conn, utc_now), "failures": monitor.recent_failures(conn),
            "job_counts": monitor.job_counts(conn), "failed_jobs": monitor.failed_jobs(conn),
            "holds": monitor.holds(conn), "hold_types": holds.HOLD_TYPES,
        }
    return _render(request, "monitor.html", context)


async def monitor_action(request: Request) -> Response:
    """Monitor의 바꾸기: 실패 잡 다시 실행, 보류 처리(유형 지정, 기존 기술에 연결, 새 기술로). CLI와 같은 함수를 쓴다."""
    target, action, form = request.path_params["target_id"], request.path_params["action"], await _form(request)
    with request.app.state.db() as conn:
        if action == "retry":
            ok, message = JobQueue(conn, utc_now).retry_failed(target), f"잡 #{target}를 다시 대기열에 넣었습니다"
            message = message if ok else "failed 상태의 잡이 아닙니다"
        elif action == "type":
            ok, message = holds.set_type(conn, target, form.get("type", ""))
        elif action == "link" and form.get("technology_id", "").isdigit():
            ok, message = holds.link_item(conn, utc_now, target, int(form["technology_id"]))
        elif action == "new":
            ok, message = holds.new_from_item(conn, utc_now, target)
        else:
            return PlainTextResponse("잘못된 요청입니다", status_code=400)
    return _back(form, "/monitor", message)


ROUTES = [
    Route("/", feed),
    Route("/kb", search),
    Route("/kb/{technology_id:int}", detail),
    Route("/kb/{technology_id:int}/{action:str}", change, methods=["POST"]),
    Route("/monitor", monitor_page),
    Route("/monitor/{target_id:int}/{action:str}", monitor_action, methods=["POST"]),
]
