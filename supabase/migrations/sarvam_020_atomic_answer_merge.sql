-- Audit 2026-10-12, finding 9: intake answers were merged read-modify-write in
-- the brain, so two requests saving different answers at the same time each
-- read the old object and the later write dropped the other's key.
-- One UPDATE with jsonb || is atomic per row: both keys survive.
create or replace function public.merge_intake_answers(p_session uuid, p_partial jsonb)
returns setof public.intake_sessions
language sql
set search_path = public
as $$
  update public.intake_sessions s
     set answers       = coalesce(s.answers, '{}'::jsonb) || coalesce(p_partial, '{}'::jsonb),
         proposal_type = coalesce(p_partial->>'proposal_type', s.proposal_type),
         client_name   = coalesce(p_partial->>'client_name',   s.client_name),
         iam_vendor    = coalesce(p_partial->>'iam_vendor',    s.iam_vendor),
         updated_at    = now()
   where s.id = p_session
  returning s.*;
$$;

-- Callable by the brain's service role only, like the functions in sarvam_018.
revoke all on function public.merge_intake_answers(uuid, jsonb) from public, anon, authenticated;
grant execute on function public.merge_intake_answers(uuid, jsonb) to service_role;
