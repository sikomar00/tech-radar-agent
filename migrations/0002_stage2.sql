-- 2단계 기술 식별. docs/ddl/v1.1.sql의 enum 4개, technologies, technology_aliases, analysis_runs, technology_sources와 점검 뷰 2개를 옮긴 것이다.
-- pg_trgm은 Supabase의 extensions 스키마에 있다. 연결의 search_path에 extensions가 있어야 gin_trgm_ops와 similarity()가 보인다.
create extension if not exists pg_trgm with schema extensions;

create type technology_type as enum ('PRODUCT', 'FRAMEWORK_LIBRARY', 'PROTOCOL_STANDARD', 'MODEL', 'PATTERN_CONCEPT', 'RESEARCH');
create type maturity as enum ('experimental', 'emerging', 'stable', 'mature');
create type lifecycle_status as enum ('active', 'maintenance', 'deprecated', 'end_of_life', 'unknown');
create type confidence as enum ('Verified', 'Corroborated', 'Unverified', 'Disputed');

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

-- 게이트 점검용 뷰. security_invoker로 RLS를 우회하지 않게 한다
create view pending_classification with (security_invoker = true) as
  select * from technologies where type is null and archived_at is null;
create view technologies_without_source with (security_invoker = true) as
  select t.* from technologies t where not exists (select 1 from technology_sources s where s.technology_id = t.id);

alter table technologies enable row level security;
alter table technology_aliases enable row level security;
alter table analysis_runs enable row level security;
alter table technology_sources enable row level security;
