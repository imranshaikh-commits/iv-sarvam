-- Sprint 3.4: what generating each proposal cost.
-- {"calls", "prompt_tokens", "completion_tokens", "cost_usd"}, written by the
-- brain after the proposal row (supabase_client.set_proposal_usage). Nullable
-- and additive: rows written before this migration simply have no figure.
alter table public.generated_proposals add column if not exists llm_usage jsonb;
