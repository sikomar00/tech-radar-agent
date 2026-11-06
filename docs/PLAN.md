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

---

# Plan v2.0 — 2단계(기술 식별) 구현

## Context
상태: **승인 2026-11-06, 착수 대기**(착수 조건 미충족).
SPEC v1.2 2단계 상세는 승인됐고(2026-11-05), ADR-0004(Gemini 무료 API)는 초안이다. 1단계 코드는 완료됐고 24시간 게이트 측정이 2026-11-06 19:35 KST까지 돈다. 이 Plan은 2단계를 게이트까지 구현하는 순서다.

**착수 조건 (둘 다 충족하기 전에는 코드를 쓰지 않는다 — 불변 규칙):** ① 1단계 게이트 보고(T1-7) 승인 ② ADR-0003·0004를 accepted로. 이 Plan을 승인해도 착수 조건은 그대로다.
이 Plan 승인은 아래 라이브러리 목록과 SPEC 보완 2건(맨 아래)을 승인한 것으로 본다. 범위 안에서는 끊지 않고 진행하고, 범위 밖(삭제·비용·목록 밖 라이브러리)은 멈춰서 묻는다. 커밋·푸시는 상시 요청(기능별)이라 각 묶음 끝에 코드 리뷰 후 성격별로 한다.

## 구조 (기존 계층을 그대로 따른다: domain ← adapters·store ← collect·worker·cli)
```
radar/domain/analysis.py     (신규) 분류 스키마·프롬프트 버전, 입력 해시, 사전 필터, evidence_type 매핑, 정규명 정리. 표준 라이브러리만
radar/llm/__init__.py        (신규) LlmClient Protocol: complete_json(지시, 데이터, 스키마) -> 결과·토큰 수, LlmError(kind, retryable), jsonschema 재검증
radar/llm/gemini.py          (신규) urllib REST. 키는 헤더(URL·오류 문구에 안 남는다), tools 없음, 호출 간격 6초(분당 10회 이하)
radar/store/analysis.py      (신규) analysis_runs 기록, is_current 교체(한 트랜잭션), 해시 재사용 조회, 오늘(태평양 시간) 호출 수
radar/store/technologies.py  (신규) 기술·별칭·출처 연결, 규칙·퍼지(pg_trgm) 후보 조회, 소문자 정규명 충돌 시 기존 기술로 연결
radar/analyze.py             (신규) run_analyze_job — collect.py와 같은 자리·모양. classify → match → 연결
radar/holds.py, radar/eval.py (신규) 보류 큐 명령, 골든셋 평가       migrations/0002_stage2.sql   tests/…
```
- 기존 파일 수정: `radar/worker.py`(잡 종류별 처리기 표, 실패 예외 공통화), `radar/store/jobs.py`(`schedule_analysis`, `claim_next`가 수집 잡을 먼저 집고 한도 도달 시 분석 잡을 제외), `radar/cli.py`(holds, eval, `jobs retry --kind`), `radar/gate.py`(2단계 점검), `radar/store/db.py`(임시 스키마에서 `pg_trgm`이 보이게 search_path에 `extensions` 추가).
- 외부 호출은 `radar/llm/`에서만, DB는 `radar/store/`에서만. 파일 300줄·함수 30줄 이하, 공개 함수 타입 힌트(`rules/code.md`).

## 라이브러리 (승인됨, 하위 의존성 포함)
`jsonschema==4.26.0`과 그 하위 의존성(attrs, jsonschema-specifications, referencing, rpds-py)을 설치 결과의 정확한 버전으로 `requirements.txt`에 고정한다(T2-0에서 버전 보고). 나머지는 표준 라이브러리다.

## 작업 순서 (PRD S2-01~07·게이트와 대응)
- **T2-0 준비**: `requirements.txt`, `0002_stage2.sql`(v1.1.sql에서 enum 4개 `technology_type`·`maturity`·`lifecycle_status`·`confidence`, `pg_trgm`, 4개 테이블, 인덱스, 뷰 `pending_classification`·`technologies_without_source`, RLS), 임시 스키마에서 `gin_trgm_ops`·`similarity()` 동작 확인. 통과하면 운영 DB에 적용하고 `schema_migrations`로 확인. 먼저 돌던 워커를 멈추고 새 코드로 다시 띄운다(GEMINI_API_KEY가 없으면 수집만 한다).
- **T2-1 도메인**(네트워크·DB 없음, S2-01·S2-02·S2-06): 입력 정제(제목·발췌 280자·메타데이터를 구분자 데이터 구간에), 분류·매칭 스키마(평평, 모든 필드 required), 입력 해시, 사전 필터(처음엔 전부 통과), evidence_type 매핑. 지시문 fixture `tests/fixtures/injection/` 작성.
- **T2-2 LLM 어댑터**(S2-06): Gemini REST 호출, 실패 분류(429·503·시간 초과·네트워크는 재시도 가능, 400·403·404는 불가), 응답을 jsonschema로 재검증(어기면 재시도 가능 실패), 가짜 클라이언트 `FakeLlm`. 실제 호출 1~2번으로 응답 모양만 확인해 줄인 fixture로 남긴다(키 값은 어디에도 남기지 않는다).
- **T2-3 저장**(S2-03·S2-04·S2-05): `analysis_runs`(실패도 기록, `is_current` 교체, 같은 해시는 재호출 없음), 기술 만들기·연결·별칭(중복 없음), 퍼지 후보 상위 5개. DB 시험 포함.
- **T2-4 분석 잡과 워커**(S2-03·S2-04): 사전 필터를 통과하고 분석 잡이 한 번도 없었던 항목마다 `analyze_item` 잡 생성(실패한 잡을 매 주기 다시 만들어 한도를 태우지 않는다). 하루 375회 도달 시 분석 잡만 집지 않는다(잡은 queued로 남아 다음 날 이어서, 시도 횟수 소모 없음). `radar holds list|set|link|new`, `radar jobs retry --kind`.
- **T2-5 평가**(S2-07): `radar eval`(기본은 튜닝용만, 보류용은 `--holdout`을 줘야 하고 게이트 판정용이라고 경고 출력). 골든셋에 없는 소스 메타데이터는 운영 DB에서 URL로 찾아 붙인다(없으면 비우고 건수를 보고). LLM 응답 파일 캐시로 같은 해시는 재호출하지 않는다(임시 스키마는 지워져서 DB 해시 재사용이 안 되기 때문). **선행: 사용자의 골든셋 검토(`reviewed=Y`)**. 평가 중 운영 워커는 멈추고, 하루 1회까지만 돌린다.
- **T2-6 게이트 측정과 QA**: 튜닝용으로 프롬프트·규칙을 고친 뒤 보류용을 한 번 잰다. 지시문 fixture로 실제 모델의 스키마 위반 수와 `tools` 미전송을 확인한다. `gate-check`에 2단계 항목(실패 기록의 재시도 여부, 출처 없는 기술 0건, 보류 건수, 오늘 호출 수) 추가. `docs/qa-guide.md`에 Claude QA와 사용자 QA(보류 큐 몇 건 눈으로 확인, 골든셋 검토) 기록.
- **T2-7 게이트 보고**: 후보 P·R, 유형 정확도, 매칭 Precision, 중복 기술 생성률(5% 이하), 재시도 가능 기록, 지시문 시험을 숫자로 보고하고 승인을 받는다. 통과하면 3단계(SPEC 3단계 상세·웹 기술 결정·IA/UJ 인터뷰가 먼저).

## 위험과 한계
- **골든셋이 얇다**: 보류용 44건, 같은 기술 묶음 5개뿐이라 매칭 Precision·중복률은 오차가 크다(PRD: 표본 60건 안팎이면 ±10%p). 24시간 데이터로 보강하고 사용자가 다시 검토해야 한다. 사용자의 검토가 늦으면 T2-5부터 멈춘다.
- **모델 품질을 모른다**: Flash Lite 계열의 분류·매칭 품질은 측정 전까지 모른다. 미달이면 프롬프트를 튜닝용으로 고치고, 그래도 안 되면 모델 교체(ADR-0004 대안)를 다시 묻는다.
- **무료 한도는 보장되지 않는다**: 하루 500회는 콘솔 표시일 뿐이고 503이 잦다. 새 항목은 하루 약 160건(12시간 측정 79건 기준 추정)이라 분류만 약 160회, 후보의 매칭 호출이 더해진다. 처음 쌓인 245건은 이틀쯤 걸린다. 한도 확인은 잡 단위라 하루 375회를 최대 1회 넘길 수 있다(75% 여유로 흡수).
- **Gemini 장애가 길면** 잡이 3번 시도 뒤 `failed`가 된다. 그래서 `jobs retry --kind`로 한꺼번에 되살린다.
- 프롬프트 버전을 올려도 이미 분류된 항목은 자동으로 다시 분석하지 않는다(필요해지면 명령을 추가).
- 무료 서비스는 입력을 Google이 쓰고 사람이 읽을 수 있다. 공개된 제목과 280자 발췌만 보낸다.
- 확인하지 못한 것: 운영 DB에 `pg_trgm`이 이미 있는지(T2-0에서 확인), Gemini 무료 한도가 시험 이후에도 같은지.

## 검증
- `python -m pytest` 통과 수를 변경 전후로 보고한다(현재 96개, DB 포함). 시험 항목: 스키마 위반 출력, 후보에 없는 technology_id, 429·503·시간 초과의 재시도 가능 기록, 한도 도달 시 분석 잡 제외, 같은 입력 재처리 시 재호출 없음, `is_current` 교체, 소문자 정규명 충돌, 요청 본문에 `tools` 없음, 실패 잡 재생성 없음.
- 변경 전후 점검: 수집 잡이 분석 잡 뒤에서 굶지 않는지(큐에 분석 잡 수백 개를 넣고 수집 잡이 먼저 집히는지), `gate-check`(1단계)가 여전히 통과하는지.
- 실제 모델 점검: 항목 3~5건 분석 후 `analysis_runs`·`technologies`·`technology_sources`를 눈으로 확인하고, `technologies_without_source`가 0건인지 본다.

## SPEC 보완 2건 (승인 시 함께 확인)
1. `jobs retry --kind <종류>`: 실패한 잡을 종류별로 한꺼번에 재실행한다(SPEC은 잡 하나씩만).
2. 평가의 입력 해시 재사용은 임시 스키마가 아니라 파일 캐시로 한다(SPEC 문장 "해시가 같으면 다시 호출하지 않는다"의 의도를 지키는 구현 방식).
