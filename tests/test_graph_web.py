"""4단계 화면(IA v1.2, UJ-7~UJ-10): 그래프 데이터, 관련 기술 표, 직접 잇기, 검토 목록, 판정 표본, 정적 파일."""
import hashlib
from pathlib import Path

from radar.domain.relations import Proposed
from radar.store import relations
from radar.store.graph import freeze_sample
from radar.web.app import STATIC
from tests.test_web import ORIGIN, _tech, client  # noqa: F401  client는 fixture다

CYTOSCAPE_SHA256 = "081591ab735b2b6a3b31a3c60f8f3a3e2fee8c92939e0c7820ad1208571b6cd8"  # 3.34.4, npm sha512 무결성 대조 뒤 고정


def _related(db) -> tuple[int, int, int, int]:
    a, item = _tech(db, "Ollama")
    b, _ = _tech(db, "llama.cpp", "FRAMEWORK_LIBRARY")
    c, _ = _tech(db, "Open WebUI")
    relations.save_ai(db, [Proposed(a, b, "BUILT_ON", "Ollama is built on llama.cpp."),
                           Proposed(min(a, c), max(a, c), "INTEGRATES_WITH", "works with")], item, None)
    return a, b, c, item


def test_the_vendored_library_is_the_checked_file() -> None:
    assert hashlib.sha256((STATIC / "cytoscape.min.js").read_bytes()).hexdigest() == CYTOSCAPE_SHA256
    assert "MIT" in (STATIC / "cytoscape.LICENSE.txt").read_text(encoding="utf-8") or "Permission is hereby granted" in (
        STATIC / "cytoscape.LICENSE.txt").read_text(encoding="utf-8")


def test_static_files_are_served_and_guarded(client) -> None:
    assert client.get("/static/graph.js").status_code == 200
    assert client.get("/static/graph.js", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/static/../app.py").status_code == 404


def test_graph_json_matches_the_database_and_hides_hidden_and_archived(client, db) -> None:
    a, b, c, _ = _related(db)
    lonely, _ = _tech(db, "Lonely")
    data = client.get("/graph.json").json()
    assert {n["id"] for n in data["nodes"]} == {a, b, c} and len(data["edges"]) == 2
    built_on = next(e for e in data["edges"] if e["type"] == "BUILT_ON")
    assert (built_on["source"], built_on["target"], built_on["verified"], built_on["symmetric"], built_on["label"]) == (a, b, False, False, "기반")
    assert lonely in {n["id"] for n in client.get("/graph.json?all=1").json()["nodes"]}
    rid = relations.for_technology(db, b)[0]["id"]
    relations.change(db, rid, "hide")
    db.execute("update technologies set archived_at = now() where id = %s", (c,))
    assert client.get("/graph.json").json()["edges"] == []


def test_the_neighborhood_follows_edges_one_or_two_steps(client, db) -> None:
    a, b, c, _ = _related(db)
    d, _ = _tech(db, "GGUF tool", "FRAMEWORK_LIBRARY")
    relations.add_manual(db, d, b, "EXTENDS")
    one = {n["id"] for n in client.get(f"/graph.json?center={b}").json()["nodes"]}
    two = {n["id"] for n in client.get(f"/graph.json?center={b}&depth=2").json()["nodes"]}
    assert one == {a, b, d} and two == {a, b, c, d}


def test_names_stay_text_in_the_graph_page_and_detail_table(client, db) -> None:
    evil, item = _tech(db, "<script>alert(1)</script>")
    other, _ = _tech(db, "Other")
    relations.save_ai(db, [Proposed(min(evil, other), max(evil, other), "RELATED_TO", "<b>quote</b>")], item, None)
    for page in (client.get("/graph").text, client.get(f"/kb/{other}").text):
        assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>quote</b>" not in client.get(f"/kb/{other}").text
    assert client.get("/graph.json").json()["nodes"][0]["name"] in ("<script>alert(1)</script>", "Other")  # JSON은 글자 그대로


def test_confirm_and_hide_buttons_change_only_with_our_origin(client, db) -> None:
    a, b, _, _ = _related(db)
    rid = next(r["id"] for r in relations.for_technology(db, a) if r["relation_type"] == "BUILT_ON")
    assert client.post(f"/relations/{rid}/verify", data={"next": f"/kb/{a}"}).status_code == 403
    assert client.post(f"/relations/{rid}/verify", data={"next": f"/kb/{a}"}, headers=ORIGIN).status_code == 303
    assert client.post(f"/relations/{rid}/drop", data={}, headers=ORIGIN).status_code == 400
    page = client.get(f"/kb/{a}").text
    assert "확인함" in page and 'id="neighborhood"' in page


def test_manual_linking_by_name_respects_the_matrix(client, db) -> None:
    app, _ = _tech(db, "App")
    lib, _ = _tech(db, "Lib", "FRAMEWORK_LIBRARY")
    bad = client.post(f"/kb/{lib}/link", data={"other": "App", "relation_type": "IMPLEMENTS", "next": f"/kb/{lib}"}, headers=ORIGIN)
    assert "%ED%97%88%EC%9A%A9" in bad.headers["location"]  # "허용 매트릭스" 안내
    missing = client.post(f"/kb/{lib}/link", data={"other": "Nope", "relation_type": "EXTENDS", "next": f"/kb/{lib}"}, headers=ORIGIN)
    assert relations.for_technology(db, lib) == [] and missing.status_code == 303
    client.post(f"/kb/{lib}/link", data={"other": "app", "relation_type": "EXTENDS", "direction": "out", "next": f"/kb/{lib}"}, headers=ORIGIN)
    row = relations.for_technology(db, lib)[0]
    assert (row["source_technology_id"], row["target_technology_id"], row["origin"]) == (lib, app, "manual")


def test_monitor_lists_proposals_and_discards_them(client, db) -> None:
    a, item = _tech(db, "A")
    held, _ = _tech(db, "Held", None)
    relations.save_ai(db, [Proposed(a, held, "BUILT_ON", "A is built on Held")], item, None)
    page = client.get("/monitor").text
    assert "관계 검토 목록 1개" in page and f'href="#hold-{held}"' in page
    proposal = relations.open_proposals(db)[0]["id"]
    assert client.post(f"/monitor/{proposal}/discard", data={"next": "/monitor"}, headers=ORIGIN).status_code == 303
    assert relations.open_proposals(db) == []


def test_the_sample_page_counts_judgements(client, db) -> None:
    a, b, c, _ = _related(db)
    assert "아직 고정한 표본이 없습니다" in client.get("/relations/sample").text
    assert freeze_sample(db, size=2)[0] and not freeze_sample(db, size=2)[0]
    rows = relations.for_technology(db, a)
    relations.change(db, rows[0]["id"], "verify")
    relations.change(db, rows[1]["id"], "hide")
    d, item = _tech(db, "Later")
    relations.save_ai(db, [Proposed(min(a, d), max(a, d), "RELATED_TO", "x")], item, None)  # 새 관계가 생겨도 표본은 그대로
    page = client.get("/relations/sample").text
    assert "판정 2/2" in page and "Precision 50.0%" in page and "Later" not in page


def test_static_dir_holds_only_what_we_ship() -> None:
    assert sorted(p.name for p in Path(STATIC).iterdir()) == ["cytoscape.LICENSE.txt", "cytoscape.min.js", "graph.js"]
