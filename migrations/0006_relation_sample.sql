-- 관계 정확도 표본을 한 번 고정한다(4단계 게이트). 새 관계가 계속 생겨도 판정한 표본이 밀려나지 않게 한다
create table relation_sample (
  relation_id bigint primary key references technology_relations,
  position int not null unique,
  created_at timestamptz not null default now()
);
alter table relation_sample enable row level security;
