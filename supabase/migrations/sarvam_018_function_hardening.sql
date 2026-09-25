-- sarvam_018: close the Supabase security-advisor warnings that need no auth design.
--
-- Measured before this migration (2026-09-25, SET ROLE anon): every public
-- table returns 0 rows to the anon key except visual_assets (939 rows, RLS
-- off, policy decision pending - deliberately NOT touched here). auth.users is
-- empty, so every is_org_member() policy is false for everyone and the brain
-- works only through the service-role key. Making RLS load-bearing needs real
-- users (Phase 7.2); this migration only removes needless exposure.

-- Trigger function for auth.users inserts. Triggers do not check EXECUTE when
-- they fire, so nobody needs to be able to call it through /rest/v1/rpc.
revoke execute on function public.handle_new_user() from public, anon, authenticated;

-- Membership probe. Signed-in users need it for policy evaluation once Auth is
-- wired; the anon key never does (anon queries are denied either way).
revoke execute on function public.is_org_member(uuid) from public, anon;
grant execute on function public.is_org_member(uuid) to authenticated, service_role;

-- Pin search_path on the retrieval RPCs (advisor 0011). Same value the session
-- already uses, so resolution of tables and pgvector operators is unchanged.
alter function public.match_proposal_chunks(text, integer, integer, text, integer, integer, text, integer)
  set search_path = public, extensions, pg_temp;
alter function public.match_partner_product_chunks(text, integer, text, text, integer)
  set search_path = public, extensions, pg_temp;
