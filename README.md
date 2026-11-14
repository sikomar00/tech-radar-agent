# Tech Radar Agent

[![CI](https://github.com/sikomar00/tech-radar-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/sikomar00/tech-radar-agent/actions/workflows/ci.yml)

GitHub·Hacker News·기술 블로그 RSS에서 새 기술을 모으고, AI가 "이게 기술인지, 어떤 유형인지, 이미 아는 기술인지, 다른 기술과 어떻게 이어지는지"를 판별해 **출처가 붙은 개인 기술 지식 그래프**로 쌓는 1인용 기술 레이더입니다.

![지식 그래프 화면: 점은 기술(색은 유형), 선은 관계. 점선은 AI가 뽑고 아직 확인하지 않은 관계, 실선은 확인했거나 직접 이은 관계](docs/images/graph-demo.png)

<sub>그림은 합성 예시 데이터(기술 13개, 관계 10개)로 로컬에서 띄운 화면입니다. 운영 DB의 데이터가 아닙니다.</sub>

## 왜 만들었나

기술 소식은 여러 곳에 흩어져 있고, 읽고 나면 지식으로 남지 않습니다. 어떤 기술이 언제 나왔는지, 무엇 위에 만들어졌는지, 무엇과 경쟁하는지를 나중에 다시 찾기 어렵습니다. 이 프로젝트는 "직접 검색하지 않아도 중요한 기술 변화를 놓치지 않고, 예전에 본 기술과 새 기술의 관계를 이해한다"를 성공 기준으로 잡았습니다([PRD](docs/PRD.md)).

## 만드는 방식: 단계 게이트

6가지 역량(수집 → 식별 → 지식 DB → 관계 → 브리핑 → 시각화)을 한 단계씩 만들고, 단계마다 **미리 정한 숫자 기준(게이트)** 을 통과해야 다음 단계로 넘어갔습니다. 수치와 측정 방법은 모두 [`docs/qa-guide.md`](docs/qa-guide.md)에 있습니다.

| 단계 | 내용 | 게이트 결과 |
|---|---|---|
| 1. 수집 엔진 | GitHub·HN·RSS 어댑터, 잡 큐 워커, 재시도·백오프 | 24시간 42분 동안 요청 1,078건 중 실패 0건, 중복 키 0건 (2026-11-06 통과) |
| 2. 기술 식별 | Gemini로 후보 판별·유형 분류·기존 기술 매칭, 골든셋 평가 | 보류용 골든셋 기준 후보 Precision 81.8%, Recall 85.7%, 유형 정확도 83.3%, 중복률 0% (2026-11-08 통과) |
| 3. 지식 DB | 출처 연결, 별칭 검색, 프로필(근거 없는 주장 0), 백업·복원 검증 | 백업 복원 14개 표 일치, 별칭 검색 표본 50/50 (2026-11-09 통과, MVP) |
| 4. 관계 분석 | 허용 매트릭스로 검증한 AI 관계, 근거 문장, 그래프 화면 | AI 관계 97개 생성, 판정 표본 50개 고정 후 **중단** |
| 5·6. 브리핑·시각화 | — | 시작하지 않음 |

한계도 같이 적어 둡니다. 2단계 라벨은 사람이 검토하기 전 초안이고, 보류용 세트는 1차 측정 뒤 다시 잰 값이라 낙관적일 수 있으며, 매칭·중복 표본이 작습니다.

## 설계에서 지킨 원칙

- **AI는 규칙으로 안 되는 곳에만**: URL 정규화·해시 중복 검사는 규칙으로, 후보 추출·분류·관계 추출만 모델로 합니다.
- **사실과 AI 생성 설명을 섞지 않음**: 프로필의 주장마다 출처를 붙이고, 근거 없는 주장은 저장하지 않습니다. AI가 뽑은 관계는 "미확인" 점선으로 보이고 사람이 확인해야 실선이 됩니다.
- **모델 출력은 신뢰하지 않음**: JSON 스키마로 검증하고, 관계는 유형별 허용 매트릭스([ADR-0002](docs/adr/0002-relation-model.md))를 어기면 버립니다. 하루 호출 상한(375회)을 코드로 지킵니다.
- **변화 이력 보존**: 기술 상태를 덮어쓰지 않고 `technology_events`로 시점별로 남깁니다.

결정 기록: [ADR 0001~0007](docs/adr/) · 데이터 모델: [ERD](docs/ERD.md), [DDL](docs/ddl/v1.1.sql) · 화면 구조: [IA](docs/IA.md), [User Journey](docs/USER-JOURNEY.md) · 명세: [SPEC](docs/SPEC.md) · 작업 순서: [PLAN](docs/PLAN.md) · 용어: [GLOSSARY](docs/GLOSSARY.md)

## 구조

```
radar/
  adapters/   github · hackernews · rss · page(프로필용 원문) · http(재시도·제한)
  llm/        gemini 클라이언트, 응답 캐시
  domain/     정규화, 프롬프트, 관계 규칙 같은 순수 로직
  store/      Postgres 접근(기술·항목·잡·관계·그래프·모니터)
  web/        Starlette + Jinja2 서버 렌더링 화면, Cytoscape.js 그래프(3.34.4, 파일 해시 고정)
  worker.py   잡 큐를 돌리는 수집·분석 워커
  cli.py      radar 명령(migrate, seed, worker, web, eval, gate-check, backup, restore …)
migrations/   0001~0006 단계별 스키마
tests/        pytest 407개(가짜 HTTP·가짜 모델·가짜 시계, DB 테스트는 시험마다 임시 스키마)
```

## 실행

Python 3.11 이상, PostgreSQL(운영은 Supabase), Gemini API 키가 필요합니다. 웹 화면은 `127.0.0.1`에만 열리고 로그인이 없습니다([ADR-0006](docs/adr/0006-web-stack.md)).

```bash
pip install -r requirements.txt
# .env 에 DATABASE_URL, GEMINI_API_KEY
python -m radar migrate
python -m radar seed        # 기본 수집 소스
python -m radar worker      # 수집·분석 워커
python -m radar web         # http://127.0.0.1:8765
```

## 테스트

```bash
python -m pytest            # DATABASE_URL이 없으면 DB 테스트 217개는 건너뜀
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres python -m pytest
```

일반 PostgreSQL에서 돌릴 때는 Supabase처럼 `create schema extensions;`를 먼저 만들어 둡니다(pg_trgm 위치). CI는 PostgreSQL 16 서비스 컨테이너로 407개 전체를 돌립니다.

## 상태

**2026-11-13 중단.** 4단계 게이트(관계 50개 사람 판정) 직전에 멈췄습니다. 만들 수는 있었지만 "최종 결과물이 무엇이어야 하는가"의 정의가 아직 모호하다고 판단했기 때문입니다. 다시 시작하면 문제 정의부터 다시 합니다. 자세한 상태는 [Intent.md](Intent.md)에 있습니다.
