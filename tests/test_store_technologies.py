from radar.store.technologies import CANDIDATE_LIMIT, TechnologyStore
from tests.conftest import add_item


def test_create_returns_the_existing_technology_when_the_lowercase_name_collides(db) -> None:
    store = TechnologyStore(db)
    first, created = store.create("Next.js", "FRAMEWORK_LIBRARY")
    second, created_again = store.create("next.JS", None)
    assert (created, created_again, second) == (True, False, first)
    assert db.execute("select count(*) as n from technologies").fetchone()["n"] == 1


def test_a_name_collision_inside_a_transaction_does_not_poison_it(db) -> None:
    store = TechnologyStore(db)
    first, _ = store.create("Ollama", "PRODUCT")
    with db.transaction():
        same, created = store.create("OLLAMA", None)
        other, _ = store.create("LangChain", "FRAMEWORK_LIBRARY")
    assert (same, created) == (first, False) and other != first


def test_a_hold_is_a_technology_without_a_type(db) -> None:
    held, _ = TechnologyStore(db).create("ERNIE 5.5", None)
    assert db.execute("select id from pending_classification").fetchone()["id"] == held


def test_definite_match_ignores_case_and_looks_at_aliases(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("LangChain", "FRAMEWORK_LIBRARY")
    store.add_aliases(tech, ["lang-chain"], ["랭체인"])
    assert store.definite_match("langCHAIN", None) == [tech]
    assert store.definite_match("랭체인", None) == [tech]
    assert store.definite_match("Lang-Chain", None) == [tech]
    assert store.definite_match("LangGraph", None) == []


def test_definite_match_finds_a_technology_through_its_linked_github_repo(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("Claude Code", "PRODUCT")
    repo_item = add_item(db, kind="github", url="https://github.com/anthropics/claude-code", metadata={"kind": "new_repo"})
    store.link(tech, repo_item, "official")
    assert store.definite_match("unrelated name", "anthropics/claude-code") == [tech]
    other, _ = store.create("Other", "PRODUCT")
    release = add_item(db, kind="github", url="https://github.com/Foo/Bar/releases/tag/v1", metadata={"kind": "release", "repo": "Foo/Bar"})
    store.link(other, release, "release")
    assert store.definite_match("x", "foo/bar") == [other]
    assert store.definite_match("x", None) == [] and store.definite_match("x", "no/such") == []


def test_definite_match_reports_every_technology_when_the_name_is_ambiguous(db) -> None:
    store = TechnologyStore(db)
    a, _ = store.create("Agent Kit", "PRODUCT")
    b, _ = store.create("Agent SDK", "FRAMEWORK_LIBRARY")
    store.add_aliases(a, ["agent"], [])
    store.add_aliases(b, ["agent"], [])
    assert store.definite_match("Agent", None) == sorted([a, b])


def test_archived_technologies_are_never_matched(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("OldTool", "PRODUCT")
    db.execute("update technologies set archived_at = now() where id = %s", (tech,))
    assert store.definite_match("OldTool", None) == [] and store.fuzzy_candidates(["oldtool"]) == []


def test_fuzzy_candidates_keep_close_names_and_drop_distant_ones(db) -> None:
    store = TechnologyStore(db)
    chain, _ = store.create("LangChain", "FRAMEWORK_LIBRARY")
    store.create("LangGraph", "FRAMEWORK_LIBRARY")
    ollama, _ = store.create("Ollama", "PRODUCT")
    store.add_aliases(ollama, [], ["올라마"])
    assert store.fuzzy_candidates(["langchain"]) == [chain]
    assert store.fuzzy_candidates(["Ollama", "올라마"]) == [ollama]
    assert store.fuzzy_candidates(["something else entirely"]) == []


def test_fuzzy_candidates_are_limited_to_the_top_few(db) -> None:
    store = TechnologyStore(db)
    ids = []
    for n in range(CANDIDATE_LIMIT + 2):
        tech, _ = store.create(f"Distinct Product {n}x{n * 7}", "PRODUCT")
        store.add_aliases(tech, ["agent framework"], [])
        ids.append(tech)
    assert store.fuzzy_candidates(["agent framework"]) == ids[:CANDIDATE_LIMIT]


def test_describe_lists_names_and_sorted_aliases(db) -> None:
    store = TechnologyStore(db)
    bare, _ = store.create("Bare", "PRODUCT")
    full, _ = store.create("Full", "PRODUCT")
    store.add_aliases(full, ["zeta", "alpha"], ["가나"])
    rows = {r["id"]: r for r in store.describe([bare, full])}
    assert rows[bare]["aliases"] == [] and rows[full]["aliases"] == ["alpha", "zeta", "가나"]
    assert rows[full]["canonical_name"] == "Full"


def test_add_aliases_skips_the_canonical_name_and_case_duplicates_and_is_repeatable(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("Claude Code", "PRODUCT")
    store.add_aliases(tech, ["claude code", "claude-code", "Claude-Code"], ["클로드 코드"])
    store.add_aliases(tech, ["CLAUDE-CODE"], ["클로드 코드"])
    rows = db.execute("select alias, lang from technology_aliases order by alias").fetchall()
    assert [(r["alias"], r["lang"]) for r in rows] == [("claude-code", "en"), ("클로드 코드", "ko")]


def test_link_is_repeatable_and_starts_unverified(db) -> None:
    store = TechnologyStore(db)
    tech, _ = store.create("Ollama", "PRODUCT")
    item = add_item(db)
    store.link(tech, item, "mention")
    store.link(tech, item, "mention")
    rows = db.execute("select evidence_type, confidence from technology_sources").fetchall()
    assert [(r["evidence_type"], r["confidence"]) for r in rows] == [("mention", "Unverified")]
    assert db.execute("select count(*) as n from technologies_without_source").fetchone()["n"] == 0
