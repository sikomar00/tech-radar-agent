"""웹 화면(IA v1.0, UJ-1~5)과 안전 규칙(SPEC 3단계 웹): Host·Origin 검사, 이스케이프, http(s) 링크만, 열린 리다이렉트 없음."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from radar.store.knowledge import save_profile
from radar.store.technologies import TechnologyStore
from radar.web.app import create_app
from tests.conftest import FakeLlm, add_item
from tests.test_analyze import classify, run

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def client(db):
    @contextmanager
    def same_connection():
        yield db

    with TestClient(create_app(same_connection, frozenset({"testserver"})), follow_redirects=False) as found:
        yield found


def _tech(db, name: str, type_: str | None = "PRODUCT", *, title: str = "item", url: str | None = None,
          aliases: tuple = ()) -> tuple[int, int]:
    store = TechnologyStore(db)
    tech, _ = store.create(name, type_)
    item = add_item(db, title=title, url=url)
    store.link(tech, item, "announcement")
    for alias in aliases:
        db.execute("insert into technology_aliases (technology_id, alias, lang) values (%s, %s, 'ko')", (tech, alias))
    return tech, item


def test_a_foreign_host_is_refused(client) -> None:
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example"}, {"Origin": "null"}])
def test_a_change_without_our_origin_is_refused(client, db, headers) -> None:
    tech, _ = _tech(db, "Ollama")
    assert client.post(f"/kb/{tech}/state", data={"state": "saved"}, headers=headers).status_code == 403
    assert db.execute("select count(*)::int as n from user_knowledge").fetchone()["n"] == 0


def test_the_feed_shows_recent_technologies_one_row_each_and_hides_archived(client, db) -> None:
    tech, _ = _tech(db, "Ollama")
    TechnologyStore(db).link(tech, add_item(db, title="second"), "mention")
    junk, _ = _tech(db, "Agilex 5")
    db.execute("update technologies set archived_at = now() where id = %s", (junk,))
    page = client.get("/").text
    assert page.count(f'href="/kb/{tech}"') == 1 and "Agilex 5" not in page and "새 기술" in page


def test_external_text_is_escaped_and_only_http_links_become_links(client, db) -> None:
    tech, item = _tech(db, "<script>alert(1)</script>", title="<img src=x onerror=alert(2)>")
    db.execute("update source_items set url = 'javascript:alert(3)' where id = %s", (item,))  # 외부 글이 준 주소라고 가정한다
    feed = client.get("/").text
    detail = client.get(f"/kb/{tech}").text
    assert "<script>alert(1)" not in feed and "&lt;script&gt;" in feed and "<img src=x" not in feed
    assert 'href="javascript:' not in detail


def test_saving_a_state_from_the_feed_returns_to_the_feed(client, db) -> None:
    tech, _ = _tech(db, "Ollama")
    res = client.post(f"/kb/{tech}/state", data={"state": "saved", "next": "/?days=7"}, headers=ORIGIN)
    assert (res.status_code, res.headers["location"]) == (303, "/?days=7")
    assert "Ollama" not in client.get("/").text  # 기본 필터는 안 읽음
    assert "Ollama" in client.get("/?state=saved").text


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example"])
def test_next_never_leaves_the_site(client, db, target) -> None:
    tech, _ = _tech(db, "Ollama")
    res = client.post(f"/kb/{tech}/state", data={"state": "learned", "next": target}, headers=ORIGIN)
    assert res.headers["location"] == f"/kb/{tech}"


def test_search_finds_a_technology_once_by_korean_alias_and_by_profile_words(client, db) -> None:
    tech, item = _tech(db, "LangChain", "FRAMEWORK_LIBRARY", aliases=("랭체인", "랭체인 프레임워크"))
    save_profile(db, tech, {"one_liner": "에이전트를 만드는 프레임워크"}, item)
    assert client.get("/kb?q=랭체인").text.count(f'href="/kb/{tech}"') == 1
    assert f'href="/kb/{tech}"' in client.get("/kb?q=에이전트를").text
    assert f'href="/kb/{tech}"' not in client.get("/kb?q=%25").text  # %는 와일드카드가 아니라 글자다


def test_detail_shows_unverified_fields_sources_and_keeps_notes_apart(client, db) -> None:
    tech, item = _tech(db, "Ollama", url="https://github.com/ollama/ollama")
    save_profile(db, tech, {"one_liner": "로컬 LLM 실행", "official_source": "https://github.com/ollama/ollama"}, item)
    client.post(f"/kb/{tech}/note", data={"body": "주말에 써 보기"}, headers=ORIGIN)
    page = client.get(f"/kb/{tech}").text
    assert "로컬 LLM 실행" in page and page.count("미확인") >= 6 and "주말에 써 보기" in page
    assert 'href="https://github.com/ollama/ollama"' in page
    assert client.get("/kb/999999").status_code == 404


def test_archive_hides_and_unarchive_refuses_when_the_name_is_taken(client, db) -> None:
    tech, _ = _tech(db, "Foo")
    client.post(f"/kb/{tech}/archive", data={}, headers=ORIGIN)
    assert "Foo" not in client.get("/").text
    _tech(db, "foo")
    res = client.post(f"/kb/{tech}/unarchive", data={}, headers=ORIGIN)
    assert "msg=" in res.headers["location"]
    assert db.execute("select archived_at from technologies where id = %s", (tech,)).fetchone()["archived_at"] is not None


def test_importance_accepts_only_one_to_five(client, db) -> None:
    tech, _ = _tech(db, "Ollama")
    client.post(f"/kb/{tech}/importance", data={"importance": "4"}, headers=ORIGIN)
    assert client.post(f"/kb/{tech}/importance", data={"importance": "9"}, headers=ORIGIN).status_code == 400
    assert db.execute("select importance from user_knowledge").fetchone()["importance"] == 4


def test_monitor_shows_failure_causes_and_resolves_holds(client, db) -> None:
    held, _ = _tech(db, "ERNIE 5.5", None)
    db.execute("insert into analysis_runs (kind, source_item_id, model, input_hash, status, error, retryable) "
               "values ('classify', %s, 'm', 'h', 'failed', 'rate_limited: HTTP 429', true)", (add_item(db),))
    page = client.get("/monitor").text
    assert "rate_limited: HTTP 429" in page and "ERNIE 5.5" in page
    client.post(f"/monitor/{held}/type", data={"type": "MODEL", "next": "/monitor"}, headers=ORIGIN)
    assert db.execute("select type from technologies where id = %s", (held,)).fetchone()["type"] == "MODEL"


def test_a_failed_job_can_be_retried_from_the_monitor(client, db) -> None:
    job = db.execute("insert into jobs (kind, payload, status, attempts) values ('analyze_item', '{}', 'failed', 3) "
                     "returning id").fetchone()["id"]
    res = client.post(f"/monitor/{job}/retry", data={"next": "/monitor"}, headers=ORIGIN)
    assert res.status_code == 303 and db.execute("select status::text as s from jobs where id = %s", (job,)).fetchone()["s"] == "queued"


def test_the_feed_warns_when_collection_has_stopped(client, db) -> None:
    assert "Monitor에서 확인하기" in client.get("/").text  # 수집 기록이 없다
    source = db.execute("insert into sources (name, kind) values ('w', 'rss') returning id").fetchone()["id"]
    db.execute("insert into collection_runs (source_id, status, started_at) values (%s, 'succeeded', %s)",
               (source, datetime.now(timezone.utc) - timedelta(minutes=10)))
    assert "Monitor에서 확인하기" not in client.get("/").text


def test_odd_numbers_and_unknown_technologies_are_client_errors_not_crashes(client, db) -> None:
    assert client.get("/?days=²").status_code == 200  # 기본 기간으로 본다
    assert client.post("/kb/999999/state", data={"state": "saved"}, headers=ORIGIN).status_code == 404
    item = add_item(db)
    res = client.post(f"/monitor/{item}/link", data={"technology_id": "²", "next": "/monitor"}, headers=ORIGIN)
    assert res.status_code == 400


def test_rows_show_why_they_are_listed(client, db) -> None:
    item = add_item(db, kind="hackernews", title="Show HN: Waymark", metadata={"score": 83})
    run(db, FakeLlm({**classify("ENTITY", "Waymark"), "reason": "워크플로 엔진 <b>발표</b>"}), item)
    feed = client.get("/").text
    assert "새 기술 발표 · HN 83점" in feed and "AI 판단: 워크플로 엔진 &lt;b&gt;발표&lt;/b&gt;" in feed
    tech = db.execute("select id from technologies").fetchone()["id"]
    assert "새 기술 발표 · HN 83점" in client.get(f"/kb/{tech}").text
