"""Feed·검색의 '리스팅 이유'(2026-11-09 결정). 규칙 근거는 분류 결과와 출처 신호로 만들고, AI 이유는 분류 호출이 쓴 한 줄이다."""
import re
from typing import Any

from radar.domain.cleaning import strip_control
from radar.domain.prompts import REASON_LIMIT

CLASS_LABELS = {"ENTITY": "새 기술 발표", "UPDATE": "업데이트"}


def _signal(basis: dict[str, Any]) -> str:
    kind = basis.get("kind")
    if kind == "github":
        if basis.get("tag"):
            return f"릴리스 {basis['tag']}"
        return f"GitHub ★{basis['stars']}" if basis.get("stars") is not None else "GitHub"
    if kind == "hackernews":
        return f"HN {basis['score']}점" if basis.get("score") is not None else "HN"
    if kind == "rss":
        return f"RSS {basis.get('source') or ''}".strip()
    return ""


def rule_basis(basis: dict[str, Any] | None) -> str:
    """가장 최근 출처 하나로 만든 규칙 근거. 예: '새 기술 발표 · GitHub ★543', '업데이트 · 릴리스 v1.2'."""
    basis = basis or {}
    label = "직접 등록" if basis.get("kind") == "manual" else CLASS_LABELS.get(str(basis.get("class") or ""), "분류 결과 없음")
    return " · ".join(part for part in (label, _signal(basis)) if part)


DESCRIPTION_LIMIT = 140


def description(one_liner: str | None, excerpt: str | None) -> tuple[str, str]:
    """기술이 대략 무엇인지와 그 출처 표시. 프로필 한 줄 설명(AI 요약)이 있으면 그것, 없으면 출처 발췌(원문 그대로)."""
    for text, label in ((one_liner, "AI 요약"), (excerpt, "출처 발췌")):
        cleaned = strip_control(text or "").strip()
        if cleaned:
            return (cleaned[:DESCRIPTION_LIMIT] + ("…" if len(cleaned) > DESCRIPTION_LIMIT else "")), label
    return "", ""


# 완성형 한글, ASCII, 흔한 문장부호만 정상으로 본다. 운영에서 flash-lite가 낸 깨진 이유(낱자모·기호 반복·깨진 바이트)를 걸러 낸다.
_READABLE = re.compile(r"[가-힣\x20-\x7e·…‘’“”–—]*")


def ai_reason(basis: dict[str, Any] | None) -> str:
    """모델이 쓴 이유 한 줄. 이 칸이 생기기 전에 분류된 글이나 글자가 깨진 이유는 빈 문자열이다. 화면은 자동 이스케이프로 보여 준다.

    shortcut: 읽을 수 있는 글자로만 된 헛소리(예: '슈시미나 녀스로')는 못 거른다, 비율이 커지면 모델이나 언어를 바꾼다.
    """
    reason = (basis or {}).get("reason")
    if not isinstance(reason, str):
        return ""
    cleaned = strip_control(reason).strip()
    return cleaned[:REASON_LIMIT] if _READABLE.fullmatch(cleaned) else ""
