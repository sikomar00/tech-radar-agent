"""그래프 화면과 관계 판정(IA-5, IA-3 관련 기술, UJ-7·UJ-8, 4단계 게이트 표본)."""
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from radar.domain.relations import RELATION_TYPES
from radar.store import graph as graph_store
from radar.store import relations
from radar.web.routes import _back, _form, _number, _render

TYPES = ("PRODUCT", "FRAMEWORK_LIBRARY", "PROTOCOL_STANDARD", "MODEL")


async def graph_page(request: Request) -> Response:
    """그래프는 화면 스크립트가 /graph.json으로 그리고, 같은 내용의 표는 여기서 만든다(화면 낭독기·키보드용)."""
    all_nodes = request.query_params.get("all") == "1"
    with request.app.state.db() as conn:
        data = graph_store.graph(conn, all_nodes=all_nodes)
    names = {n["id"]: n["name"] for n in data["nodes"]}
    return _render(request, "graph.html", {"types": TYPES, "relation_types": RELATION_TYPES, "all_nodes": all_nodes,
                                           "nodes": data["nodes"], "edges": data["edges"], "names": names})


async def graph_json(request: Request) -> Response:
    """점·선 데이터. 글자는 화면 스크립트가 HTML이 아닌 글자 데이터로만 넣는다."""
    params = request.query_params
    center, depth = _number(params.get("center")), 2 if params.get("depth") == "2" else 1
    with request.app.state.db() as conn:
        data = graph_store.graph(conn, center, depth, params.get("all") == "1")
    return JSONResponse(data)


async def sample_page(request: Request) -> Response:
    with request.app.state.db() as conn:
        rows = graph_store.sample(conn)
    verified, judged, precision = graph_store.precision(rows)
    return _render(request, "sample.html", {"rows": rows, "verified": verified, "judged": judged, "precision": precision})


async def relation_action(request: Request) -> Response:
    """확인·확인 취소·숨기기·되돌리기. 숨겨도 지우지 않는다."""
    relation_id, action, form = request.path_params["relation_id"], request.path_params["action"], await _form(request)
    with request.app.state.db() as conn:
        if not relations.change(conn, relation_id, action):
            return PlainTextResponse("잘못된 요청입니다", status_code=400)
    return _back(form, "/graph")


GRAPH_ROUTES = [
    Route("/graph", graph_page),
    Route("/graph.json", graph_json),
    Route("/relations/sample", sample_page),
    Route("/relations/{relation_id:int}/{action:str}", relation_action, methods=["POST"]),
]
