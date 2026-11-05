---
status: accepted
---
# 기술은 6개 유형으로 제한하고, 저장소는 기술이 아닌 수집 항목으로 둔다

스파이크 표본(`spike/REPORT.md`)에서 저장소 하나가 기술 하나가 아니었다. langchain은 `langchain-core`, `langchain` 등 패키지별 태그로 릴리스한다. 그래서 저장소와 논문 원문은 수집 항목이고, 기술은 PRD의 6개 유형(PRODUCT, FRAMEWORK_LIBRARY, PROTOCOL_STANDARD, MODEL, PATTERN_CONCEPT, RESEARCH)으로 제한한다.

판정 규칙은 다음과 같다.
- **PRODUCT**: 사용자가 설치하거나 가입해서 직접 쓰는 것.
- **FRAMEWORK_LIBRARY**: 코드가 의존성으로 가져다 쓰거나 확장하는 것(SDK, 플러그인, 스킬 포함). Claude Code 스킬처럼 플랫폼을 확장하는 저장소는 FRAMEWORK_LIBRARY이고 대상 플랫폼과 EXTENDS로 잇는다.
- 두 규칙에 모두 해당하거나 판정하지 못하면 유형을 비우고 분류 보류로 둔다.
- 모노레포의 패키지별 릴리스는 한 기술의 release 이벤트로 합치고, `evidence_key`는 태그 전체로 한다. 패키지가 독립적으로 알려져 있을 때만 별도 기술로 만들고 PART_OF로 잇는다.

## Considered Options
- 저장소 하나를 기술 하나로 본다: langchain 사례에서 파편화가 생겨 버렸다.
- 유형을 처음부터 6개 모두 연다: PRD의 도입 순서(2단계 2개, 3단계 2개, 4단계 2개)를 유지한다. 표본에서 MODEL 후보(모델 가중치 공개)는 2단계에서 유형을 비워 분류 보류로 보낸다.

## Consequences
- 이 규칙은 표본 관찰에서 나온 잠정안이다. 골든셋 라벨링(사전 작업 4)에서 경계 사례가 나오면 개정한다.
- 유형 도입 순서(2단계 → 3단계 → 4단계)는 그대로 둔다.
- PATTERN_CONCEPT와 RESEARCH의 판정 규칙은 표본에 사례가 거의 없어 정하지 않았다. 4단계에서 두 유형을 열기 전(S4-05)에 정한다.
