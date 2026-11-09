"""관계 뽑기 규칙(SPEC v1.7 4단계). 모델에 넘길 후보를 고르고, 모델이 낸 관계를 저장 전에 검사한다.

모델 출력은 믿지 않는다: 후보 밖 id, 페이지에 실제로 없는 근거 문장은 버린다(지어낸 관계 차단).
"""
import re
from dataclasses import dataclass
from typing import Any, Iterable

from radar.domain.cleaning import strip_control

RELATION_TYPES = ("DERIVED_FROM", "BUILT_ON", "EXTENDS", "IMPLEMENTS", "PART_OF", "REPLACES",
                  "COMPETES_WITH", "INTEGRATES_WITH", "RELATED_TO")
SYMMETRIC = frozenset({"COMPETES_WITH", "INTEGRATES_WITH", "RELATED_TO"})  # S4-02: id가 작은 쪽이 주어
# 화면에 보이는 이름. 조사(은/는)가 이름마다 달라 문장 대신 "A → B · 기반"처럼 보인다
RELATION_LABELS = {
    "DERIVED_FROM": "파생", "BUILT_ON": "기반", "EXTENDS": "확장", "IMPLEMENTS": "구현", "PART_OF": "일부",
    "REPLACES": "대체", "COMPETES_WITH": "경쟁", "INTEGRATES_WITH": "연동", "RELATED_TO": "관련",
}
MAX_CANDIDATES = 20
MAX_RELATIONS = 10
QUOTE_LIMIT = 200
MIN_QUOTE = 20
SHORT_NAME = 3  # 이보다 짧은 이름(Go, R 등)은 대소문자까지 같아야 후보로 본다


@dataclass(frozen=True)
class Proposed:
    source_id: int
    target_id: int
    relation_type: str
    quote: str


def _mentions(name: str, text: str) -> int:
    flags = 0 if len(name) < SHORT_NAME else re.IGNORECASE
    # llama.cpp·open-webui·org/repo 안의 'llama'·'open'은 낱말이 아니다. 문장 끝 마침표는 경계로 본다
    return len(re.findall(rf"(?<![\w./-]){re.escape(name)}(?![\w/-]|\.\w)", text, flags))


def _squash(text: str) -> str:
    return " ".join(strip_control(text).split())


def find_candidates(text: str, known: Iterable[tuple[int, Iterable[str]]], self_id: int) -> dict[int, list[str]]:
    """페이지 본문에 정규명이나 별칭이 낱말로 나오는 다른 기술 {id: 이름들}. 많이 나온 순으로 최대 MAX_CANDIDATES개."""
    page, counts, names_of = _squash(text), [], {}
    for technology_id, names in known:
        if technology_id == self_id:
            continue
        names_of[technology_id] = [n for n in {n.strip() for n in names} if n]
        found = sum(_mentions(name, page) for name in names_of[technology_id])
        if found:
            counts.append((-found, technology_id))
    return {technology_id: names_of[technology_id] for _, technology_id in sorted(counts)[:MAX_CANDIDATES]}


def _check(item: dict[str, Any], candidates: dict[int, list[str]], page: str) -> tuple[int, str, bool, str] | None:
    """근거 문장은 MIN_QUOTE자 이상, 페이지에 그대로 있고, 상대 기술의 이름을 담아야 한다(아무 글자나 근거가 되지 않게)."""
    other, type_ = item.get("other_id"), item.get("relation_type")
    quote = _squash(str(item.get("quote", "")))[:QUOTE_LIMIT]
    if other not in candidates or type_ not in RELATION_TYPES or len(quote) < MIN_QUOTE or quote not in page:
        return None
    if not any(_mentions(name, quote) for name in candidates[other]):
        return None
    return other, type_, bool(item.get("this_is_subject")), quote


def parse_relations(raw: list[Any], self_id: int, candidates: dict[int, list[str]], page_text: str) -> tuple[list[Proposed], int]:
    """(통과한 관계, 버린 수). 근거 문장은 공백을 하나로 줄여 페이지 본문에 그대로 있어야 한다."""
    page, kept, seen = _squash(page_text), [], set()
    for item in raw[:MAX_RELATIONS] if isinstance(raw, list) else []:
        checked = _check(item, candidates, page) if isinstance(item, dict) else None
        if checked is None:
            continue
        other, type_, this_is_subject, quote = checked
        source, target = (self_id, other) if this_is_subject else (other, self_id)
        if type_ in SYMMETRIC and source > target:
            source, target = target, source
        if (source, target, type_) not in seen:
            seen.add((source, target, type_))
            kept.append(Proposed(source, target, type_, quote))
    total = len(raw) if isinstance(raw, list) else 0
    return kept, total - len(kept)
