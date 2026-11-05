---
status: accepted
---
# 관계는 "주어가 source"로 통일하고 유형 조합 매트릭스로 검증한다

방향 규약은 PRD를 따른다. 주어가 source이고, REPLACES는 신형에서 구형으로 향하며, 대칭 관계는 id가 작은 쪽을 source로 한 줄만 저장한다. AI가 제안한 관계가 아래 매트릭스를 어기면 저장하지 않고 검토 큐로 보낸다.

| 관계 | source 유형 | target 유형 |
| --- | --- | --- |
| IMPLEMENTS | PRODUCT, FRAMEWORK_LIBRARY | PROTOCOL_STANDARD |
| EXTENDS | PRODUCT, FRAMEWORK_LIBRARY | PRODUCT, FRAMEWORK_LIBRARY |
| BUILT_ON | PRODUCT, FRAMEWORK_LIBRARY | FRAMEWORK_LIBRARY, MODEL |
| DERIVED_FROM, REPLACES | 같은 유형 | 같은 유형 |
| PART_OF | 같은 유형, 또는 FRAMEWORK_LIBRARY | 같은 유형, 또는 PRODUCT |
| COMPETES_WITH | 같은 유형, 또는 PRODUCT와 FRAMEWORK_LIBRARY 사이 | 위와 같음 |
| INTEGRATES_WITH, RELATED_TO | 4개 유형 모두 | 4개 유형 모두 |

PATTERN_CONCEPT와 RESEARCH 행은 4단계에서 두 유형을 열 때(S4-05) 추가한다. 근거가 약한 연관은 RELATED_TO로 두고 검증 대상으로 남긴다.

## Consequences
- 스파이크 표본에서 직접 본 사례는 스킬·플러그인 저장소에서 플랫폼으로 가는 EXTENDS 후보와 모노레포 패키지의 PART_OF 후보뿐이다. 나머지 칸은 PRD의 관계 정의에서 도출한 가설이다.
- 골든셋 관계 표본(4단계)으로 검증하고 개정한다.
