-- 4단계: 관계(SPEC v1.7). DDL v1.1의 4단계 부분에 직접 잇기·숨김·근거 문장·검토 목록을 더한다
create type relation_type as enum ('DERIVED_FROM', 'BUILT_ON', 'EXTENDS', 'IMPLEMENTS', 'PART_OF', 'REPLACES', 'COMPETES_WITH', 'INTEGRATES_WITH', 'RELATED_TO');

-- 허용 매트릭스는 ADR-0002를 그대로 옮긴 것이다(61행)
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

-- 대칭 관계는 id가 작은 쪽을 source로 한 줄만 저장한다(S4-02). 직접 이은 관계는 사용자가 근거다(2026-11-09 결정)
create table technology_relations (
  id bigint generated always as identity primary key,
  source_technology_id bigint not null references technologies,
  target_technology_id bigint not null references technologies,
  relation_type relation_type not null,
  origin text not null default 'ai' check (origin in ('ai', 'manual')),
  confidence confidence not null default 'Unverified',
  user_verified boolean not null default false,
  hidden_at timestamptz,
  analysis_run_id bigint references analysis_runs,
  created_at timestamptz not null default now(),
  unique (source_technology_id, target_technology_id, relation_type),
  check (source_technology_id <> target_technology_id),
  check (relation_type not in ('COMPETES_WITH', 'INTEGRATES_WITH', 'RELATED_TO') or source_technology_id < target_technology_id)
);
create index technology_relations_target_idx on technology_relations (target_technology_id);

-- search_path를 만들 때의 값으로 고정한다(보안 어드바이저 경고). 시험은 임시 스키마에 만들므로 public으로 못 박지 않는다
create function check_relation_matrix() returns trigger language plpgsql set search_path from current as $$
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
create trigger technology_relations_matrix before insert or update of source_technology_id, target_technology_id, relation_type
  on technology_relations for each row execute function check_relation_matrix();

-- 근거 문장은 페이지 원문에서 그대로 옮긴 짧은 발췌다(본문 전문을 저장하지 않는다)
create table relation_evidence (
  relation_id bigint not null references technology_relations,
  source_item_id bigint not null references source_items,
  quote text check (char_length(quote) <= 200),
  primary key (relation_id, source_item_id)
);
create index relation_evidence_item_idx on relation_evidence (source_item_id);

-- 저장하지 못한 AI 관계(검토 목록, S4-03)
create table relation_proposals (
  id bigint generated always as identity primary key,
  source_technology_id bigint not null references technologies,
  target_technology_id bigint not null references technologies,
  relation_type relation_type not null,
  reason text not null check (reason in ('matrix', 'untyped')),
  source_item_id bigint references source_items,
  quote text check (char_length(quote) <= 200),
  status text not null default 'open' check (status in ('open', 'accepted', 'discarded')),
  analysis_run_id bigint references analysis_runs,
  created_at timestamptz not null default now(),
  check (source_technology_id <> target_technology_id)
);
create unique index relation_proposals_open_idx on relation_proposals (source_technology_id, target_technology_id, relation_type) where status = 'open';

-- 근거 없는 AI 관계 점검(4단계 게이트). 숨긴 관계와 직접 이은 관계는 뺀다
create view relations_without_evidence with (security_invoker = true) as
  select r.* from technology_relations r
  where r.origin = 'ai' and r.hidden_at is null
    and not exists (select 1 from relation_evidence e where e.relation_id = r.id);

alter table relation_type_rules enable row level security;
alter table technology_relations enable row level security;
alter table relation_evidence enable row level security;
alter table relation_proposals enable row level security;
