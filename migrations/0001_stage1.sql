-- 1단계 수집. docs/ddl/v1.1.sql의 job_status, sources, jobs, collection_runs, source_items를 옮긴 것이다.
create type job_status as enum ('queued', 'running', 'succeeded', 'failed');

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

-- 정책 없이 RLS만 켜서 클라이언트 키로는 읽고 쓸 수 없게 한다. 서버 연결은 영향을 받지 않는다
alter table sources enable row level security;
alter table jobs enable row level security;
alter table collection_runs enable row level security;
alter table source_items enable row level security;
