from datetime import datetime, timedelta, timezone

from radar.domain.models import RawItem
from radar.store.db import apply_migrations
from radar.store.items import ItemStore
from tests.conftest import Clock

T0 = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def _source(db, name: str = "s1", kind: str = "rss") -> int:
    return db.execute("insert into sources (name, kind) values (%s, %s) returning id", (name, kind)).fetchone()["id"]


def _item(url: str = "https://a.test/1", title: str = "Title", excerpt: str = "An excerpt", external_id: str | None = "e1",
          metadata: dict | None = None, published_at: datetime | None = None) -> RawItem:
    return RawItem(external_id, url, title, excerpt, published_at, metadata or {})


def _save(db, source_id: int, items: list[RawItem], clock: Clock | None = None):
    return ItemStore(db, clock or Clock(T0)).save(source_id, items)


def _count(db) -> int:
    return db.execute("select count(*) as n from source_items").fetchone()["n"]


def _titles(db) -> list[str]:
    return [r["title"] for r in db.execute("select title from source_items order by id").fetchall()]


def test_migrations_apply_once(db) -> None:
    assert apply_migrations(db) == []


def test_new_item_is_inserted_with_discovery_time(db) -> None:
    stats = _save(db, _source(db), [_item()])
    assert (stats.inserted, stats.updated) == (1, 0)
    row = db.execute("select * from source_items").fetchone()
    assert row["normalized_url"] == "a.test/1" and row["first_discovered_at"] == T0 and row["collected_at"] == T0


def test_recollecting_same_items_updates_without_new_rows(db) -> None:
    sid = _source(db)
    items = [_item(), _item("https://a.test/2", "Two", "x", "e2")]
    _save(db, sid, items)
    stats = _save(db, sid, items, Clock(T0 + timedelta(hours=1)))
    assert (stats.inserted, stats.updated) == (0, 2) and _count(db) == 2
    row = db.execute("select first_discovered_at, collected_at from source_items where external_id = 'e1'").fetchone()
    assert row["first_discovered_at"] == T0 and row["collected_at"] == T0 + timedelta(hours=1)


def test_update_refreshes_title_and_excerpt(db) -> None:
    sid = _source(db)
    _save(db, sid, [_item()])
    _save(db, sid, [_item(title="New title", excerpt="New excerpt")])
    row = db.execute("select title, excerpt from source_items").fetchone()
    assert (row["title"], row["excerpt"]) == ("New title", "New excerpt")


def test_same_url_from_another_source_is_a_duplicate_and_leaves_the_row_untouched(db) -> None:
    a, b = _source(db, "a"), _source(db, "b", "github")
    _save(db, a, [_item("http://a.test/x/", "Original", "Original excerpt", None)])
    variant = _item("https://www.a.test/x?utm_source=rss", "Show HN: Other", "Other", "9", {"score": 5})
    stats = _save(db, b, [variant])
    assert (stats.inserted, stats.updated, stats.duplicates) == (0, 0, 1)
    row = db.execute("select title, source_id, metadata from source_items").fetchone()
    assert (row["title"], row["source_id"], row["metadata"]) == ("Original", a, {})


def test_two_sources_alternating_never_overwrite_each_other(db) -> None:
    a, b = _source(db, "a"), _source(db, "b")
    for _ in range(2):
        _save(db, a, [_item("https://x.test/p", "From A", "A", "a1")])
        _save(db, b, [_item("https://x.test/p", "From B", "B", "b1")])
    assert _titles(db) == ["From A"]


def test_distinct_entries_sharing_one_url_are_counted_as_duplicates_not_merged(db) -> None:
    sid = _source(db)
    stats = _save(db, sid, [_item("https://a.test/log", "Entry 1", "one", "g1"), _item("https://a.test/log", "Entry 2", "two", "g2")])
    assert (stats.inserted, stats.duplicates) == (1, 1)
    assert _titles(db) == ["Entry 1"]


def test_same_text_with_different_ids_in_one_source_is_a_duplicate(db) -> None:
    sid = _source(db)
    stats = _save(db, sid, [_item("https://a.test/1", external_id="g1"), _item("https://a.test/2", external_id="g2")])
    assert (stats.inserted, stats.duplicates) == (1, 1)


def test_same_content_without_ids_under_a_new_url_updates_the_same_item(db) -> None:
    sid = _source(db)
    _save(db, sid, [_item("https://a.test/1", external_id=None)])
    stats = _save(db, sid, [_item("https://mirror.test/copy", external_id=None)])
    assert (stats.inserted, stats.updated) == (0, 1) and _count(db) == 1


def test_items_without_external_id_and_different_urls_are_both_stored(db) -> None:
    sid = _source(db)
    stats = _save(db, sid, [_item("https://a.test/1", "One", "x", None), _item("https://a.test/2", "Two", "y", None)])
    assert stats.inserted == 2 and _count(db) == 2


def test_same_title_with_empty_excerpt_on_different_urls_stays_separate(db) -> None:
    sid = _source(db)
    stats = _save(db, sid, [_item("https://a.test/1", "Weekly", "", None), _item("https://a.test/2", "Weekly", "", None)])
    assert stats.inserted == 2


def test_empty_external_id_is_stored_as_null(db) -> None:
    _save(db, _source(db), [_item(external_id="")])
    assert db.execute("select external_id from source_items").fetchone()["external_id"] is None


def test_invalid_url_is_counted_and_not_stored(db) -> None:
    stats = _save(db, _source(db), [_item("https:///nohost"), _item("https://ok.test/1", external_id="e2")])
    assert (stats.invalid, stats.inserted) == (1, 1) and _count(db) == 1


def test_control_characters_in_url_and_id_are_removed_instead_of_aborting_the_batch(db) -> None:
    stats = _save(db, _source(db), [_item("https://a.test/\x001", external_id="e\x001"), _item("https://a.test/2", "Two", "x", "e2")])
    assert (stats.inserted, stats.invalid) == (2, 0)
    assert db.execute("select external_id from source_items order by id").fetchone()["external_id"] == "e1"


def test_star_snapshots_accumulate_across_collections(db) -> None:
    sid = _source(db, "gh", "github")
    for hours, stars in enumerate([100, 150, 400]):
        _save(db, sid, [_item(metadata={"stars": stars})], Clock(T0 + timedelta(hours=hours)))
    snapshots = db.execute("select metadata from source_items").fetchone()["metadata"]["stars_snapshots"]
    assert [s["stars"] for s in snapshots] == [100, 150, 400]


def test_published_at_is_kept_once_set(db) -> None:
    sid = _source(db)
    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    _save(db, sid, [_item(published_at=first)])
    _save(db, sid, [_item(published_at=datetime(2026, 11, 4, tzinfo=timezone.utc))])
    assert db.execute("select published_at from source_items").fetchone()["published_at"] == first
