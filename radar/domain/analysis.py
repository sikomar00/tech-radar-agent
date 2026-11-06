"""분석 입력 만들기, 입력 해시, 모델 결과 해석. 표준 라이브러리만 쓰고 네트워크·DB는 만지지 않는다."""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from radar.domain.cleaning import EXCERPT_LIMIT, TITLE_LIMIT, clean_text
from radar.domain.prompts import MAX_ALIASES

CANDIDATE_CLASSES = ("ENTITY", "UPDATE")
NAME_LIMIT = 100
# 모델이 이름을 모를 때 채우는 말. 이런 이름의 기술이 생기면 같은 말을 쓰는 모든 항목이 거기에 규칙으로 합쳐진다
PLACEHOLDER_NAMES = {"none", "null", "n/a", "na", "unknown", "untitled", "-", "없음", "알 수 없음"}
MAX_TOPICS = 10
DATA_START, DATA_END = "<<<DATA", "DATA>>>"
# 모델에 보내는 메타데이터는 이 키뿐이다. stars_snapshots 같은 내부 값은 보내지 않는다
_SENT_METADATA = ("kind", "stars", "language", "topics", "fork", "archived", "repo", "tag", "prerelease", "score", "num_comments")
_RUN_OF_ANGLES = re.compile(r"<{2,}|>{2,}")


def _clip(value: Any) -> Any:
    return clean_text(value, NAME_LIMIT) if isinstance(value, str) else value


def classify_payload(source_kind: str, title: str, excerpt: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """분류 호출에 보낼 데이터. 제목과 발췌는 1단계 한도(300자, 280자)로, 메타데이터는 허용한 키만 자른다."""
    meta = {key: _clip(metadata[key]) for key in _SENT_METADATA if key in metadata}
    if isinstance(meta.get("topics"), list):
        meta["topics"] = [_clip(str(topic)) for topic in meta["topics"][:MAX_TOPICS]]
    return {"source": source_kind, "title": clean_text(title, TITLE_LIMIT), "excerpt": clean_text(excerpt, EXCERPT_LIMIT), "metadata": meta}


def render_data(payload: dict[str, Any]) -> str:
    """데이터를 구분자로 감싼 문자열로 만든다. 데이터 안의 `<<`·`>>`는 한 글자로 줄여서 구분자를 흉내 낼 수 없게 한다."""
    body = _RUN_OF_ANGLES.sub(lambda m: m.group(0)[0], json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return f"{DATA_START}\n{body}\n{DATA_END}"


def input_hash(model: str, instruction: str, schema: dict[str, Any], payload: dict[str, Any]) -> str:
    """모델, 지시문, 스키마, 입력 중 하나라도 바뀌면 달라지는 해시. 같은 해시의 성공 결과는 다시 호출하지 않는다."""
    parts = json.dumps([model, instruction, schema, payload], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def passes_prefilter(payload: dict[str, Any]) -> bool:
    # shortcut: 제외 규칙이 없다(SPEC 사전 필터). 일일 호출 상한에 걸리기 시작하면 골든셋 Recall을 지키는 규칙만 더한다
    return True


@dataclass(frozen=True)
class Classification:
    label: str
    name: str = ""
    type: str | None = None  # None이면 분류 보류
    aliases_en: tuple[str, ...] = ()
    aliases_ko: tuple[str, ...] = ()

    @property
    def is_candidate(self) -> bool:
        return self.label in CANDIDATE_CLASSES


def _clean_name(value: str) -> str:
    cleaned = clean_text(value, NAME_LIMIT + 1)
    if len(cleaned) > NAME_LIMIT:
        raise ValueError(f"이름이 {NAME_LIMIT}자를 넘는다")
    return "" if cleaned.lower() in PLACEHOLDER_NAMES else cleaned


def _aliases(values: list[str], canonical: str) -> tuple[str, ...]:
    seen, kept = {canonical.lower()}, []
    for value in values:
        alias = clean_text(value, NAME_LIMIT + 1)
        if alias and len(alias) <= NAME_LIMIT and alias.lower() not in seen:
            seen.add(alias.lower())
            kept.append(alias)
    return tuple(kept[:MAX_ALIASES])


def parse_classification(raw: dict[str, Any]) -> Classification:
    """스키마를 통과한 분류 결과를 해석한다. 후보가 아니면 이름·유형·별칭은 버린다(모델이 NOISE에도 이름을 채운다)."""
    if raw["class"] not in CANDIDATE_CLASSES:
        return Classification(raw["class"])
    name = _clean_name(raw["canonical_name"])
    if not name:
        raise ValueError("후보인데 canonical_name이 비어 있다")
    kind = None if raw["type"] == "NONE" else raw["type"]
    return Classification(raw["class"], name, kind, _aliases(raw["aliases_en"], name), _aliases(raw["aliases_ko"], name))


def match_payload(item: dict[str, Any], found: Classification, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """매칭 호출에 보낼 데이터. candidates는 {id, canonical_name, aliases}(퍼지 상위 5개)다."""
    named = {**item, "canonical_name": found.name, "aliases": [*found.aliases_en, *found.aliases_ko]}
    return {"item": named, "candidates": candidates}


@dataclass(frozen=True)
class MatchDecision:
    decision: str
    technology_id: int | None = None


def parse_match(raw: dict[str, Any], candidate_ids: set[int]) -> MatchDecision:
    """SAME의 technology_id가 후보 목록에 없으면 모델이 만들어 낸 값이므로 받아들이지 않는다."""
    if raw["decision"] != "SAME":
        return MatchDecision(raw["decision"])
    if raw["technology_id"] not in candidate_ids:
        raise ValueError(f"후보에 없는 technology_id {raw['technology_id']}")
    return MatchDecision("SAME", raw["technology_id"])


def repo_key(source_kind: str, normalized_url: str, metadata: dict[str, Any]) -> str | None:
    """GitHub 항목의 `owner/name`(소문자). 릴리스는 metadata.repo에, 저장소 항목은 정규화한 URL에 들어 있다."""
    if source_kind != "github":
        return None
    repo = metadata.get("repo") if metadata.get("kind") == "release" else None
    if not repo:
        host, _, path = normalized_url.partition("/")
        repo = "/".join(path.split("/")[:2]) if host == "github.com" else None
    return repo.lower() if repo and repo.count("/") == 1 else None


def evidence_type(source_kind: str, metadata: dict[str, Any], label: str) -> str:
    """기술과 수집 항목을 잇는 근거 종류. GitHub 릴리스 release, 저장소 official, ENTITY인 글 announcement, 그 밖은 mention."""
    if source_kind == "github":
        return "release" if metadata.get("kind") == "release" else "official"
    return "announcement" if label == "ENTITY" else "mention"
