-- Migration: split Kanal Finans TS into a shared YouTube fetcher
-- (Kanal-Finans-Fetcher, also serving XAU-Guess) plus this project's own
-- trade-application step (see CLAUDE.md and kanal_finans.py's module
-- docstring for the full "why").
--
-- RUN THIS BEFORE deploying the new backend/kanal_finans.py: it queries
-- `applied_at`, and PostgREST rejects the whole query if the column doesn't
-- exist. One-time: Supabase SQL Editor -> New query -> paste -> Run.
-- Safe to run twice (every statement is idempotent).

alter table kanal_finans_mentions add column if not exists applied_at timestamptz;

create index if not exists kf_mentions_pending_idx
  on kanal_finans_mentions (applied_at) where applied_at is null;

-- Backfill: every row that exists already went through the OLD inline
-- pipeline, which applied a mention to the portfolio the instant it was
-- saved. Left NULL, the new slim kanal_finans.py would read all 45+ of them
-- as "pending" on its very first run and replay years of trades against
-- today's price -- backfilling to the row's own created_at (when it was
-- actually traded on) is what makes "pending" mean only genuinely-new rows
-- going forward.
update kanal_finans_mentions set applied_at = created_at where applied_at is null;
