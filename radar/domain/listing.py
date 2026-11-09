"""Feed·검색의 '리스팅 이유'(2026-11-09 결정). 규칙 근거는 분류 결과와 출처 신호로 만들고, AI 이유는 분류 호출이 쓴 한 줄이다."""
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


def ai_reason(basis: dict[str, Any] | None) -> str:
    """모델이 쓴 이유 한 줄. 이 칸이 생기기 전에 분류된 글은 빈 문자열이다. 화면은 자동 이스케이프로 보여 준다."""
    reason = (basis or {}).get("reason")
    return strip_control(reason).strip()[:REASON_LIMIT] if isinstance(reason, str) else ""
