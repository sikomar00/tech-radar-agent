"""URL 수동 등록(S3-06): 페이지 제목으로 manual 항목을 만들고, 분석은 후보로 다룬다."""
import argparse

import pytest

from radar.adapters.page import Page, PageError
from radar.analyze import AnalyzeFailed
from radar.cli import _add
from radar.manual import add_urls
from tests.conftest import Clock, FakeLlm
from tests.test_analyze import classify, run, technologies


def _page(url: str) -> Page:
    return Page(url, "LangChain", "A framework for agents. " * 30, "body text that is never stored in full")


def _items(db) -> list[dict]:
    return db.execute("select i.id, i.title, i.excerpt, i.metadata, s.kind from source_items i "
                      "join sources s on s.id = i.source_id order by i.id").fetchall()


def test_a_url_becomes_a_manual_item_with_the_page_title_and_a_short_excerpt(db) -> None:
    lines = add_urls(db, ["https://www.langchain.com/"], Clock(), read=_page)
    item = _items(db)[0]
    assert lines == ["등록함: https://www.langchain.com/"]
    assert (item["title"], item["kind"], item["metadata"]["kind"]) == ("LangChain", "manual", "manual")
    assert len(item["excerpt"]) <= 280 and "never stored" not in item["excerpt"]


def test_an_unreadable_page_is_still_registered_by_its_url(db) -> None:
    def fail(url: str) -> Page:
        raise PageError("robots", "robots.txt가 막는다")

    lines = add_urls(db, ["https://blocked.example/tool"], Clock(), read=fail)
    assert lines == ["등록함: https://blocked.example/tool (페이지를 읽지 못함: robots)"]
    assert _items(db)[0]["title"] == "https://blocked.example/tool"


def test_known_urls_other_schemes_blanks_and_comments_are_not_added_twice(db) -> None:
    add_urls(db, ["https://ollama.com/"], Clock(), read=_page)
    lines = add_urls(db, ["https://OLLAMA.com", "file:///etc/passwd", "", "# 메모", "javascript:alert(1)"], Clock(), read=_page)
    assert lines[0].startswith("이미 있음(항목 #") and lines[1].startswith("건너뜀") and lines[2].startswith("건너뜀")
    assert len(lines) == 3 and len(_items(db)) == 1


def test_a_manual_item_is_analyzed_as_a_candidate(db) -> None:
    add_urls(db, ["https://ollama.com/"], Clock(), read=_page)
    llm = FakeLlm(classify("ENTITY", "Ollama"))
    run(db, llm, _items(db)[0]["id"])
    assert technologies(db) == [("Ollama", "PRODUCT")]
    assert '"source": "manual"' in llm.calls[0][1]


def test_a_manual_item_the_model_calls_noise_fails_visibly_instead_of_vanishing(db) -> None:
    add_urls(db, ["https://ollama.com/"], Clock(), read=_page)
    with pytest.raises(AnalyzeFailed) as caught:
        run(db, FakeLlm(classify("NOISE", "")), _items(db)[0]["id"])
    assert not caught.value.retryable and technologies(db) == []


def test_the_cli_needs_exactly_one_of_url_or_file(db, capsys) -> None:
    assert _add(argparse.Namespace(url=None, file=None), db) == 2
    assert "하나만" in capsys.readouterr().out
