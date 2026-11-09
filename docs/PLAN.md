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
상태: **승인 2026-11-06, 착수 2026-11-06**(1단계 게이트 통과 승인, ADR-0003·0004 accepted).
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

---

# Plan v3.0 — 3단계(지식 데이터베이스) 구현 (승인 2026-11-08, 착수 2026-11-08)

## Context
2단계 게이트는 통과했다(2026-11-08). 운영 분석도 돌고 있어서, 기술이 중복 없이 쌓이기 시작했다. 3단계는 이 기술들을 출처와 함께 다시 꺼내 볼 수 있는 지식으로 만든다(PRD 3단계).
- 선행 문서는 모두 승인됐다: IA v1.0, User Journey v1.0, SPEC v1.5 3단계 절, ADR-0006(웹 기술), ADR-0007(프로필 출처).
- 이 Plan은 3단계를 게이트까지 구현하는 순서다.
- 이 Plan을 승인하면 아래 라이브러리 목록도 함께 승인한 것으로 본다. 목록에는 ADR-0006을 FastAPI에서 Starlette로 좁히는 개정이 들어 있다.
- 범위 안에서는 끊지 않고 진행한다. 커밋·푸시는 상시 요청이므로 묶음마다 코드 리뷰 뒤 성격별로 한다.
- 범위 밖(삭제, 비용, 목록 밖 라이브러리, 운영 데이터 복원)은 멈춰서 묻는다.

## 라이브러리 (승인 대상, 2026-11-08 pip 해석 결과로 고정)
| 이름 | 버전 | 이유 |
| --- | --- | --- |
| starlette | 1.10.0 | 웹 라우팅·요청·응답. FastAPI의 기반이다. 서버 렌더링 폼에는 FastAPI의 API 검증(pydantic)이 필요 없어서 패키지가 18개에서 11개로 준다 |
| uvicorn | 0.54.0 | 웹 서버(127.0.0.1에만 묶는다) |
| Jinja2 | 3.1.6 | HTML 템플릿, 자동 이스케이프 |
| httpx | 0.28.1 | 시험 전용(Starlette TestClient) |

- 하위 의존성도 해석 결과 그대로 `requirements.txt`에 고정한다: anyio 4.16.0, click 8.5.1, h11 0.16.0, idna 3.20, MarkupSafe 3.0.4, httpcore 1.0.9, certifi 2026.10.23.
- 폼 본문은 `urllib.parse.parse_qs`로 직접 읽어 python-multipart를 쓰지 않는다. htmx도 쓰지 않는다. POST 뒤 원래 화면으로 돌려보낸다(PRG).
- ADR-0006은 "FastAPI → Starlette"로 개정한다.

## 구조 (기존 계층 그대로: domain ← adapters·llm·store ← 업무 흐름·worker·cli·web)
```
migrations/0003_stage3.sql   DDL v1.1의 3단계 표 + manual 소스·사용자 한 행 + RLS
radar/adapters/page.py       (신규) 공식 페이지 한 장 읽기: 스킴·포트·사설 IP 차단, 리다이렉트 재검사(3회), robots.txt(urllib.robotparser), 1MB·20초, HTML→텍스트(기존 domain/cleaning의 태그 제거 재사용), GitHub README(API raw)
radar/domain/prompts.py      PROFILE·RETYPE 지시문·스키마 추가, CLASSIFY 유형에 PROTOCOL_STANDARD·MODEL 추가
radar/profile.py             (신규) run_profile_job: 페이지 읽기 → _ask 재사용 → claims·claim_evidence 저장
radar/events.py              (신규) 규칙 이벤트: 릴리스 항목→release, 저장소 항목→first_published, first_published_at 재계산
radar/store/knowledge.py     (신규) claims·events·user_knowledge·notes·검색·Feed·보관 조회
radar/backup.py              (신규) COPY TO/FROM, manifest(행 수·SHA-256), 임시 스키마 복원 검증
radar/web/app.py, routes.py, templates/*.html   (신규) Starlette 앱, Host·Origin 검사 미들웨어
```
- 고치는 기존 코드:
  - `radar/analyze.py`: 수동 항목은 후보로 다루고, 기술이 새로 생기면 프로필 잡과 규칙 이벤트를 만든다.
  - `radar/store/technologies.py`: 정규명이 보관 기술과 같으면 그 기술에 잇는다.
  - `radar/store/jobs.py`: `PROFILE` 종류를 더하고, 잡을 수집 → 분석 → 프로필 순으로 집는다.
  - `radar/worker.py`: 처리기 표에 프로필 잡을 더한다.
  - `radar/holds.py`: `HOLD_TYPES`를 넓히고 `retype`을 더한다.
  - `radar/cli.py`: `add`, `web`, `backup`, `restore`, `holds retype` 명령을 더한다.
  - `radar/gate.py`: 3단계 점검 항목을 더한다.
- 재사용하는 기존 코드:
  - 분석 흐름: `_ask`·`daily_cap_reached`·`analysis_paused`(`radar/analyze.py`), `AnalysisStore`.
  - 수집 저장: `ItemStore` 저장 규칙(`radar/store/items.py`), `normalize_url`.
  - 보류 처리: `holds.set_type`·`link_item`·`new_from_item`. 웹 버튼도 이 함수들을 그대로 부른다.
  - 그 밖: `sanitize_json`, `UrllibClient`(`radar/adapters/http.py`).
- 파일은 300줄, 함수는 30줄 이하로 쓰고 공개 함수에는 타입 힌트를 단다(`rules/code.md`). 웹은 DB를 `radar/store/`로만 만진다.

## 작업 순서 (SPEC v1.5 3단계 절, PRD S3-01~09와 대응)
- **T3-0 준비**: 라이브러리 설치·고정, ADR-0006 개정, `0003_stage3.sql`(임시 스키마 시험 → 운영 적용, `schema_migrations`로 확인).
- **T3-1 수동 등록·페이지 읽기**(S3-06):
  - `radar/adapters/page.py`를 만든다. 안전 규칙마다 시험을 붙인다(사설 IP·`file:`·리다이렉트로 내부망 가기·큰 응답·robots 차단).
  - `radar add`를 만든다. 수동 항목은 후보 판별을 건너뛴다.
- **T3-2 보관·유형 넓히기·이벤트**:
  - 보관 기술에 잇는 규칙(S3-09 포함).
  - `holds retype`: 유형이 빈 기술만, 호출 1번.
  - 규칙 이벤트는 기존 연결분까지 한 번 채운다(`radar events backfill`, 운영 DB 쓰기는 추가뿐).
- **T3-3 프로필**(S3-01·S3-03):
  - 프로필 잡과 지시문을 만든다. 8칸 중 7칸은 모델이, 공식 출처 칸은 규칙이 채우고, 빈 칸은 행을 만들지 않는다.
  - 지시문을 심은 페이지 fixture로 스키마 이탈과 지시 추종이 없는지 시험한다.
  - 실제 모델로 5~10개를 점검한다.
- **T3-4 웹**(S3-07·S3-08, IA-1~4, UJ-1~5):
  - 화면을 Feed → 상세 → 검색 → Monitor 순으로 만든다.
  - 안전 미들웨어(Host·Origin), 자동 이스케이프, `http(s)` 링크만 만들기를 넣는다.
  - 다크 모드, 키보드 조작, 대비를 지킨다.
- **T3-5 백업·복원**: `radar backup`과 `radar restore`를 만든다. 복원은 임시 스키마에서만 하고, 행 수와 체크섬을 비교한다.
- **T3-6 게이트 측정과 QA**:
  - `gate-check --stage 3`: 출처 연결률 100%, 별칭 검색, 백업 복원, Monitor에서 실패 원인 확인.
  - 서로 다른 출처 3개가 연결된 기술 하나를 화면에 보여 준다.
  - 내장 브라우저로 화면을 클릭해 QA하고 `docs/qa-guide.md`에 적는다.
  - 사용자 QA는 실제 사용 체감, 문구·디자인, 이미 아는 기술 50~100개 넣기다.
- **T3-7 게이트 보고**: 숫자로 보고하고 승인을 받는다. MVP(1~3단계)가 여기서 끝난다.

## 위험과 한계
- **호출 한도:** 프로필 호출이 분류와 같은 하루 375회를 쓴다. 새 기술이 하루 30~50개면 안에 들어오지만, 첫 50~100개 수동 등록과 쌓인 기술의 프로필은 며칠에 나눠 처리된다.
- **GitHub 한도:** README 읽기가 비인증 한도(시간당 60회)를 수집과 나눠 쓴다. 그래서 시간당 30회로 제한한다.
- **모델이 지어낼 위험:** 근거 페이지가 있어도 Flash Lite가 지어낼 수 있다. 칸마다 `Unverified`로 두고 근거 링크를 보여 주며, 실제 모델 점검 표본은 눈으로 확인한다.
- **약관:** 페이지 읽기의 사이트 약관은 확인하지 못한다. 한 장만 읽고 저장하지 않는다(ADR-0007).
- **CSRF:** localhost 웹이라도 다른 사이트가 요청을 보낼 수 있어서 Host·Origin 검사가 빠지면 위험하다. 시험으로 고정한다.
- **확인하지 못한 것:** Starlette 1.x의 TestClient와 미들웨어 API가 문서와 같은지. T3-0에서 확인한다.

## 검증
- `python -m pytest` 통과 수를 변경 전후로 보고한다(현재 264개). 1·2단계 `gate-check`가 여전히 통과하는지 본다.
- 안전 시험:
  - 사설·루프백 주소, `file:` URL, 내부망으로 가는 리다이렉트를 거절한다.
  - `Host`·`Origin`이 다른 요청을 거절한다.
  - `javascript:` 링크가 링크로 나오지 않는다.
  - 지시문을 심은 페이지로 만든 프로필이 스키마를 벗어나지 않는다.
- 동작 시험:
  - 보관한 이름의 새 글이 새 기술을 만들지 않는다.
  - 이벤트를 다시 넣어도 중복이 없다.
  - 메모는 AI 분석이 바꾸지 않는다.
  - 백업 후 복원하면 행 수와 체크섬이 같다.
- 화면: `radar web`을 띄우고 내장 브라우저로 UJ-1~UJ-5를 따라 클릭한다.

# Plan v4.0 — 4단계(관계 분석과 그래프) 구현 (승인 2026-11-09, 착수 2026-11-09)

## Context
3단계 게이트는 2026-11-09에 통과했고, MVP가 끝났다. 기술은 출처·프로필과 함께 쌓이지만, 기술끼리 어떻게 이어지는지는 아직 없다. 사용자가 "핵심"이라고 한 옵시디언식 그래프뷰(PRD S4-08)는 관계 데이터가 있어야 그릴 수 있다.
- 선행 문서는 모두 승인됐다(2026-11-09): PRD 4단계 범위 조정(S4-05·S4-07은 5단계로), IA v1.2, User Journey v1.1(UJ-7~UJ-10), SPEC v1.7 4단계 절, ERD 가정 12.
- 이 Plan을 승인하면 아래 라이브러리도 함께 승인한 것으로 본다.
- 범위 안에서는 끊지 않고 진행한다. 커밋·푸시는 묶음마다 시험 결과를 확인한 뒤 성격별로 한다.
- 멈춰서 묻는 것: 삭제, 비용, 목록 밖 라이브러리, 작업 스케줄러 등록 직전 확인(SPEC에서 정함).

## 라이브러리 (승인 대상)
| 이름 | 버전 | 이유 |
| --- | --- | --- |
| Cytoscape.js | 3.34.4 (2026-10-15 공개, MIT, 의존성 0개) | 그래프 그리기. npm 레지스트리에서 `dist/cytoscape.min.js` 한 파일을 받아 `radar/web/static/`에 넣는다. CDN은 쓰지 않는다. SHA-256은 시험으로 고정하고, npm이 준 sha512 무결성 값과 대조한다 |

- Python 새 패키지는 없다. 정적 파일은 Starlette에 이미 있는 `StaticFiles`로 내보낸다.
- 배치는 Cytoscape 내장 `cose`를 쓰고, 확장 플러그인은 쓰지 않는다.

## 구조 (기존 계층 그대로: domain ← adapters·llm·store ← 업무 흐름·worker·cli·web)
```
migrations/0005_stage4.sql      DDL v1.1 4단계 표 + origin·hidden_at·quote·relation_proposals + RLS
radar/domain/relations.py       (신규) 후보 찾기(이름·별칭이 페이지에 낱말로 나오는 기술, 최대 20), 모델 출력 검사(근거 문장이 페이지에 실제로 있는지, 후보 id인지), 대칭 관계 방향 맞추기
radar/domain/prompts.py         PROFILE 지시문에 관계 규칙 추가, profile_schema(candidate_ids)로 relations 배열 스키마
radar/store/relations.py        (신규) AI 관계 저장(관계+근거 한 트랜잭션, 매트릭스 위반·유형 없음은 제안으로), 확인·숨김·되돌리기, 직접 잇기, 검토 목록, 표본, 그래프 데이터
radar/web/graph_routes.py       (신규) /graph, /graph.json, /relations/sample, 관계 POST
radar/web/static/cytoscape.min.js, graph.js   (신규) 그래프 화면 스크립트(점·선은 글자 데이터로만 넣는다)
radar/web/templates/graph.html, _relations.html (신규), detail.html·monitor.html·base.html 수정
scripts/start-worker.ps1        (신규) pid가 살아 있으면 그냥 끝, 아니면 워커를 창 없이 띄우고 pid 기록
```
- 고치는 기존 코드:
  - `radar/profile.py`: 페이지를 읽었으면 후보를 찾아 payload에 넣고, 결과의 relations를 `store/relations`로 저장한다. 버린 수를 로그로 남긴다.
  - `radar/domain/profile.py`: `parse_profile`은 그대로 두고, 관계 해석은 `domain/relations.py`가 맡는다.
  - `radar/holds.py`: `set_type`(웹·CLI·retype 공통 경로)이 성공하면 그 기술의 open 제안을 다시 검사한다.
  - `radar/profile.py` `backfill`과 `radar/cli.py`: `--all`(이미 프로필이 있는 기술도)을 더한다.
  - `radar/web/app.py`: `StaticFiles` mount를 더한다. `routes.py`는 300줄을 넘지 않게 그래프·관계 라우트를 새 파일로 나눈다.
  - `radar/gate.py`, `radar/cli.py`: `gate-check --stage 4`를 더한다.
  - `radar/backup.py`: `TABLES`에 4단계 표 4개를 더한다.
- 재사용하는 기존 코드:
  - 호출·기록: `_ask`(`radar/analyze.py`), `AnalysisStore`. 입력 해시에 스키마가 들어가므로 후보가 바뀌면 새 호출이 된다.
  - 이름 찾기: `TechnologyStore`(정규명·별칭), `views.search`.
  - 화면: 3단계 안전 규칙(`HostOriginGuard`, 자동 이스케이프, `safe_url`), `_form`·`_back`·`_number`(`radar/web/routes.py`).
- 함수는 30줄, 파일은 300줄 안팎으로 쓰고, 공개 함수에는 타입 힌트를 단다.

## 작업 순서 (SPEC v1.7 4단계 절)
- **T4-0 준비**: Cytoscape 3.34.4 받기(무결성 대조), `0005_stage4.sql`(임시 스키마 시험 → 운영 적용).
- **T4-1 관계 뽑기 규칙**(domain):
  - 후보 찾기: 낱말 경계, 3글자 미만은 대소문자까지 일치, 자기 자신·보관 기술 제외.
  - 출력 검사: 후보 밖 id, 페이지에 없는 근거 문장(공백 무시 비교), 대칭 방향.
  - 지시문을 심은 페이지 fixture로 시험한다.
- **T4-2 저장과 프로필 연결**(store·profile·holds):
  - 매트릭스 트리거가 막으면 제안(`matrix`)으로, 한쪽 유형이 없으면 제안(`untyped`)으로 보낸다.
  - 같은 관계는 근거만 늘린다. 다시 만들어도 확인·숨김은 그대로 둔다.
  - 유형을 정하면 open 제안을 다시 검사한다.
  - `profile backfill --all`.
- **T4-3 관계 화면**:
  - 상세 화면의 관련 기술 표(문장형, 확인·숨기기·되돌리기)와 직접 잇기(활성 기술 이름 datalist, 매트릭스 위반이면 까닭을 보여 줌).
  - Monitor 검토 목록(버리기, 유형 고치기 링크).
  - `/relations/sample`(md5(id) 순서 50개).
- **T4-4 그래프 화면**:
  - `/graph.json`(전체·이웃 1~2단계·`all=1`).
  - `/graph`(색은 유형, 크기는 출처 수, 점선·실선, 대칭은 화살표 없음, 거르기, 이름 찾기, 점 누르면 상세).
  - 상세 안 이웃 그래프와, 같은 내용의 표.
  - 다크 모드·키보드·대비를 지킨다.
- **T4-5 워커 자동 시작**: `scripts/start-worker.ps1`과 등록·해제 명령을 문서로 둔다. 등록은 사용자에게 한 번 확인받고 `Register-ScheduledTask`(현재 사용자 로그온, 관리자 권한 없음)로 한다.
- **T4-6 운영 적용**:
  - 마이그레이션 적용, 워커를 새 코드로 다시 시작, `profile backfill --all`(약 200~250회, 1~2일).
  - 실제 모델 결과 10개를 눈으로 점검: 관계가 맞는지, 근거 문장 버림 비율, 깨진 글자.
- **T4-7 게이트**:
  - `gate-check --stage 4`: 매트릭스 위반 저장 0, AI 관계 근거 없음 0, 그래프 점·선 수 일치.
  - 사용자가 `/relations/sample` 50개를 판정하고 Precision을 잰다.
  - QA를 `docs/qa-guide.md`에 적고, 숫자로 보고해 승인을 받는다.

## 위험과 한계
- **정수 enum:** Gemini가 `responseJsonSchema`의 정수 enum을 지키는지 확인하지 못했다. 그래서 받은 뒤에도 후보 id인지 검사한다(이중 방어).
- **근거 문장 버림:** 모델이 문장을 바꿔 쓰면 근거 검사에서 많이 버려질 수 있다. T4-6에서 버림 비율을 재고, 높으면 지시문을 고친다(검사를 느슨하게 하지 않는다).
- **이어지지 않는 관계:** 먼저 프로필이 만들어진 기술은 나중 기술과 이어지지 않는다(SPEC 한계). 다시 이을 방법은 4단계 측정 뒤에 정한다.
- **호출:** `--all`은 이미 프로필이 있는 약 90개도 다시 부른다. 관계를 얻으려면 한 번은 다시 불러야 하기 때문이다. 그동안 새 글 분석과 하루 375회를 나눠 쓴다.
- **그래프가 읽힐지:** 기술 200~1,000개에서 점이 뭉칠 수 있다. 기본으로 관계가 있는 기술만 보여 주고, 사용자 QA로 확인한다.
- **깨진 한국어:** AI 판단 칸처럼 깨질 수 있다. 관계에는 모델이 쓴 한국어가 없다(근거 문장은 페이지 원문, 관계 문장은 규칙으로 만든다).

## 검증
- `python -m pytest` 통과 수를 변경 전후로 보고한다(현재 377개). 1~3단계 `gate-check`가 여전히 통과하는지 본다.
- 안전 시험:
  - 이름에 `<script>`를 넣은 기술이 그래프·표에서 글자로만 보인다.
  - 다른 Origin에서 온 관계 POST를 거절한다.
  - 지시문을 심은 페이지로 관계를 뽑아도 후보 밖 기술이나 스키마 밖 값이 나오지 않는다.
- 동작 시험:
  - 매트릭스를 어긴 관계와 유형 없는 기술의 관계는 저장되지 않고 제안으로 간다.
  - 유형을 정하면 제안이 관계가 된다.
  - 근거 문장이 페이지에 없으면 버린다. 대칭 관계는 한 방향만 저장한다.
  - 같은 관계를 다시 뽑으면 근거만 늘어난다. 다시 만들어도 확인·숨김이 그대로다.
  - `/graph.json`의 점·선 수가 DB와 같다.
  - 정적 파일 SHA-256이 고정값과 같다.
- 화면: `radar web`을 띄워 그래프·상세·Monitor·표본 화면이 200으로 응답하는지 HTTP로 확인한다. 눈으로 보는 확인은 사용자 QA로 넘긴다(이 세션에는 내장 브라우저가 없다).
