# Tech Radar Agent

개인이 발견한 기술을 출처와 함께 시간이 지날수록 깊어지는 지식으로 쌓는 도구의 용어집이다.

## Language

### 수집

**출처 제공자 (Source Provider)**:
데이터를 제공하는 플랫폼이나 발행처(GitHub, Hacker News, RSS 발행처).
_Avoid_: 소스, 채널

**수집 항목 (Source Item)**:
출처 제공자에서 가져온 개별 콘텐츠(저장소, 게시글, 논문). 기술이 아니다.
_Avoid_: 기사, 문서, 데이터

**항목 분류 (Item Class)**:
수집 항목을 ENTITY, UPDATE, USAGE, NEWS, OPINION, NOISE 중 하나로 나눈 결과.
_Avoid_: 라벨, 카테고리

### 지식

**기술 (Technology)**:
지식으로 관리하는 대상이며 6개 유형 중 하나다. 저장소나 논문 원문은 기술이 아니다.
_Avoid_: 엔티티, 프로젝트, 항목

**기술 유형 (Technology Type)**:
PRODUCT, FRAMEWORK_LIBRARY, PROTOCOL_STANDARD, MODEL, PATTERN_CONCEPT, RESEARCH 여섯 가지. 기술 하나는 유형이 하나다.
_Avoid_: 카테고리, 종류

**분류 보류 (Pending Classification)**:
유형을 정하지 못한 기술이 사람의 판정을 기다리는 상태. 유형 값이 아니다.
_Avoid_: 미분류, 기타 유형

**정규명 (Canonical Name)**:
기술을 대표하는 하나의 이름. 원문(영문) 표기를 쓴다.
_Avoid_: 표준명, 공식명

**별칭 (Alias)**:
기술을 가리키는 다른 이름(한·영). 검색과 기존 기술 매칭에 쓴다.
_Avoid_: 동의어, 다른 이름

**근거 (Evidence)**:
주장이나 관계를 뒷받침하는 수집 항목의 특정 내용.
_Avoid_: 증거, 출처 링크

**주장 (Claim)**:
기술 프로필에 적는 개별 사실 하나. 신뢰도는 주장 단위로 붙는다.
_Avoid_: 정보, 속성

**신뢰도 (Confidence)**:
AI가 판단한 근거의 강도(Verified, Corroborated, Unverified, Disputed). 사용자 확인과는 별개다.
_Avoid_: 정확도, 점수

**사용자 확인 (User Verified)**:
사용자가 관계나 주장을 직접 확인했는지 여부. 신뢰도와는 별개다.
_Avoid_: 검증됨

**성숙도 (Maturity)**:
기술이 얼마나 자리 잡았는지(experimental, emerging, stable, mature). 정보의 정확성과는 무관하다.
_Avoid_: 완성도

**생애주기 (Lifecycle Status)**:
기술이 지금 유지되는 상태(active, maintenance, deprecated, end_of_life, unknown).
_Avoid_: 상태

**보관 (Archived)**:
시스템이 기술을 지식 목록에서 내린 처리. GitHub 저장소의 archived 상태와는 별개다.
_Avoid_: 삭제

**관계 (Relation)**:
두 기술 사이의 방향 있는 연결. 주어가 source다.
_Avoid_: 링크, 연결

**기술 이벤트 (Technology Event)**:
기술에 일어난 변화 하나를 시점과 함께 기록한 것(최초 공개, 릴리스, 인수 등). 고쳐 쓰지 않고 쌓는다.
_Avoid_: 업데이트, 변경 이력

**사용자 지식 (User Knowledge)**:
사용자별 상태(unread, saved, learned), 메모, 중요도. AI가 만든 지식과 분리한다.
_Avoid_: 북마크, 즐겨찾기

### 분석과 전달

**골든셋 (Golden Set)**:
사람이 정답을 붙인 평가용 표본. 튜닝용과 보류용으로 나눈다.
_Avoid_: 테스트셋, 정답지

**게이트 (Gate)**:
다음 단계로 넘어가려고 통과해야 하는 완료 기준.
_Avoid_: 체크포인트, 마일스톤

**변화 (Change)**:
브리핑에 싣는 단위로, 기술 이벤트 또는 수집 항목 하나. 중복 판정은 기술이 아니라 변화 단위로 한다.
_Avoid_: 업데이트, 소식

**브리핑 (Briefing)**:
일간 또는 주간으로 자동 생성하는 변화 요약.
_Avoid_: 리포트, 뉴스레터

**관찰 목록 (Watchlist)**:
근거가 있는 변화만 올리는 주목 목록. 미래를 예측하지 않는다.
_Avoid_: 예측 목록, 추천
