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
  whale_direction       text,
  whale_confidence      numeric,
  whale_pct_change      numeric,
  whale_price           numeric,
  news_direction        text,
  news_confidence       numeric,
  news_pct_change       numeric,
  news_price            numeric,
  orderbook_direction   text,
  orderbook_confidence  numeric,
  orderbook_pct_change  numeric,
  orderbook_price       numeric,
  claude_direction      text,
  claude_confidence     numeric,
  claude_pct_change     numeric,
  claude_price          numeric,
  weight_technical      numeric,
  weight_ml             numeric,
  weight_whale          numeric,
  weight_news           numeric,
  weight_orderbook      numeric,
  weight_claude         numeric,
  model_version         text,
  -- trading.min_confidence_to_open_position() at insert time -- lets the
  -- frontend show "does this confidence clear the real trade-opening bar"
  -- without re-deriving that threshold's math in JS.
  trade_threshold       numeric,

  resolved_at           timestamptz,
  price_at_resolution   numeric,
  actual_direction      text check (actual_direction in ('UP', 'DOWN')),
  correct               boolean,
  tech_correct          boolean,
  ml_correct            boolean,
  whale_correct         boolean,
  news_correct          boolean,
  orderbook_correct     boolean,
  claude_correct        boolean
);

create index if not exists predictions_created_at_idx on predictions (created_at desc);
create index if not exists predictions_target_time_idx on predictions (target_time);
create index if not exists predictions_unresolved_idx on predictions (resolved_at) where resolved_at is null;

-- `weight` is display-only (the UI / daily mail "who matters how much" bar):
-- ensemble.combine() pools components in log-odds space on their measured
-- reliability, not on a weight -- see ensemble.py. `sample_size` is how many
-- resolved, non-abstained predictions `rolling_accuracy` is computed from;
-- combine() needs it to know how much evidence is behind an accuracy (an
-- accuracy alone can't distinguish 6/10 from 600/1000).
create table if not exists model_state (
  component        text primary key check (component in ('technical', 'ml', 'whale', 'news', 'orderbook', 'claude')),
  weight           numeric not null default 0.2,
  rolling_accuracy numeric,
  sample_size      int not null default 0,
  updated_at       timestamptz not null default now()
);

-- Migration for an existing database (safe to re-run):
--   alter table model_state add column if not exists sample_size int not null default 0;

insert into model_state (component, weight) values
  ('technical', 0.24),
  ('ml', 0.24),
  ('whale', 0.12),
  ('news', 0.10),
  ('orderbook', 0.15),
  ('claude', 0.15)
on conflict (component) do nothing;

-- Virtual $1000 paper-trading portfolio: simulates automatically buying/
-- selling XRP based on the ensemble prediction's direction, no real money or
-- Binance account involved.
create table if not exists portfolio_state (
  id                     int primary key default 1,
  cash_usd               numeric not null default 1000,
  xrp_amount             numeric not null default 0,
  position               text not null default 'CASH' check (position in ('CASH', 'LONG')),
  peak_value             numeric not null default 1000,
  -- Candles left before a new position can be opened after a stop-loss (see
  -- trading.py:STOP_LOSS_COOLDOWN_CANDLES) -- without this, re-entering right
  -- after a stop-loss almost always re-triggers it on the very next candle.
  stop_loss_cooldown     int not null default 0,
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

-- Five more $1000 paper portfolios, one per individual signal (technical-only,
-- ml-only, whale-only, news-only, claude-only), independent of the weighted
-- ensemble portfolio above -- lets us compare a single-signal strategy's real
-- performance against the ensemble instead of only ever seeing them blended
-- together. orderbook isn't included here -- see CLAUDE.md for why the user
-- scoped this to just these five. Same shape as portfolio_state/trades so
-- trading.py can reuse the exact same compute_rebalance()-driven logic, just
-- routed to these tables via a `strategy` argument instead of a fixed id=1.
create table if not exists strategy_portfolios (
  strategy               text primary key check (strategy in ('technical', 'ml', 'whale', 'news', 'claude')),
  cash_usd               numeric not null default 1000,
  xrp_amount             numeric not null default 0,
  position               text not null default 'CASH' check (position in ('CASH', 'LONG')),
  peak_value             numeric not null default 1000,
  stop_loss_cooldown     int not null default 0,
  updated_at             timestamptz not null default now()
);

insert into strategy_portfolios (strategy) values ('technical'), ('ml'), ('whale'), ('news'), ('claude')
  on conflict (strategy) do nothing;

create table if not exists strategy_trades (
  id                          bigint generated always as identity primary key,
  strategy                    text not null check (strategy in ('technical', 'ml', 'whale', 'news', 'claude')),
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

create index if not exists strategy_trades_strategy_idx on strategy_trades (strategy, created_at desc);

-- Kanal Finans TŞ (YouTube @KanalFinans, Tunc Satiroglu) gunluk piyasa
-- videolarindan Claude ile cikarilan XRP/BTC/ETH/kripto bahisleri. Tamamen
-- bagimsiz bir bilgi akisi -- ensemble.COMPONENTS'e dahil DEGIL, sadece bu
-- kisinin videolarda ne soyledigini raporlar (bkz. CLAUDE.md). Bir video
-- ancak transkript+Claude cikarimi basariyla tamamlandiktan sonra
-- kanal_finans_videos'a yazilir (kripto bahsi hic yoksa bile, 0 mention'li
-- "islendi" satiri normaldir); herhangi bir adim basarisiz olursa video hic
-- yazilmaz ve bir sonraki kosuda otomatik tekrar denenir.
create table if not exists kanal_finans_videos (
  video_id          text primary key,
  video_title       text,
  published_at      timestamptz,
  processed_at      timestamptz not null default now(),
  transcript_found  boolean not null default false
);

-- Basarisiz olan videolarin tekrar-deneme sayaci. Yerel gorev 4 kez/gun
-- yerine 15 dakikada bir calistigi icin var: geri cekilme olmadan transkripti
-- IP-engelli bir video gunde 96 kez, zaten bizi reddeden endpoint'e
-- vurulurdu. Sadece basarisizken satiri olur -- video basariyla islenince
-- satir silinir (kanal_finans.py:clear_failures). Bekleme araligi
-- kanal_finans.py:RETRY_SCHEDULE'da.
create table if not exists kanal_finans_fetch_attempts (
  video_id          text primary key,
  attempts          int not null default 0,
  last_attempt_at   timestamptz not null default now(),
  last_error        text
);

create table if not exists kanal_finans_mentions (
  id                bigint generated always as identity primary key,
  created_at        timestamptz not null default now(),
  video_id          text not null references kanal_finans_videos (video_id),
  video_title       text,
  published_at      timestamptz,
  asset             text not null check (asset in ('XRP', 'BTC', 'ETH', 'KRIPTO')),
  summary           text not null,
  stance            text not null check (stance in ('UP', 'DOWN', 'NEUTRAL')),
  -- Only meaningful for asset='XRP' (the only asset kanal_finans_portfolio
  -- trades) -- see CLAUDE.md. 0 is the "not mentioned" sentinel for the two
  -- price columns (same nullable-unsupported json_schema workaround as
  -- claude_signal.py), not a real price.
  action            text check (action in ('BUY', 'SELL', 'HOLD')),
  stop_loss_price   numeric,
  resistance_price  numeric
);

create index if not exists kanal_finans_mentions_published_idx on kanal_finans_mentions (published_at desc);

-- Kanal Finans TS "takip portfoyu": kanal_finans_mentions'daki action/
-- stop_loss_price'i harfiyen uygulayan yedinci $1000 kagit-portfoy --
-- trading.compute_rebalance() DEGIL, kendi ikili (guven skorsuz) karar
-- motoru kanal_finans_trading.py'de yasiyor (bkz. CLAUDE.md).
create table if not exists kanal_finans_portfolio (
  id                int primary key default 1,
  cash_usd          numeric not null default 1000,
  xrp_amount        numeric not null default 0,
  position          text not null default 'CASH' check (position in ('CASH', 'LONG')),
  stop_loss_price   numeric,
  resistance_price  numeric,
  updated_at        timestamptz not null default now(),
  check (id = 1)
);

insert into kanal_finans_portfolio (id) values (1) on conflict (id) do nothing;

create table if not exists kanal_finans_trades (
  id                       bigint generated always as identity primary key,
  created_at               timestamptz not null default now(),
  side                     text not null check (side in ('BUY', 'SELL')),
  price                    numeric not null,
  xrp_amount               numeric not null,
  usd_amount               numeric not null,
  fee_usd                  numeric not null,
  cash_after               numeric not null,
  xrp_after                numeric not null,
  triggered_by_mention_id  bigint references kanal_finans_mentions(id),
  reason                   text
);

create index if not exists kanal_finans_trades_created_at_idx on kanal_finans_trades (created_at desc);

-- Sekizinci ($1000) kagit-portfoy: trend-takip / momentum stratejisi
-- (Donchian kirilim + EMA9/21 trend filtresi + trailing/hard stop).
-- trading.compute_rebalance() DEGIL -- o guven-olcekli surekli yeniden
-- dengeleme yapiyor (her yon degisiminde tepki verir, bkz. CLAUDE.md'deki
-- 2026-09-04 bulgusu: 19 saatte 11 tam gidis-donus, %6.8'lik ralliye ragmen
-- 6 stratejinin de zararda kapanmasi). Bu ise ikili bir durum makinesi:
-- kirilinca TAM gir, trend gercekten kirilana/stop'a carpana kadar TUT.
-- Karar motoru backend/momentum_trading.py'de yasiyor (kanal_finans_trading.py
-- ile ayni desen: kendi state'i, kendi tablosu, compute_rebalance()'a girmiyor).
--
-- BILEREK backfill EDILMEDI -- diger 5 tekil stratejinin aksine bu portfoy
-- $1000/0 islemle sifirdan baslar. Sebep: 208 gunluk kesif backtest'i bu
-- yaklasimin dogru yonde oldugunu ama sonucun 16 islemden SADECE BIRINE
-- (%51'lik tek bir olaganustu hareket) bagimli oldugunu gosterdi -- o islem
-- cikarilinca +%30,68 -%8,87'ye donuyordu. Gecmise dayali o kirilgan sayiyi
-- "gercek performans" gibi gostermek yaniltici olurdu; bu yuzden sadece
-- gercek, ileriye-donuk canli kararlar birikecek (bkz. momentum_trading.py
-- modul docstring'i).
create table if not exists momentum_portfolio (
  id                  int primary key default 1,
  cash_usd            numeric not null default 1000,
  xrp_amount          numeric not null default 0,
  position            text not null default 'FLAT' check (position in ('FLAT', 'LONG')),
  entry_price         numeric,
  peak_since_entry    numeric,
  peak_value          numeric not null default 1000,
  stop_loss_cooldown  int not null default 0,
  updated_at          timestamptz not null default now(),
  check (id = 1)
);

insert into momentum_portfolio (id) values (1) on conflict (id) do nothing;

create table if not exists momentum_trades (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  side        text not null check (side in ('BUY', 'SELL')),
  price       numeric not null,
  xrp_amount  numeric not null,
  usd_amount  numeric not null,
  fee_usd     numeric not null,
  cash_after  numeric not null,
  xrp_after   numeric not null,
  reason      text not null
);

create index if not exists momentum_trades_created_at_idx on momentum_trades (created_at desc);

-- Row Level Security: the frontend uses the public "anon" key and must only
-- ever be able to read data. All writes come from the backend, which uses the
-- service_role key (bypasses RLS) via GitHub Actions secrets.
alter table predictions enable row level security;
alter table model_state enable row level security;
alter table portfolio_state enable row level security;
alter table trades enable row level security;
alter table strategy_portfolios enable row level security;
alter table strategy_trades enable row level security;
alter table kanal_finans_videos enable row level security;
alter table kanal_finans_mentions enable row level security;
-- Bilerek "public read" policy'si YOK: bu tablo sadece backend'in tekrar-
-- deneme muhasebesi, frontend hic okumuyor. RLS acik + policy yok = anon
-- key ile okunamaz; backend service_role ile RLS'i zaten baypas ediyor.
alter table kanal_finans_fetch_attempts enable row level security;
alter table kanal_finans_portfolio enable row level security;
alter table kanal_finans_trades enable row level security;
alter table momentum_portfolio enable row level security;
alter table momentum_trades enable row level security;

create policy "public read predictions" on predictions
  for select using (true);

create policy "public read model_state" on model_state
  for select using (true);

create policy "public read portfolio_state" on portfolio_state
  for select using (true);

create policy "public read trades" on trades
  for select using (true);

create policy "public read strategy_portfolios" on strategy_portfolios
  for select using (true);

create policy "public read strategy_trades" on strategy_trades
  for select using (true);

create policy "public read kanal_finans_videos" on kanal_finans_videos
  for select using (true);

create policy "public read kanal_finans_mentions" on kanal_finans_mentions
  for select using (true);

create policy "public read kanal_finans_portfolio" on kanal_finans_portfolio
  for select using (true);

create policy "public read kanal_finans_trades" on kanal_finans_trades
  for select using (true);

create policy "public read momentum_portfolio" on momentum_portfolio
  for select using (true);

create policy "public read momentum_trades" on momentum_trades
  for select using (true);
