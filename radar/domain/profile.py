"""프로필 주장(S3-01)의 칸과 해석 규칙. 모델이 채우는 7칸과, 읽은 페이지 URL로 규칙이 채우는 공식 출처 칸이 있다."""
from typing import Any

from radar.domain.cleaning import strip_control

# 칸마다 글자 수 상한. 스키마의 maxLength는 공급자가 무시할 수 있어(ADR-0004) 받은 뒤 자른다
FIELD_LIMITS = {
    "one_liner": 120, "description": 600, "problem": 300, "features": 600,
    "differences": 300, "use_cases": 300, "pros_cons": 400,
}
MODEL_FIELDS = tuple(FIELD_LIMITS)
OFFICIAL_SOURCE = "official_source"
ALL_FIELDS = (*MODEL_FIELDS, OFFICIAL_SOURCE)


def parse_profile(raw: dict[str, Any], page_read: bool) -> dict[str, str]:
    """스키마를 통과한 결과에서 비지 않은 칸만 남긴다. 빈 칸은 행을 만들지 않으므로 화면에서 "미확인"이다.

    페이지를 읽지 못했으면 제목만 보고 쓴 것이라 한 줄 설명만 받는다(나머지를 쓰면 지어낸 것이다).
    """
    fields = {}
    for name in MODEL_FIELDS if page_read else ("one_liner",):
        text = strip_control(str(raw.get(name, ""))).strip()[:FIELD_LIMITS[name]]
        if text:
            fields[name] = text
    return fields
