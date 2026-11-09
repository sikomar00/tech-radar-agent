"""리스팅 이유(2026-11-09 결정): 규칙 근거와 AI 이유 한 줄."""
import pytest

from radar.domain.listing import ai_reason, rule_basis
from radar.domain.prompts import REASON_LIMIT
from radar.store.technologies import TechnologyStore
from tests.conftest import FakeLlm, add_item
from tests.test_analyze import classify, run


@pytest.mark.parametrize(("basis", "expected"), [
    ({"kind": "github", "class": "ENTITY", "stars": 543}, "새 기술 발표 · GitHub ★543"),
    ({"kind": "github", "class": "UPDATE", "tag": "v16.5.0"}, "업데이트 · 릴리스 v16.5.0"),
    ({"kind": "hackernews", "class": "ENTITY", "score": 83}, "새 기술 발표 · HN 83점"),
    ({"kind": "rss", "class": "UPDATE", "source": "rss-cloudflare"}, "업데이트 · RSS rss-cloudflare"),
    ({"kind": "manual", "class": "ENTITY"}, "직접 등록"),
    ({"kind": "hackernews"}, "분류 결과 없음 · HN"),
    (None, "분류 결과 없음"),
])
def test_rule_basis_names_the_class_and_the_source_signal(basis, expected) -> None:
    assert rule_basis(basis) == expected


def test_ai_reason_is_cleaned_clipped_and_empty_for_old_results() -> None:
    assert ai_reason({"reason": "개발자용 SDK\x07 발표"}) == "개발자용 SDK 발표"
    assert len(ai_reason({"reason": "가" * 500})) == REASON_LIMIT
    assert ai_reason({}) == "" and ai_reason({"reason": None}) == "" and ai_reason(None) == ""


@pytest.mark.parametrize("garbled", ["⌣⌣⌣⌣⌣", "소흐트애여 기슈ᅧᆫ대녀", "\x80\x99\x84 \x94\x80"])  # 운영에서 나온 깨진 이유
def test_ai_reason_hides_garbled_model_text(garbled) -> None:
    assert ai_reason({"reason": garbled}) == ""
    assert ai_reason({"reason": "Next.js 프레임워크의 v16.5 “canary” 릴리스입니다."}) != ""


def test_the_model_reason_is_stored_with_the_classification(db) -> None:
    item = add_item(db)
    answer = {**classify("ENTITY", "Ollama"), "reason": "로컬에서 LLM을 돌리는 개발자 도구 발표"}
    run(db, FakeLlm(answer), item)
    stored = db.execute("select result ->> 'reason' as r from analysis_runs where kind = 'classify'").fetchone()["r"]
    assert stored == "로컬에서 LLM을 돌리는 개발자 도구 발표"
    assert TechnologyStore(db).definite_match("Ollama", None)


def test_description_prefers_the_profile_line_then_the_source_excerpt() -> None:
    from radar.domain.listing import DESCRIPTION_LIMIT, description
    assert description("로컬 LLM 실행 도구", "Run LLMs locally") == ("로컬 LLM 실행 도구", "AI 요약")
    assert description(None, "Run LLMs locally") == ("Run LLMs locally", "출처 발췌")
    assert description("  ", "") == ("", "")
    text, _ = description(None, "a" * 500)
    assert len(text) == DESCRIPTION_LIMIT + 1 and text.endswith("…")
