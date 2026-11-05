-- Tech Radar Agent DDL v1.1 (PostgreSQL, Supabase 전제)
-- 근거: docs/PRD.md, docs/adr/0001, docs/adr/0002. 검증: docs/ddl/v1.1-checks.sql
create extension if not exists pg_trgm with schema extensions;

create type technology_type as enum ('PRODUCT', 'FRAMEWORK_LIBRARY', 'PROTOCOL_STANDARD', 'MODEL', 'PATTERN_CONCEPT', 'RESEARCH');
create type maturity as enum ('experimental', 'emerging', 'stable', 'mature');
create type lifecycle_status as enum ('active', 'maintenance', 'deprecated', 'end_of_life', 'unknown');
create type confidence as enum ('Verified', 'Corroborated', 'Unverified', 'Disputed');
create type relation_type as enum ('DERIVED_FROM', 'BUILT_ON', 'EXTENDS', 'IMPLEMENTS', 'PART_OF', 'REPLACES', 'COMPETES_WITH', 'INTEGRATES_WITH', 'RELATED_TO');
create type job_status as enum ('queued', 'running', 'succeeded', 'failed');
create type knowledge_state as enum ('unread', 'saved', 'learned');
create type date_precision as enum ('day', 'month', 'year');

-- 사용자 (MVP는 1명이지만 처음부터 user_id를 둔다. Supabase Auth에는 묶지 않는다)
create table users (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now()
);

-- 1단계: 수집
create table sources (
  id bigint generated always as identity primary key,
  name text not null unique,
  kind text not null check (kind in ('github', 'hackernews', 'rss')),
  config jsonb not null default '{}',
  collect_interval interval not null default '1 hour',
  is_active boolean not null default true
);

create table jobs (
  id bigint generated always as identity primary key,
  kind text not null,
  payload jsonb not null default '{}',
  status job_status not null default 'queued',
  attempts int not null default 0,
  max_attempts int not null default 3,
  run_after timestamptz not null default now(),
  locked_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create index jobs_ready_idx on jobs (run_after) where status = 'queued';

create table collection_runs (
  id bigint generated always as identity primary key,
  source_id bigint not null references sources,
  job_id bigint references jobs,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status job_status not null default 'running',
  requests_total int not null default 0,
  requests_failed int not null default 0,
  error text
);
create index collection_runs_source_idx on collection_runs (source_id, started_at desc);

-- 중복 방지 키 세 개는 각각 별도 제약이다(S1-04). 본문 전문은 저장하지 않으므로 발췌 길이를 막는다(S1-06).
create table source_items (
  id bigint generated always as identity primary key,
  source_id bigint not null references sources,
  external_id text,
  url text not null,
  normalized_url text not null,
  title text not null,
  excerpt text not null default '' check (length(excerpt) <= 500),
  content_hash text not null,
  metadata jsonb not null default '{}',
  published_at timestamptz,
  first_discovered_at timestamptz not null default now(),
  collected_at timestamptz not null default now(),
  constraint source_items_external_uq unique (source_id, external_id),
  constraint source_items_url_uq unique (normalized_url),
  constraint source_items_hash_uq unique (content_hash)
);

-- 2단계: 기술 식별
create table technologies (
  id bigint generated always as identity primary key,
  canonical_name text not null,
  type technology_type,  -- null이면 분류 보류(ADR-0001)
  maturity maturity,
  lifecycle_status lifecycle_status not null default 'unknown',
  archived_at timestamptz,
  first_published_at timestamptz,  -- technology_events에서 파생한 캐시. 원천은 이벤트다
  created_at timestamptz not null default now()
);
create unique index technologies_name_uq on technologies (lower(canonical_name)) where archived_at is null;

create table technology_aliases (
  id bigint generated always as identity primary key,
  technology_id bigint not null references technologies,
  alias text not null,
  lang text not null check (lang in ('ko', 'en', 'other')),
  unique (technology_id, alias)
);
create index technology_aliases_lower_idx on technology_aliases (lower(alias));
create index technology_aliases_trgm_idx on technology_aliases using gin (alias gin_trgm_ops);

-- AI 분석은 덮어쓰지 않고 쌓고, 현재 값만 is_current로 표시한다(S2-05)
create table analysis_runs (
  id bigint generated always as identity primary key,
  kind text not null check (kind in ('classify', 'match', 'profile', 'relation')),
  source_item_id bigint references source_items,
  technology_id bigint references technologies,
  model text not null,
  input_hash text not null,
  status text not null check (status in ('succeeded', 'failed')),
  result jsonb,
  error text,
  retryable boolean not null default false,
  tokens_in int,
  tokens_out int,
  cost_usd numeric(10, 6),
  is_current boolean not null default false,
  created_at timestamptz not null default now(),
  check (source_item_id is not null or technology_id is not null),
  check (status = 'failed' or result is not null),
  check (not is_current or status = 'succeeded')
);
create unique index analysis_runs_current_uq
  on analysis_runs (kind, coalesce(source_item_id, 0), coalesce(technology_id, 0)) where is_current;

create table technology_sources (
  technology_id bigint not null references technologies,
  source_item_id bigint not null references source_items,
  evidence_type text not null check (evidence_type in ('official', 'release', 'announcement', 'analysis', 'mention')),
  confidence confidence not null default 'Unverified',
  created_at timestamptz not null default now(),
  primary key (technology_id, source_item_id, evidence_type)
);
create index technology_sources_item_idx on technology_sources (source_item_id);

-- 3단계: 지식 데이터베이스. 프로필은 필드별 주장(claim) 행이고, 행이 없는 필드는 화면에서 "미확인"이다(S3-01)
create table technology_claims (
  id bigint generated always as identity primary key,
  technology_id bigint not null references technologies,
  field text not null check (field in ('one_liner', 'description', 'problem', 'features', 'differences', 'use_cases', 'pros_cons', 'official_source')),
  content text not null,
  confidence confidence not null default 'Unverified',
  analysis_run_id bigint references analysis_runs,
  is_current boolean not null default true,
  created_at timestamptz not null default now()
);
create index technology_claims_current_idx on technology_claims (technology_id, field) where is_current;

create table claim_evidence (
  claim_id bigint not null references technology_claims,
  source_item_id bigint not null references source_items,
  primary key (claim_id, source_item_id)
);

-- 이벤트는 고쳐 쓰지 않고 쌓는다(S3-04). 값 목록은 초안이라 text+CHECK로 두고 바꿀 때는 마이그레이션한다
create table technology_events (
  id bigint generated always as identity primary key,
  technology_id bigint not null references technologies,
  event_type text not null check (event_type in ('first_published', 'release', 'acquisition', 'integration', 'deprecation', 'end_of_life')),
  occurred_at timestamptz not null,
  date_precision date_precision not null default 'day',
  evidence_key text not null,  -- 릴리스 태그나 공지 URL
  source_item_id bigint references source_items,
  summary text,
  created_at timestamptz not null default now(),
  unique (technology_id, event_type, occurred_at, evidence_key)
);
create index technology_events_timeline_idx on technology_events (technology_id, occurred_at desc);

create function forbid_change() returns trigger language plpgsql as $$
begin
  raise exception '% on % is not allowed: rows are append-only', tg_op, tg_table_name;
end $$;
create trigger technology_events_append_only before update or delete on technology_events
  for each row execute function forbid_change();

-- 사용자별 데이터. AI가 만든 지식(claims)과 개인 메모는 테이블을 나눈다(S3-05)
create table user_knowledge (
  user_id uuid not null references users,
  technology_id bigint not null references technologies,
  state knowledge_state not null default 'unread',
  importance smallint check (importance between 1 and 5),
  updated_at timestamptz not null default now(),
  primary key (user_id, technology_id)
);

create table user_notes (
  id bigint generated always as identity primary key,
  user_id uuid not null references users,
  technology_id bigint not null references technologies,
  body text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index user_notes_idx on user_notes (user_id, technology_id);

-- 4단계: 관계. 허용 매트릭스는 ADR-0002를 그대로 옮긴 것이다
create table relation_type_rules (
  relation_type relation_type not null,
  source_type technology_type not null,
  target_type technology_type not null,
  primary key (relation_type, source_type, target_type)
);

insert into relation_type_rules (relation_type, source_type, target_type)
with t(type) as (values ('PRODUCT'::technology_type), ('FRAMEWORK_LIBRARY'), ('PROTOCOL_STANDARD'), ('MODEL')),
     dev(type) as (values ('PRODUCT'::technology_type), ('FRAMEWORK_LIBRARY'))
select 'IMPLEMENTS'::relation_type, s.type, 'PROTOCOL_STANDARD'::technology_type from dev s
union all select 'EXTENDS', s.type, d.type from dev s, dev d
union all select 'BUILT_ON', s.type, d.type from dev s, t d where d.type in ('FRAMEWORK_LIBRARY', 'MODEL')
union all select r.v::relation_type, x.type, x.type from t x, (values ('DERIVED_FROM'), ('REPLACES'), ('PART_OF'), ('COMPETES_WITH')) r(v)
union all select 'PART_OF', 'FRAMEWORK_LIBRARY', 'PRODUCT'
union all select 'COMPETES_WITH', 'PRODUCT', 'FRAMEWORK_LIBRARY'
union all select 'COMPETES_WITH', 'FRAMEWORK_LIBRARY', 'PRODUCT'
union all select r.v::relation_type, s.type, d.type from t s, t d, (values ('INTEGRATES_WITH'), ('RELATED_TO')) r(v);

-- 대칭 관계는 id가 작은 쪽을 source로 한 줄만 저장한다(S4-02)
create table technology_relations (
  id bigint generated always as identity primary key,
  source_technology_id bigint not null references technologies,
  target_technology_id bigint not null references technologies,
  relation_type relation_type not null,
  confidence confidence not null default 'Unverified',
  user_verified boolean not null default false,
  analysis_run_id bigint references analysis_runs,
  created_at timestamptz not null default now(),
  unique (source_technology_id, target_technology_id, relation_type),
  check (source_technology_id <> target_technology_id),
  check (relation_type not in ('COMPETES_WITH', 'INTEGRATES_WITH', 'RELATED_TO') or source_technology_id < target_technology_id)
);

create function check_relation_matrix() returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from technologies s, technologies t, relation_type_rules r
    where s.id = new.source_technology_id and t.id = new.target_technology_id
      and r.relation_type = new.relation_type and r.source_type = s.type and r.target_type = t.type
  ) then
    raise exception 'relation % between technologies % and % violates the type matrix (ADR-0002)',
      new.relation_type, new.source_technology_id, new.target_technology_id;
  end if;
  return new;
end $$;
create trigger technology_relations_matrix before insert or update on technology_relations
  for each row execute function check_relation_matrix();

create table relation_evidence (
  relation_id bigint not null references technology_relations,
  source_item_id bigint not null references source_items,
  primary key (relation_id, source_item_id)
);

-- 5단계: 브리핑. 중복 판정은 기술이 아니라 변화(이벤트 또는 수집 항목) 단위다(S5-04)
create table briefings (
  id bigint generated always as identity primary key,
  user_id uuid not null references users,
  kind text not null check (kind in ('daily', 'weekly')),
  period_start date not null,
  status job_status not null default 'queued',
  error text,
  created_at timestamptz not null default now(),
  unique (user_id, kind, period_start)
);

create table briefing_items (
  id bigint generated always as identity primary key,
  briefing_id bigint not null references briefings,
  user_id uuid not null references users,
  kind text not null check (kind in ('daily', 'weekly')),  -- 중복 방지 인덱스에 쓰려고 briefings.kind를 복사해 둔다
  section text not null,
  event_id bigint references technology_events,
  source_item_id bigint references source_items,
  source_url text not null,  -- 모든 항목에 원본 출처 링크가 있어야 한다
  check ((event_id is null) <> (source_item_id is null))
);
create unique index briefing_items_event_uq on briefing_items (user_id, kind, event_id) where event_id is not null;
create unique index briefing_items_item_uq on briefing_items (user_id, kind, source_item_id) where source_item_id is not null;

-- 게이트 점검용 뷰. security_invoker로 RLS를 우회하지 않게 한다
create view pending_classification with (security_invoker = true) as
  select * from technologies where type is null and archived_at is null;
create view technologies_without_source with (security_invoker = true) as
  select t.* from technologies t where not exists (select 1 from technology_sources s where s.technology_id = t.id);
create view relations_without_evidence with (security_invoker = true) as
  select r.* from technology_relations r where not exists (select 1 from relation_evidence e where e.relation_id = r.id);

-- 정책 없이 RLS만 켜서 클라이언트 키로는 읽고 쓸 수 없게 한다. 서버 연결은 영향을 받지 않는다
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = current_schema() loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;
