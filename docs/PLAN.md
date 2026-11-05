# Plan v1.0 — 1단계(수집 엔진) 구현, 2~3단계는 마일스톤

## Context
PRD v1.1(Approved), ADR-0001·0002·0005, ERD v1.1(DDL 40개 점검 통과), SPEC v1.0이 준비됐다. 코드는 아직 없다. 이 Plan은 1단계 "수집 엔진"을 게이트까지 구현하는 순서를 정한다. 이 Plan을 승인하면 **SPEC v1.0, 아래 라이브러리 목록, 사용자 작업 1~3도 함께 승인한 것으로 본다.** 승인 범위 안에서는 끊지 않고 진행하고, 범위 밖(커밋·푸시·삭제·비용·새 라이브러리)은 멈춰서 묻는다.

## 구조
Python 3.11, 의존성은 안쪽으로만 향한다. 근거: Clean Architecture의 의존성 규칙(소스 의존성은 안쪽 업무 규칙을 향한다). 2차 자료(블로그)로 확인했고 Martin의 원문은 확인하지 못했다.
```
radar/domain/     정제, URL 정규화, 해시, 모델. 표준 라이브러리만 쓴다
radar/adapters/   github, hackernews, rss, http. domain에만 의존한다
radar/store/      PostgreSQL 접근(항목 upsert, 잡, 실행 기록). domain에 의존한다
radar/worker.py   스케줄러와 워커 루프      radar/cli.py   migrate, runs list, jobs retry, gate-check
migrations/       0001_stage1.sql (v1.1.sql 중 1단계 부분)     tests/   fixtures/ 와 test_*.py
```
- DB는 `radar/store/` 한 곳에서만 만진다. 외부 API는 `radar/adapters/`에서만 부른다.
- 스키마 적용은 `radar migrate`(적용 이력 테이블 + SQL 파일 순서 실행, 약 25줄). 테스트는 임시 스키마를 만들어 쓰고 지운다.
- 규칙: 파일 300줄 이하, 함수 30줄 이하, 공개 함수에 타입 힌트(`rules/code.md`).

## 라이브러리 목록 (정확한 버전은 2026-11-05에 PyPI에서 조회)
| 이름 | 버전 | 이유 |
| --- | --- | --- |
| `psycopg[binary]` (psycopg-binary 포함) | 3.3.8 | PostgreSQL 연결. 표준 라이브러리에는 드라이버가 없다 |
| `feedparser` | 6.0.14 | RSS·Atom 파싱. 형식이 제각각이고 깨진 XML도 많아서 직접 만들지 않는다. 스파이크의 `xml.etree` 방식을 대체한다 |
| `pytest` | 9.2.0 | 테스트 |
- 나머지(HTTP, 설정, 스케줄, 잡 큐, URL 정규화)는 표준 라이브러리로 한다. 2단계의 LLM SDK와 스키마 검증 라이브러리는 ADR-0004(공급자 결정)와 함께 2단계 시작 전에 한 번 묶어서 묻는다.

## 사용자 작업 (한 번에 알려 드린다)
1. Supabase 대시보드에서 DB 연결 문자열을 복사해 `.env`의 `DATABASE_URL`에 직접 넣는다. 채팅에는 붙이지 않는다. (T1-3부터 필요)
2. (선택) GitHub 토큰이 필요해지면 `.env`의 `GITHUB_TOKEN`. 기본은 비인증이다.
3. 게이트 측정 때 워커를 24시간 돌리므로 그동안 PC를 켜 둔다. 꺼지면 이어서 실행하고 측정 시간만 늘린다.

## 1단계 작업 순서 (PRD S1-01~07, 게이트와 대응)
- **T1-0 준비**: ADR-0003(수집 전략: 공식 API만 사용, 요청 단위 재시도 없음, HN 미러 제외, GitHub 한도 처리)을 SPEC에서 옮겨 쓴다. `requirements.txt`(정확한 버전 고정), `.env` 로더, `radar migrate`, `0001_stage1.sql`.
- **T1-1 도메인**(DB 없이): 정제, `normalize_url`, `content_hash`와 단위 테스트. SPEC의 정규화 규칙을 그대로 구현한다.
- **T1-2 어댑터**: HN → GitHub → RSS 순서로 하나씩, 실제 응답을 줄여 저장한 fixture와 함께 붙인다(PRD의 "한 번에 끝내지 않는다"). 실패 원인 분류(`http_status`, `timeout`, `network`, `parse`, `rate_limited`)를 포함한다.
- **T1-3 저장**: 3개 키 순서 조회 후 upsert, 잡 가져오기(`FOR UPDATE SKIP LOCKED`)·재시도·잠금 복구, 실행 기록. DB 테스트 포함.
- **T1-4 워커와 CLI**: 스케줄러와 워커 루프, `radar runs list`, `radar jobs retry`, `radar migrate`, 소스 시드.
- **T1-5 게이트 측정**: `radar gate-check`가 소스별 성공률, 재수집 후 새 행 수, 실패 원인 목록을 출력한다. 측정 때는 모든 소스를 1시간 주기로 낮춰 24시간 실행한다(GitHub는 시간당 약 6요청이라 비인증 한도 60 안이다).
- **T1-6 QA**: `rules/qa.md`에 따라 `docs/qa-guide.md`에 Claude QA(기대값, 실제값, Pass/Fail, 날짜)와 사용자 QA 목록(실제 항목 10개를 원본 URL과 대조 등 3개 안팎)을 쓴다.
- **T1-7 게이트 보고**: 숫자로 보고하고 ADR-0003과 함께 승인을 한 번에 받는다. 통과하면 2단계로 간다.
- 각 묶음이 끝날 때마다 `code-review`를 돌리고 통과 수를 숫자로 보고한다. 커밋은 요청하실 때만 하고, 단계 끝에 성격별(feat, test, docs, chore) 묶음을 제안한다.

## 2단계·3단계 마일스톤 (시작 전에 SPEC 보강, 질문은 단계당 한 번)
- **2단계 시작 전**: ADR-0004(LLM 공급자, AI 보안), 비용 상한, 키 설정, 라이브러리, 골든셋 50~100건 라벨링(제가 후보 목록과 초안 라벨을 만들고 사용자가 고친다. 사람 판단 작업이라 대신하지 않는다).
- **2단계**: 사전 필터 → 분류·매칭 → 인젝션 fixture → 보류용 골든셋으로 게이트 측정.
- **3단계 시작 전**: 웹 화면 기술 결정, IA와 User Journey 인터뷰, SPEC 3단계 상세. `sources.kind`에 `manual` 추가 마이그레이션.

## 위험
- Supabase 무료 프로젝트는 일정 기간 활동이 없으면 일시정지된다(기존 프로젝트 2개가 정지 상태인 것으로 확인). 워커가 돌고 있는 동안은 문제가 없을 것이다. 게이트 이후 장기 운영 방식은 확인하지 못했다.
- Windows에서 Supabase 직접 연결이 IPv6라면 연결이 안 될 수 있다. 그 경우 대시보드의 세션 풀러 연결 문자열을 쓴다(미확인, T1-3에서 확인).
- GitHub 소스의 요청 수가 적어서 24시간 측정에서 1건 실패가 성공률에 크게 반영된다. 표본이 작으면 측정 시간을 늘려 다시 잰다.

## 검증
- `python -m pytest`의 통과 수를 변경 전후로 보고한다. 같은 입력을 두 번 수집했을 때 새 행이 0개여야 한다.
- `radar migrate`가 빈 스키마에서 끝까지 적용되는지, `radar gate-check`가 게이트 4개 항목(소스 3개 수집과 성공률 95% 이상, 재수집 중복 0, 실패 원인 로그, 어댑터별 fixture 테스트)을 숫자로 출력하는지 확인한다.
- 게이트 후 `docs/qa-guide.md`의 Claude QA 결과와 사용자 QA 목록 개수를 채팅 하단에 보고한다.
