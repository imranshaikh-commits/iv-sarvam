-- sarvam_022: a new Supabase auth user no longer joins the organisation by itself.
--
-- Deep audit 2026-10-12, finding F02. NOT a no-op: it replaces a function that
-- exists in production. Apply only with Imran's explicit yes.
--
-- handle_new_user (001_init) runs on every insert into auth.users and added
-- the new user to the Inspirit Vision organisation as a member. Every RLS
-- policy on the corpus is "is a member of the organisation", so an account
-- was the same thing as access to the whole proposal bank. Nobody signs in
-- through Supabase today (auth.users and org_members are empty; identity
-- comes from Open WebUI), which is why nothing was exposed. The moment
-- Supabase Auth is used, or sign-ups are left on, it would be.
--
-- The profile row is still created. Membership becomes a deliberate act:
--   insert into public.org_members (org_id, user_id, role) values (..., ..., 'member');
--
-- CREATE OR REPLACE keeps the function's owner and privileges, so the
-- revocation in sarvam_018 (nobody may call it through /rest/v1/rpc) stands.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    default_org uuid;
begin
    select id into default_org from organizations where slug = 'inspirit-vision' limit 1;

    insert into profiles (user_id, display_name, default_org_id)
    values (new.id, new.email, default_org);

    return new;
end;
$$;
