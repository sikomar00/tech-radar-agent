"""골든셋 평가 지표(S2-07). 정답과 예측을 견주는 순수 계산이다."""
from dataclasses import dataclass

CANDIDATES = ("ENTITY", "UPDATE")


@dataclass(frozen=True)
class GoldRow:
    id: str
    split: str
    source: str
    title: str
    url: str
    excerpt: str
    label: str
    name: str
    type: str | None  # 비어 있으면 None(분류 보류가 정답)


@dataclass(frozen=True)
class Outcome:
    gold: GoldRow
    label: str | None  # 분석이 실패하면 None
    name: str = ""
    type: str | None = None
    technology_id: int | None = None  # 연결된 기술
    created: bool = False  # 이 항목이 그 기술을 만들었는가


@dataclass(frozen=True)
class Ratio:
    hit: int
    total: int

    def __str__(self) -> str:
        return f"{self.hit}/{self.total} ({100 * self.hit / self.total:.1f}%)" if self.total else f"{self.hit}/0 (표본 없음)"


@dataclass(frozen=True)
class Metrics:
    candidate_precision: Ratio
    candidate_recall: Ratio
    type_accuracy: Ratio
    match_precision: Ratio
    duplicate_rate: Ratio


def _is_candidate(label: str | None) -> bool:
    return label in CANDIDATES


def _same_name(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _gold_name_of_technology(outcomes: list[Outcome]) -> dict[int, str]:
    """기술마다 그 기술을 만든 항목의 정답 이름. 정답이 후보가 아닌 항목이 만든 기술은 이름이 없다."""
    return {o.technology_id: (o.gold.name if _is_candidate(o.gold.label) else "") for o in outcomes if o.created and o.technology_id}


def evaluate(outcomes: list[Outcome]) -> Metrics:
    predicted = [o for o in outcomes if _is_candidate(o.label)]
    true_positive = [o for o in predicted if _is_candidate(o.gold.label)]
    gold_candidates = [o for o in outcomes if _is_candidate(o.gold.label)]
    names = _gold_name_of_technology(outcomes)
    joined = [o for o in outcomes if o.technology_id and not o.created]
    created = list(names)
    distinct = {n.lower() for n in names.values() if n}
    return Metrics(
        candidate_precision=Ratio(len(true_positive), len(predicted)),
        candidate_recall=Ratio(len(true_positive), len(gold_candidates)),
        type_accuracy=Ratio(sum(1 for o in true_positive if o.type == o.gold.type), len(true_positive)),
        match_precision=Ratio(sum(1 for o in joined if _is_candidate(o.gold.label) and _same_name(o.gold.name, names.get(o.technology_id, ""))), len(joined)),
        duplicate_rate=Ratio(len(created) - len(distinct) - sum(1 for n in names.values() if not n), len(created)),
    )
