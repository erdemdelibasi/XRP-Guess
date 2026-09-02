-- Migration: "Sadece Claude" 6. strateji için (bkz. CLAUDE.md / commit mesajı).
-- Bir kerelik: Supabase SQL Editor -> New query -> yapıştır -> Run.

alter table predictions
  add column if not exists claude_direction   text,
  add column if not exists claude_confidence  numeric,
  add column if not exists claude_pct_change  numeric,
  add column if not exists claude_price       numeric,
  add column if not exists claude_correct     boolean,
  add column if not exists weight_claude      numeric;

alter table model_state drop constraint if exists model_state_component_check;
alter table model_state add constraint model_state_component_check
  check (component in ('technical', 'ml', 'whale', 'news', 'orderbook', 'claude'));
insert into model_state (component, weight) values ('claude', 0.15)
  on conflict (component) do nothing;

alter table strategy_portfolios drop constraint if exists strategy_portfolios_strategy_check;
alter table strategy_portfolios add constraint strategy_portfolios_strategy_check
  check (strategy in ('technical', 'ml', 'whale', 'news', 'claude'));
insert into strategy_portfolios (strategy) values ('claude')
  on conflict (strategy) do nothing;

alter table strategy_trades drop constraint if exists strategy_trades_strategy_check;
alter table strategy_trades add constraint strategy_trades_strategy_check
  check (strategy in ('technical', 'ml', 'whale', 'news', 'claude'));
