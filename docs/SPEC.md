# Spec v1.1

기준: `docs/PRD.md`, `docs/ERD.md`, `docs/adr/`. 1단계는 구현할 수 있는 수준으로 쓰고, 2단계는 중복 탐지와 후보 선별만, 3단계는 확정된 것만 적는다. 4~6단계는 PRD 원칙(게이트 통과 전에는 다음 단계를 시작하지 않는다)에 따라 각 단계 시작 전에 추가한다.

## 1단계 · 수집 엔진

### 어댑터 인터페이스 (S1-01)
- 어댑터는 `fetch(source) -> FetchResult`를 구현한다. `FetchResult`는 항목 목록과 요청별 결과(성공 또는 실패와 원인)를 담는다.
- 항목(`RawItem`)은 `external_id`(없으면 null), `url`, `title`, `excerpt`, `published_at`(없으면 null), `metadata`를 가진다.
- 어댑터는 DB를 모른다. HTTP 호출은 주입받고, 응답을 `RawItem`으로 바꾸는 일만 한다.
- 요청 사이는 1초 쉬고, 타임아웃은 20초, 요청마다 식별 가능한 User-Agent를 보낸다. 요청 단위 재시도는 하지 않는다. 성공률을 요청 단위로 세기 때문에 실패를 숨기지 않기 위해서다. 재시도는 잡 단위로 한다.

### 소스별 동작 (스파이크에서 동작을 확인한 방식)
| 소스 | 가져오는 것 | `external_id` | 비고 |
| --- | --- | --- | --- |
| GitHub 신규 저장소 | 검색 `created:>{7일 전} stars:>20`, 스타순 30건 | 저장소 숫자 id | metadata에 스타, 언어, 토픽, archived, fork, 생성·푸시 시각 |
| GitHub 릴리스 | 시드 저장소별 최근 3건 | 릴리스 id | 제목은 `owner/repo tag`. 시드 목록은 `sources.config` |
| Hacker News | 공식 API의 topstories와 showstories 각 15건 | 글 id | URL이 없는 글은 HN 글 주소를 URL로 쓴다. dead·deleted는 건너뛴다 |
| RSS | 피드의 최근 항목 | guid(없으면 null) | 발췌가 없으면 빈 문자열을 허용한다 |

### 정제 (S1-03, S1-06)
- 제목은 300자, 발췌는 280자에서 자르고(DB 상한은 500자) 공백과 제어 문자를 정리한다. 본문 전문은 저장하지 않는다.
- 엔티티를 풀고 태그를 지우는 것은 HTML이 올 수 있는 필드(RSS 요약, HN 글 본문)에만 한다. 제목이나 GitHub 설명 같은 일반 텍스트는 태그를 지우지 않는다(`Vec<T>`가 깨지면 안 된다). HN 제목은 엔티티만 푼다.
- `normalized_url` 규칙은 `host/path?정렬된 쿼리` 형태다.
  1. 스킴(http, https)은 지운다.
  2. 호스트는 소문자로 바꾸고, 앞의 `www.`와 기본 포트를 지운다.
  3. 프래그먼트(`#...`)를 지운다.
  4. 추적 파라미터(`utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`)를 지우고 나머지 파라미터는 키 순으로 정렬한다.
  5. 경로 끝의 `/`를 지운다(루트 제외). 경로의 대소문자는 유지하지만 `github.com` 경로는 소문자로 바꾼다.
- 위 규칙은 일반 관행에 따른 가설이다. 스파이크 표본에는 정규화가 필요한 변형 사례가 없었다. 새 변형이 나오면 fixture를 추가하고 규칙을 고친다.
- `content_hash`는 소문자로 바꾸고 공백을 정리한 `제목 + "\n" + 발췌`의 SHA-256이다. 발췌가 비어 있으면 `normalized_url`을 이어 붙여서, 제목만 같은 서로 다른 글이 한 항목으로 합쳐지지 않게 한다(Hugging Face 피드가 이 경우다).

### 저장과 중복 방지 (S1-02, S1-04)
- 항목마다 `(source_id, external_id)` → `normalized_url` → `content_hash` 순으로 기존 행을 찾는다.
- 못 찾으면 새로 넣는다. 찾았을 때는 **같은 항목인지** 먼저 본다. 찾은 행의 `source_id`와 `external_id`가 모두 같을 때만(둘 다 `external_id`가 없는 경우 포함) 같은 항목으로 보고 제목, 발췌, metadata, `collected_at`을 갱신한다(`first_discovered_at`은 유지). 그 밖의 경우, 곧 다른 소스가 이미 가진 URL이거나 같은 URL·같은 텍스트를 가리키는 서로 다른 항목이면 행을 건드리지 않고 `duplicates`로 세고 로그에 남긴다. 덮어쓰면 소스끼리 제목이 번갈아 바뀌기 때문이다. 대신 같은 URL을 가리키는 서로 다른 항목(예: 모든 글이 같은 페이지로 링크하는 변경 로그 피드)은 첫 항목만 남는다. ERD의 `normalized_url` 전역 유일 제약의 결과다.
- 빈 `external_id`는 없는 것으로 본다. 제어 문자는 `url`과 `external_id`에서도 지운다.
- GitHub의 `external_id`는 `repo:<id>`, `release:<id>`다. 저장소 id와 릴리스 id는 서로 다른 번호 공간이라 접두사가 없으면 같은 숫자가 한 행으로 합쳐질 수 있다.
- 갱신은 키(`external_id`, `normalized_url`, `content_hash`)와 `url`을 바꾸지 않으므로 제약과 충돌하지 않는다. 그 대신 GitHub 저장소 이름이 바뀌면 `url`이 옛 이름을 가리킨다(알려진 한계, 2단계에서 필요해지면 충돌 처리와 함께 갱신한다). 삽입 중에 키 충돌이 나면(조회 직후 다른 쓰기가 끼어든 경우뿐) 그 항목을 건너뛰고 `conflicts`로 센다. 저장할 수 없는 항목(호스트 없는 URL, 인코딩할 수 없는 문자)도 건너뛰고 `invalid`로 센다. 이 개수들은 실행 로그에 남기고 `collection_runs`에는 저장하지 않는다.
- `published_at`은 소스가 준 최초 공개 시각이고, `first_discovered_at`은 우리가 처음 저장한 시각이다. 둘은 다른 값이다.
- GitHub 항목은 스타 수 스냅샷(`metadata.stars_snapshots`, 최근 10개의 `{at, stars}`)을 이어 붙여 둔다. 2단계의 스타 증가율 계산에 쓴다. 스키마 변경은 없다.

### 잡, 워커, 스케줄 (S1-05, S1-07)
- 워커는 1개이고 루프는 다음 순서다.
  1. 스케줄: 활성 소스마다 마지막 실행 뒤 `collect_interval`이 지났고 `queued`나 `running` 잡이 없으면 `collect_source` 잡을 만든다.
  2. 잡 하나를 `FOR UPDATE SKIP LOCKED`로 가져와 실행한다.
  3. 성공하면 `succeeded`로 한다. 실패하면 `2^attempts`분 뒤로 `run_after`를 미루고 다시 `queued`로 둔다(`attempts`는 잡을 가져올 때 올린다). `max_attempts`(3)를 다 쓰면 `failed`로 남긴다.
  - 잡은 요청이 **모두** 실패했거나 예외가 났을 때만 실패다. 일부 요청만 실패하면 성공한 항목을 저장하고 잡은 성공으로 끝내며, 실패한 요청은 `collection_runs`의 `requests_failed`와 `error`에 남긴다(실패한 요청을 즉시 재시도해도 같은 원인일 가능성이 커서 요청을 더 늘릴 뿐이라서다).
  4. `locked_at`이 15분 넘은 `running` 잡은 시도 횟수가 남았으면 `queued`로 되돌리고, 다 썼으면 `failed`로 둔다.
- 기본 수집 주기는 GitHub 6시간, Hacker News 1시간, RSS 1시간이다(초기값).
- `collection_runs`는 잡 시도마다 1행이다. `requests_total`, `requests_failed`, 그리고 `error`에 원인 분류(`http_status`, `timeout`, `network`, `parse`, `rate_limited`)와 메시지를 적는다.
- 소스별 성공률은 `(requests_total - requests_failed) / requests_total`이다. HTTP 2xx가 아닌 응답, 타임아웃, 연결 오류(연결 끊김, SSL 오류, 본문이 약속한 길이보다 짧게 끊긴 경우 포함), 파싱 실패는 실패로 세고, 200 응답에 결과가 0건인 경우는 성공으로 센다.
- 예외로 끝난 실행(어댑터 오류, 저장 오류)은 이미 보낸 요청에 **실패 1건을 더해서** 기록한다. 0건으로 적으면 성공률 분모에서 빠져 지표가 부풀려지기 때문이다. 워커가 죽어서 끝나지 못한 실행은 다음 정리 때 요청 1건 실패로 닫는다(`worker_died`). 소스가 없거나 페이로드가 잘못된 잡은 다시 해도 소용없으므로 재시도 없이 바로 `failed`로 둔다.
- 수동 재실행은 `radar jobs retry <job_id>`, 실행 기록 조회는 `radar runs list`다. 둘 다 운영용 명령이고 화면이 아니다.

### 1단계 시험 (게이트와 대응)
- 어댑터마다 실제 응답을 줄여 저장한 fixture 테스트.
- `normalized_url`과 `content_hash` 단위 테스트.
- 같은 입력을 두 번 수집하면 새 행이 0개다.
- DB 제약 테스트와, 워커의 재시도·잠금 복구 테스트.

## 2단계 · 중복 탐지와 후보 선별

- 사전 필터(S2-01)의 신호는 GitHub의 릴리스 여부, 스타 수와 증가율, 토픽·키워드, Hacker News의 점수와 키워드, RSS의 키워드다. 임계값과 키워드 목록은 튜닝용 골든셋으로 정하고 보류용 골든셋으로 검증한다(S2-07).
- 기존 기술 매칭(S2-04)은 세 단계다.
  1. 규칙: 별칭이 대소문자 무시로 일치하거나, 같은 URL·저장소가 이미 `technology_sources`로 연결돼 있으면 같은 기술로 확정한다.
  2. 퍼지: `pg_trgm` 유사도 0.6 이상(초기값, 튜닝 대상)인 후보 중 상위 5개만 다음 단계로 보낸다.
  3. LLM 판정: 새 항목과 후보 5개를 주고 "같은 기술, 다른 기술, 모름" 중 하나를 받는다. "모름"은 분류 보류 큐로 간다.
- LLM 호출 규약(S2-06): 도구 권한이 없고, 외부 콘텐츠는 데이터 구간으로 구분해서 넣고, 출력은 JSON 스키마로 검증한다. 어기면 `analysis_runs`에 실패와 재시도 가능으로 기록한다. 공급자는 ADR-0004(제안: Groq 무료 API, REST, 교체 가능한 인터페이스 뒤)에서, 검증 라이브러리는 `jsonschema` 4.26.0으로 정했다(2단계 코드 시작 때 추가).

### 후보 판별 기준 (2026-11-05 결정)
- 후보는 분류가 ENTITY 또는 UPDATE인 항목이다. USAGE, NEWS, OPINION은 기술을 언급해도 후보가 아니다.
- ENTITY와 UPDATE는 개발자·AI 실무자가 추적할 만한 도구, 라이브러리, 모델, 표준만 해당한다. 개인용 앱, 게임, 취미 프로젝트는 NOISE다. PRD의 "소프트웨어·AI 범위"를 이렇게 좁혀 읽는다.
- 2단계 유형은 PRODUCT와 FRAMEWORK_LIBRARY다. 모델 릴리스는 MODEL 유형이 3단계에 열리므로 유형을 비워 분류 보류 큐로 보낸다. 보류 큐에 모델이 너무 많이 쌓이면 2단계 측정 때 MODEL을 앞당길지 다시 묻는다.

## 3단계 · 확정된 것만
- 수동 등록은 `radar add <url>`과 `radar add --file urls.txt`다(CLI로 결정함).
- 수동 등록의 출처를 위해 `sources.kind`에 `manual`을 추가한다. 이 변경은 3단계 마이그레이션에서 하고, 그때 시험한다.
- 프로필 생성, 이벤트 수집, 검색, 웹 화면 3개의 상세는 3단계 시작 전에 이 문서에 추가한다.

## 승인 시 함께 확인할 가정
1. 제목 300자, 발췌 280자 절단과 `content_hash` 입력 정의.
2. 기본 수집 주기(GitHub 6시간, Hacker News와 RSS 1시간)와 재시도(최대 3번, `2^attempts`분 대기).
3. 퍼지 유사도 0.6과 후보 5개는 시작값이고 골든셋으로 조정한다.
4. 스타 스냅샷을 `metadata`에 두고 별도 테이블은 만들지 않는다.
