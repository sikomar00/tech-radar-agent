-- DDL v1.1 검증. v1.1.sql을 적용한 빈 스키마에서 실행하고 마지막 select의 FAIL 행을 확인한다.
create table check_results (name text, outcome text);

create function expect_fail(n text, stmt text) returns void language plpgsql as $$
begin
  execute stmt;
  insert into check_results values (n, 'FAIL: 에러가 나야 하는데 성공함');
exception when others then
  insert into check_results values (n, 'PASS: ' || sqlstate);
end $$;

create function expect_ok(n text, stmt text) returns void language plpgsql as $$
begin
  execute stmt;
  insert into check_results values (n, 'PASS');
exception when others then
  insert into check_results values (n, 'FAIL: ' || sqlerrm);
end $$;

create function expect_eq(n text, q text, want bigint) returns void language plpgsql as $$
declare got bigint;
begin
  execute q into got;
  insert into check_results values (n, case when got = want then 'PASS' else format('FAIL: got %s want %s', got, want) end);
end $$;

insert into users (id) values ('00000000-0000-0000-0000-000000000001');
insert into sources (name, kind) values ('github', 'github'), ('rss-a', 'rss');
insert into source_items (source_id, external_id, url, normalized_url, title, content_hash) values
  (1, '1', 'https://a.test/1', 'a.test/1', 't1', 'h1'),
  (2, null, 'https://b.test/2', 'b.test/2', 't2', 'h2');
insert into technologies (canonical_name, type) values
  ('LangChain', 'FRAMEWORK_LIBRARY'), ('Claude Code', 'PRODUCT'), ('MCP', 'PROTOCOL_STANDARD'), ('Mystery', null);

select expect_eq('허용 매트릭스 행 수(ADR-0002 시드)', 'select count(*) from relation_type_rules', 61);

-- 1단계: 중복 방지 키와 발췌 길이
select expect_fail('중복 normalized_url', $q$ insert into source_items (source_id, external_id, url, normalized_url, title, content_hash) values (1, '9', 'u', 'a.test/1', 't', 'h9') $q$);
select expect_fail('같은 소스의 중복 external_id', $q$ insert into source_items (source_id, external_id, url, normalized_url, title, content_hash) values (1, '1', 'u', 'a.test/x', 't', 'h9') $q$);
select expect_fail('중복 content_hash', $q$ insert into source_items (source_id, external_id, url, normalized_url, title, content_hash) values (1, '8', 'u', 'a.test/y', 't', 'h1') $q$);
select expect_ok('external_id가 null인 항목 여러 개', $q$ insert into source_items (source_id, external_id, url, normalized_url, title, content_hash) values (2, null, 'u', 'b.test/3', 't', 'h3') $q$);
select expect_fail('발췌 501자', $q$ insert into source_items (source_id, url, normalized_url, title, excerpt, content_hash) values (1, 'u', 'a.test/z', 't', repeat('a', 501), 'h4') $q$);
select expect_fail('잘못된 소스 종류', $q$ insert into sources (name, kind) values ('x', 'twitter') $q$);

-- 2단계: 기술과 별칭, 분석 이력
select expect_fail('대소문자만 다른 기술 이름', $q$ insert into technologies (canonical_name) values ('langchain') $q$);
select expect_ok('보관된 기술은 같은 이름 허용', $q$ insert into technologies (canonical_name, archived_at) values ('langchain', now()) $q$);
select expect_eq('분류 보류 뷰', 'select count(*) from pending_classification', 1);
insert into technology_aliases (technology_id, alias, lang) values (1, 'langchain', 'en'), (1, '랭체인', 'ko');
select expect_fail('같은 기술의 중복 별칭', $q$ insert into technology_aliases (technology_id, alias, lang) values (1, 'langchain', 'en') $q$);
select expect_eq('별칭 오타 퍼지 검색(pg_trgm)', $q$ select count(*) from technology_aliases where alias % 'langchan' $q$, 1);
insert into analysis_runs (kind, source_item_id, model, input_hash, status, result, is_current)
  values ('classify', 1, 'm', 'i1', 'succeeded', '{}', true);
select expect_fail('같은 대상의 현재 분석 두 개', $q$ insert into analysis_runs (kind, source_item_id, model, input_hash, status, result, is_current) values ('classify', 1, 'm', 'i2', 'succeeded', '{}', true) $q$);
select expect_ok('이전 분석은 현재가 아니면 여러 개', $q$ insert into analysis_runs (kind, source_item_id, model, input_hash, status, result, is_current) values ('classify', 1, 'm', 'i3', 'succeeded', '{}', false) $q$);
select expect_fail('실패한 분석은 현재 값이 될 수 없음', $q$ insert into analysis_runs (kind, source_item_id, model, input_hash, status, error, is_current) values ('match', 1, 'm', 'i4', 'failed', 'boom', true) $q$);
select expect_ok('실패한 분석 기록', $q$ insert into analysis_runs (kind, source_item_id, model, input_hash, status, error, retryable) values ('match', 1, 'm', 'i4', 'failed', 'boom', true) $q$);
select expect_fail('분석 대상 없음', $q$ insert into analysis_runs (kind, model, input_hash, status, result) values ('classify', 'm', 'i5', 'succeeded', '{}') $q$);

-- 3단계: 출처 연결, 이벤트
select expect_eq('출처 없는 기술 뷰(연결 전)', 'select count(*) from technologies_without_source', 5);
insert into technology_sources (technology_id, source_item_id, evidence_type) values (1, 1, 'official');
select expect_eq('출처 없는 기술 뷰(연결 후)', 'select count(*) from technologies_without_source', 4);
insert into technology_events (technology_id, event_type, occurred_at, evidence_key) values (1, 'release', '2026-11-04', 'v1.4.9');
select expect_fail('중복 이벤트', $q$ insert into technology_events (technology_id, event_type, occurred_at, evidence_key) values (1, 'release', '2026-11-04', 'v1.4.9') $q$);
select expect_ok('같은 날 다른 태그의 이벤트', $q$ insert into technology_events (technology_id, event_type, occurred_at, evidence_key) values (1, 'release', '2026-11-04', 'core-1.7.6') $q$);
select expect_fail('이벤트 수정 금지', $q$ update technology_events set summary = 'x' $q$);
select expect_fail('이벤트 삭제 금지', $q$ delete from technology_events $q$);
select expect_fail('잘못된 중요도', $q$ insert into user_knowledge (user_id, technology_id, importance) values ('00000000-0000-0000-0000-000000000001', 1, 9) $q$);
insert into user_knowledge (user_id, technology_id) values ('00000000-0000-0000-0000-000000000001', 1);
select expect_fail('사용자별 기술 상태 중복', $q$ insert into user_knowledge (user_id, technology_id) values ('00000000-0000-0000-0000-000000000001', 1) $q$);

-- 4단계: 관계 매트릭스, 방향, 근거
select expect_ok('EXTENDS 라이브러리 → 제품', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 2, 'EXTENDS') $q$);
select expect_ok('IMPLEMENTS 라이브러리 → 표준', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 3, 'IMPLEMENTS') $q$);
select expect_fail('IMPLEMENTS 대상이 표준이 아님', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 2, 'IMPLEMENTS') $q$);
select expect_fail('유형 없는(보류) 기술과의 관계', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 4, 'RELATED_TO') $q$);
select expect_fail('중복 관계', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 2, 'EXTENDS') $q$);
select expect_fail('자기 자신과의 관계', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 1, 'RELATED_TO') $q$);
select expect_fail('대칭 관계는 id가 작은 쪽이 source', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (2, 1, 'RELATED_TO') $q$);
select expect_ok('대칭 관계 정방향', $q$ insert into technology_relations (source_technology_id, target_technology_id, relation_type) values (1, 2, 'RELATED_TO') $q$);
select expect_eq('근거 없는 관계 뷰(연결 전)', 'select count(*) from relations_without_evidence', 3);
insert into relation_evidence select id, 1 from technology_relations;
select expect_eq('근거 없는 관계 뷰(연결 후)', 'select count(*) from relations_without_evidence', 0);

-- 5단계: 브리핑 중복 방지
insert into briefings (user_id, kind, period_start) values ('00000000-0000-0000-0000-000000000001', 'daily', '2026-11-05');
select expect_fail('같은 기간의 브리핑 중복', $q$ insert into briefings (user_id, kind, period_start) values ('00000000-0000-0000-0000-000000000001', 'daily', '2026-11-05') $q$);
insert into briefing_items (briefing_id, user_id, kind, section, event_id, source_url)
  values (1, '00000000-0000-0000-0000-000000000001', 'daily', 'updates', 1, 'https://a.test/1');
select expect_fail('같은 변화를 같은 종류 브리핑에 다시 실음', $q$ insert into briefing_items (briefing_id, user_id, kind, section, event_id, source_url) values (1, '00000000-0000-0000-0000-000000000001', 'daily', 'updates', 1, 'https://a.test/1') $q$);
select expect_ok('같은 기술의 새 변화는 다시 실음', $q$ insert into briefing_items (briefing_id, user_id, kind, section, event_id, source_url) values (1, '00000000-0000-0000-0000-000000000001', 'daily', 'updates', (select id from technology_events where evidence_key = 'core-1.7.6'), 'https://a.test/1') $q$);
select expect_fail('변화 없이 항목만 있음', $q$ insert into briefing_items (briefing_id, user_id, kind, section, source_url) values (1, '00000000-0000-0000-0000-000000000001', 'daily', 'updates', 'u') $q$);

-- 보안: 정책 없이 모든 테이블에 RLS가 켜져 있어야 한다
select expect_eq('RLS가 꺼진 테이블', $q$ select count(*) from pg_tables where schemaname = current_schema() and tablename <> 'check_results' and not rowsecurity $q$, 0);

select (select count(*) from check_results where outcome like 'PASS%') as pass,
       (select count(*) from check_results where outcome not like 'PASS%') as fail,
       (select coalesce(string_agg(name || ' → ' || outcome, E'\n'), '') from check_results where outcome not like 'PASS%') as failures;
