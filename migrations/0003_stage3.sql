-- 3단계: 지식 데이터베이스(SPEC v1.5). DDL v1.1의 3단계 부분을 옮기고, 수동 등록 소스와 사용자 한 명을 넣는다
create type knowledge_state as enum ('unread', 'saved', 'learned');
create type date_precision as enum ('day', 'month', 'year');

-- MVP는 1명이지만 처음부터 user_id를 둔다. Supabase Auth에는 묶지 않는다(ERD 가정 8)
create table users (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now()
);
insert into users default values;

-- 수동 등록(S3-06)의 출처. 비활성이라 수집 스케줄에 잡히지 않는다
alter table sources drop constraint sources_kind_check;
alter table sources add constraint sources_kind_check check (kind in ('github', 'hackernews', 'rss', 'manual'));
insert into sources (name, kind, is_active) values ('manual', 'manual', false);

-- 유형이 빈 기술을 넓힌 유형 목록으로 다시 묻는 호출(S3-09)
alter table analysis_runs drop constraint analysis_runs_kind_check;
alter table analysis_runs add constraint analysis_runs_kind_check check (kind in ('classify', 'match', 'profile', 'relation', 'retype'));

-- 프로필은 필드별 주장(claim) 행이고, 행이 없는 필드는 화면에서 "미확인"이다(S3-01)
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

-- 이벤트는 고쳐 쓰지 않고 쌓는다(S3-04)
create table technology_events (
  id bigint generated always as identity primary key,
  technology_id bigint not null references technologies,
  event_type text not null check (event_type in ('first_published', 'release', 'acquisition', 'integration', 'deprecation', 'end_of_life')),
  occurred_at timestamptz not null,
  date_precision date_precision not null default 'day',
  evidence_key text not null,  -- 릴리스 태그나 저장소 URL
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

alter table users enable row level security;
alter table technology_claims enable row level security;
alter table claim_evidence enable row level security;
alter table technology_events enable row level security;
alter table user_knowledge enable row level security;
alter table user_notes enable row level security;
