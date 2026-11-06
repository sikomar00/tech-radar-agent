# QA 가이드 · 1단계 수집 엔진

기준: `rules/qa.md`. 시트 대신 이 문서의 표로 기록한다(3단계에서 다시 정함). 마지막 갱신: 2026-11-06.

## Claude QA

| ID | 대상 | 방법 | 기대값 | 실제값 | 결과 | 날짜 |
| --- | --- | --- | --- | --- | --- | --- |
| Q-01 | S1-01 Hacker News 어댑터 | `tests/test_hackernews.py`(실제 응답 fixture 3건) | 항목 3, 요청 4, 실패 0. 삭제·죽은 글은 실패 없이 제외, 일부 요청 실패는 집계, 제목의 `<T>`는 유지 | 8개 통과 | Pass | 2026-11-05 |
| Q-02 | S1-01 GitHub 어댑터 | `tests/test_github.py`(실제 검색·릴리스 응답) | 신규 저장소 2, 릴리스 2(id는 `repo:`, `release:` 접두사). 한도 초과는 `rate_limited`, 형식 이상은 `parse` | 6개 통과 | Pass | 2026-11-05 |
| Q-03 | S1-01 RSS 어댑터 | `tests/test_rss.py`(실제 피드 4종: RSS 3, Atom 1) | 항목 2씩, 발췌 없는 피드는 빈 문자열, guid가 없거나 비어 있으면 `external_id` null | 14개 통과 | Pass | 2026-11-05 |
| Q-04 | S1-03, S1-06 정제·정규화 | `tests/test_domain.py` | HTML 필드만 태그 제거, 일반 텍스트는 유지, 제어문자 제거, URL 변형이 한 키로 합쳐짐, 호스트 없는 URL은 거부 | 22개 통과 | Pass | 2026-11-05 |
| Q-05 | S1-05 실패 원인 분류 | `tests/test_http.py` + 어댑터 테스트 | `http_status`, `timeout`, `network`, `parse`, `rate_limited`로 분류 | 4개 통과(어댑터 테스트에서도 확인) | Pass | 2026-11-05 |
| Q-05b | S1-05 실제 네트워크 오류 분류 | `tests/test_http_client.py`(로컬 서버로 연결 끊김, 본문 잘림, 느린 응답, 연결 거부, 요청 간격) | 모두 요청 실패로 분류(`network`, `timeout`)되고 예외로 새지 않음 | 7개 통과 | Pass | 2026-11-05 |
| Q-06 | 게이트: 어댑터마다 fixture 테스트 | 위 Q-01~03 | 3종 모두 있음 | 3종 모두 있음 | Pass | 2026-11-05 |
| Q-07 | S1-05, S1-07 잡·스케줄 SQL | Supabase 임시 스키마에서 SQL 직접 실행(잠금, 복구, 수동 재실행, 스케줄, 게이트 쿼리) | 어서션 전부 통과 | 전부 통과, 임시 스키마 삭제 확인 | Pass (SQL만) | 2026-11-05 |
| Q-08 | S1-04, S1-05, S1-07 DB 저장·잡·재시도 | `tests/test_store_items.py` 16, `test_store_jobs.py` 9, `test_collect.py` 10 | 35개 통과 | 전체 `pytest` 96개 통과, 건너뜀 0(DB 35개 포함). 실제 Supabase 프로젝트(`tech-radar-agent`)에서 실행, 테스트 뒤 임시 스키마 0개 확인 | Pass | 2026-11-05 |
| Q-09 | 게이트: 같은 입력을 두 번 수집하면 새 행 0 | `test_collect.py::test_recollecting_the_same_input_creates_no_new_rows` | 새 행 0 | 테스트 통과(위 96개에 포함). 실제 재수집(6개 소스, 1분 간격)은 새 행 1건 — `rss-huggingface`에서 첫 수집에 없던 새 URL이 나옴(두 수집 사이에 피드가 바뀐 것으로 추정, 피드 원본은 확인하지 못함). 나머지 5개 소스는 0건, 키 중복 행 0 | Pass (테스트), 실수집은 입력이 같지 않았음 | 2026-11-05 |
| Q-10 | 게이트: 3개 소스 수집, 소스별 성공률 95% 이상 | `radar gate-check`, 24시간 측정 | 소스 종류 3개, 각 95% 이상 | 2026-11-05 19:35 ~ 2026-11-06 20:17 KST(24시간 42분). 소스 종류 3개: github 156건 중 실패 0(100%, 저장 60건), hackernews 818건 중 실패 0(100%, 저장 134건), rss(4개 소스) 104건 중 실패 0(100%, 저장 97건). 요청 합계 1,078건, 실패 0건. 워커 로그에서 소스마다 24회 주기, 최대 간격 60분. 23:00 DB 연결 오류 1건은 워커가 스스로 다시 연결해 다음 주기에 수집했다. GitHub·RSS는 요청 수가 적어(GitHub 156건) 표본이 작다 | Pass | 2026-11-06 |
| Q-11 | 게이트: 실패 원인이 로그에서 보임 | `radar runs list`, `radar gate-check`, `test_collect.py::test_gate_report_counts_requests_failures_and_causes` | 원인 종류가 출력됨 | 24시간 동안 실제 실패가 없어서 `gate-check`는 "기록된 실패 없음"을 출력했다. 원인 집계와 출력은 시험(실패를 심은 실행 기록)으로 확인했고 통과했다. 실제 장애에서의 출력은 보지 못했다 | Pass(시험으로 확인) | 2026-11-06 |

## 사용자 QA 목록

사람이 해야 하는 항목이다(비밀번호·실데이터·실제 환경).

| ID | 해 주실 일 | 방법 | 정상이면 보이는 것 | 주의점 |
| --- | --- | --- | --- | --- |
| U-01 | DB 연결 문자열을 `.env`에 넣기 | Supabase 대시보드 → `tech-radar-agent` → Connect에서 연결 문자열을 복사한다. `[YOUR-PASSWORD]` 자리에 DB 비밀번호를 넣어 `C:\TechRadarAgent\.env`에 `DATABASE_URL=...` 한 줄로 저장한다 | `python -m radar migrate` 를 실행하면 "적용: 0001_stage1.sql"이 나온다 | 비밀번호를 채팅에 붙이지 않는다. 직접 연결이 안 되면 같은 화면의 세션 풀러 문자열을 쓴다 |
| U-02 | 수집된 항목이 원본과 맞는지 눈으로 확인하기 | 수집이 끝난 뒤 Supabase Table Editor에서 `source_items` 10건을 고르고 `url`을 열어 제목을 비교한다 | 제목과 내용이 원본 페이지와 같다 | 원본이 삭제되었거나 이동했을 수 있다. 낯선 외부 링크는 조심해서 연다 |
| U-03 | 24시간 측정 동안 PC 켜 두기 | 측정을 시작한다고 알려 드리면 그때부터 PC를 켜 두고 절전 모드를 끈다 | 끝난 뒤 `python -m radar gate-check`가 소스 종류 3개와 성공률을 출력한다 | PC가 꺼지면 측정 시간이 늘어난다. 꺼진 시간은 실패로 세지 않는다 |
