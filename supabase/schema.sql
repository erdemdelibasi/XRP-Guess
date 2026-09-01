-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- to create the tables this app needs.

create table if not exists predictions (
  id                    bigint generated always as identity primary key,
  created_at            timestamptz not null default now(),
  target_time           timestamptz,
  symbol                text not null default 'XRPUSDT',
  price_at_prediction   numeric not null,
  predicted_direction   text not null check (predicted_direction in ('UP', 'DOWN')),
  confidence            numeric not null,
  predicted_pct_change  numeric,
  predicted_price       numeric,

  tech_direction        text,
  tech_confidence       numeric,
  tech_pct_change       numeric,
  tech_price            numeric,
  ml_direction          text,
  ml_confidence         numeric,
  ml_pct_change         numeric,
  ml_price              numeric,
  weight_technical      numeric,
  weight_ml             numeric,
  model_version         text,

  resolved_at           timestamptz,
  price_at_resolution   numeric,
  actual_direction      text check (actual_direction in ('UP', 'DOWN')),
  correct               boolean,
  tech_correct          boolean,
  ml_correct            boolean
);

create index if not exists predictions_created_at_idx on predictions (created_at desc);
create index if not exists predictions_target_time_idx on predictions (target_time);
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

-- Virtual $1000 paper-trading portfolio: simulates automatically buying/
-- selling XRP based on the ensemble prediction's direction, no real money or
-- Binance account involved.
create table if not exists portfolio_state (
  id                     int primary key default 1,
  cash_usd               numeric not null default 1000,
  xrp_amount             numeric not null default 0,
  position               text not null default 'CASH' check (position in ('CASH', 'LONG')),
  updated_at             timestamptz not null default now(),
  check (id = 1)
);

insert into portfolio_state (id) values (1) on conflict (id) do nothing;

create table if not exists trades (
  id                          bigint generated always as identity primary key,
  created_at                  timestamptz not null default now(),
  side                        text not null check (side in ('BUY', 'SELL')),
  price                       numeric not null,
  xrp_amount                  numeric not null,
  usd_amount                  numeric not null,
  fee_usd                     numeric not null,
  cash_after                  numeric not null,
  xrp_after                   numeric not null,
  triggered_by_prediction_id  bigint references predictions(id),
  reason                      text
);

create index if not exists trades_created_at_idx on trades (created_at desc);

-- Row Level Security: the frontend uses the public "anon" key and must only
-- ever be able to read data. All writes come from the backend, which uses the
-- service_role key (bypasses RLS) via GitHub Actions secrets.
alter table predictions enable row level security;
alter table model_state enable row level security;
alter table portfolio_state enable row level security;
alter table trades enable row level security;

create policy "public read predictions" on predictions
  for select using (true);

create policy "public read model_state" on model_state
  for select using (true);

create policy "public read portfolio_state" on portfolio_state
  for select using (true);

create policy "public read trades" on trades
  for select using (true);
