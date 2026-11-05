from datetime import datetime, timezone

import pytest

from radar.domain.cleaning import clean_text
from radar.domain.metadata import MAX_SNAPSHOTS, merge_metadata
from radar.domain.normalize import content_hash, normalize_url

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def test_clean_html_strips_tags_entities_and_whitespace() -> None:
    assert clean_text("<p>Hello&nbsp;&amp; <b>world</b></p>\n\n  ok", 100, is_html=True) == "Hello & world ok"


def test_clean_html_removes_escaped_tags() -> None:
    assert clean_text("a &lt;script&gt;alert(1)&lt;/script&gt; b", 100, is_html=True) == "a alert(1) b"


@pytest.mark.parametrize("plain", ["Rust: Vec<T> vs Box<dyn Trait>", "if a < b and c > d then", "A &amp; B stays literal"])
def test_clean_plain_text_is_not_treated_as_html(plain: str) -> None:
    assert clean_text(plain, 100) == plain


def test_clean_text_removes_control_characters_and_truncates() -> None:
    assert clean_text("ab\x00cd\x07ef", 100) == "abcdef"
    assert clean_text("abcdef", 3) == "abc"
    assert clean_text(None, 10) == ""


@pytest.mark.parametrize("raw, expected", [
    ("https://www.Example.com/Path/", "example.com/Path"),
    ("http://example.com/a#section", "example.com/a"),
    ("https://example.com:443/a", "example.com/a"),
    ("https://example.com:8080/a", "example.com:8080/a"),
    ("https://example.com/a?utm_source=x&b=2&a=1&fbclid=z", "example.com/a?a=1&b=2"),
    ("https://example.com/", "example.com"),
    ("example.com/a/", "example.com/a"),
    ("https://GitHub.com/LangChain-AI/LangChain/", "github.com/langchain-ai/langchain"),
])
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_normalize_url_unifies_scheme_and_tracking_variants() -> None:
    variants = ["http://a.test/x", "https://www.a.test/x/", "https://a.test/x?utm_medium=rss#top"]
    assert len({normalize_url(v) for v in variants}) == 1


@pytest.mark.parametrize("bad", ["", "   ", "https:///path"])
def test_normalize_url_rejects_missing_host(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(bad)


def test_content_hash_ignores_case_and_spacing() -> None:
    assert content_hash("Hello  World", "An  excerpt", "a.test/1") == content_hash("hello world", "an excerpt", "b.test/2")


def test_content_hash_with_empty_excerpt_depends_on_url() -> None:
    assert content_hash("Same title", "", "a.test/1") != content_hash("Same title", "", "a.test/2")
    assert content_hash("Same title", "", "a.test/1") == content_hash("Same title", "  ", "a.test/1")


def test_merge_metadata_appends_star_snapshots_and_caps_history() -> None:
    meta: dict = {}
    for stars in range(MAX_SNAPSHOTS + 3):
        meta = merge_metadata(meta, {"stars": stars, "language": "Rust"}, NOW)
    assert meta["language"] == "Rust"
    assert [s["stars"] for s in meta["stars_snapshots"]] == list(range(3, MAX_SNAPSHOTS + 3))


def test_merge_metadata_keeps_old_keys_and_skips_snapshots_without_stars() -> None:
    merged = merge_metadata({"score": 1, "kept": True}, {"score": 5}, NOW)
    assert merged == {"score": 5, "kept": True}
