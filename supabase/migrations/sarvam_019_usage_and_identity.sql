-- Sprint 3.4: what generating each proposal cost.
-- {"calls", "prompt_tokens", "completion_tokens", "cost_usd"}, written by the
-- brain after the proposal row (supabase_client.set_proposal_usage).
alter table public.generated_proposals add column if not exists llm_usage jsonb;

-- Sprint 3.5: who did it. People sign in to Open WebUI, not Supabase auth, so
-- the existing created_by / approved_by uuid columns (FK auth.users) cannot
-- hold them. Open WebUI forwards the user's email; the brain stamps it here.
alter table public.generated_proposals   add column if not exists created_by_email text;
alter table public.intake_sessions       add column if not exists created_by_email text;
alter table public.architecture_diagrams add column if not exists approved_by_email text;
-- All additive and nullable: rows written before this migration have no value.
