"""3단계에서 바뀐 식별 규칙: 보관 기술에 잇기, 규칙 이벤트(S3-04), 유형 넓히기와 retype(S3-09)."""
from radar import events, holds
from radar.store.technologies import TechnologyStore
from tests.conftest import Clock, FakeLlm, add_item
from tests.test_analyze import classify, run, technologies


def _github(db, url: str, metadata: dict, when: str = "2026-11-01T00:00:00Z") -> int:
    item = add_item(db, kind="github", url=url, title=url.rsplit("/", 2)[-2] + " x", metadata=metadata)
    db.execute("update source_items set published_at = %s where id = %s", (when, item))
    return item


def _events(db) -> list[tuple]:
    return [(r["event_type"], r["evidence_key"]) for r in
            db.execute("select event_type, evidence_key from technology_events order by id")]


def test_a_new_item_named_like_an_archived_technology_joins_it_instead_of_recreating_it(db) -> None:
    junk, _ = TechnologyStore(db).create("Agilex 5", "PRODUCT")
    db.execute("update technologies set archived_at = now() where id = %s", (junk,))
    item = add_item(db)
    run(db, FakeLlm(classify("ENTITY", "agilex 5")), item)
    assert db.execute("select count(*)::int as n from technologies").fetchone()["n"] == 1
    assert db.execute("select technology_id from technology_sources where source_item_id = %s", (item,)).fetchone()["technology_id"] == junk


def test_an_active_technology_wins_over_an_archived_one_with_the_same_name(db) -> None:
    store = TechnologyStore(db)
    old, _ = store.create("Foo", "PRODUCT")
    db.execute("update technologies set archived_at = now() where id = %s", (old,))
    active, _ = store.create("Foo", "PRODUCT")
    item = add_item(db)
    run(db, FakeLlm(classify("UPDATE", "Foo")), item)
    assert db.execute("select technology_id from technology_sources where source_item_id = %s", (item,)).fetchone()["technology_id"] == active


def test_releases_and_new_repos_become_events_once(db) -> None:
    release = _github(db, "https://github.com/ollama/ollama/releases/tag/v1", {"kind": "release", "repo": "ollama/ollama", "tag": "v1"})
    repo = _github(db, "https://github.com/ollama/ollama", {"kind": "new_repo"}, when="2023-06-01T00:00:00Z")
    run(db, FakeLlm(classify("UPDATE", "Ollama")), release)
    run(db, FakeLlm(classify("ENTITY", "Ollama")), repo)
    assert _events(db) == [("release", "v1"), ("first_published", "https://github.com/ollama/ollama")]
    first = db.execute("select first_published_at from technologies").fetchone()["first_published_at"]
    assert first.year == 2023
    assert events.backfill(db) == 0 and len(_events(db)) == 2


def test_items_that_are_not_github_releases_or_repos_make_no_event(db) -> None:
    run(db, FakeLlm(classify("ENTITY", "Ollama")), add_item(db, kind="rss"))
    run(db, FakeLlm(classify("UPDATE", "Ollama")), _github(db, "https://github.com/o/r/issues/1", {"kind": "issue"}))
    assert _events(db) == []


def test_backfill_fills_events_for_links_made_before_stage_3(db) -> None:
    tech, _ = TechnologyStore(db).create("Ollama", "PRODUCT")
    item = _github(db, "https://github.com/ollama/ollama/releases/tag/v2", {"kind": "release", "tag": "v2"})
    db.execute("insert into technology_sources (technology_id, source_item_id, evidence_type) values (%s, %s, 'release')", (tech, item))
    assert events.backfill(db) == 1 and _events(db) == [("release", "v2")]


def test_a_model_release_now_gets_the_model_type(db) -> None:
    run(db, FakeLlm(classify("ENTITY", "Kimi K3", "MODEL")), add_item(db))
    assert technologies(db) == [("Kimi K3", "MODEL")]


def test_retype_asks_only_held_technologies_and_records_the_call_on_the_technology(db) -> None:
    store = TechnologyStore(db)
    held, _ = store.create("ERNIE 5.5", None)
    store.link(held, add_item(db, title="Baidu releases ERNIE 5.5"), "mention")
    typed, _ = store.create("LangChain", "FRAMEWORK_LIBRARY")
    llm = FakeLlm({"type": "MODEL"})
    lines = holds.retype(db, llm, Clock())
    assert lines == ["기술 #%d ERNIE 5.5: MODEL" % held] and len(llm.calls) == 1
    assert "Baidu releases ERNIE 5.5" in llm.calls[0][1]
    assert technologies(db) == [("ERNIE 5.5", "MODEL"), ("LangChain", "FRAMEWORK_LIBRARY")]
    run_row = db.execute("select kind, technology_id, is_current from analysis_runs").fetchone()
    assert (run_row["kind"], run_row["technology_id"], run_row["is_current"]) == ("retype", held, True)


def test_retype_leaves_a_technology_held_when_the_model_is_unsure(db) -> None:
    held, _ = TechnologyStore(db).create("Mystery", None)
    assert holds.retype(db, FakeLlm({"type": "NONE"}), Clock()) == [f"기술 #{held} Mystery: 모델도 정하지 못해 보류로 둠"]
    assert technologies(db) == [("Mystery", None)]


def test_retype_stops_at_the_daily_cap(db, monkeypatch) -> None:
    TechnologyStore(db).create("Held", None)
    monkeypatch.setattr("radar.holds.daily_cap_reached", lambda *_: True)
    assert holds.retype(db, FakeLlm(), Clock())[0].startswith("오늘 호출 상한")


def test_people_can_now_set_the_wider_types(db) -> None:
    held, _ = TechnologyStore(db).create("MCP", None)
    assert holds.set_type(db, held, "PROTOCOL_STANDARD")[0]
    assert not holds.set_type(db, held, "RESEARCH")[0]
