-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- to create the tables this app needs.

create table if not exists predictions (
  id                  bigint generated always as identity primary key,
  created_at          timestamptz not null default now(),
  symbol              text not null default 'XRPUSDT',
  price_at_prediction numeric not null,
  predicted_direction text not null check (predicted_direction in ('UP', 'DOWN')),
  confidence          numeric not null,

  tech_direction      text,
  tech_confidence     numeric,
  ml_direction        text,
  ml_confidence       numeric,
  weight_technical    numeric,
  weight_ml           numeric,
  model_version       text,

  resolved_at         timestamptz,
  price_at_resolution numeric,
  actual_direction    text check (actual_direction in ('UP', 'DOWN')),
  correct             boolean,
  tech_correct        boolean,
  ml_correct          boolean
);

create index if not exists predictions_created_at_idx on predictions (created_at desc);
create index if not exists predictions_unresolved_idx on predictions (resolved_at) where resolved_at is null;

create table if not exists model_state (
  component        text primary key check (component in ('technical', 'ml')),
  weight           numeric not null default 0.5,
  rolling_accuracy numeric,
  updated_at       timestamptz not null default now()
);

insert into model_state (component, weight) values
  ('technical', 0.5),
  ('ml', 0.5)
on conflict (component) do nothing;

-- Row Level Security: the frontend uses the public "anon" key and must only
-- ever be able to read data. All writes come from the backend, which uses the
-- service_role key (bypasses RLS) via GitHub Actions secrets.
alter table predictions enable row level security;
alter table model_state enable row level security;

create policy "public read predictions" on predictions
  for select using (true);

create policy "public read model_state" on model_state
  for select using (true);
