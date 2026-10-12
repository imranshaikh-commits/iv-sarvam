-- sarvam_021: a database rebuilt from these files matches production.
--
-- Deep audit 2026-10-12, findings F01 and F60. Production was built partly by
-- hand, so three things the files would produce on a clean database are not
-- what is live. Every statement here is a no-op in production (checked against
-- the live catalog on 2026-10-12: one match_proposal_chunks overload, six
-- private buckets). It matters for a restore into a fresh project.

-- F01. 001_init created match_proposal_chunks with a vector signature as
-- SECURITY DEFINER, with a NULL organisation filter and the default EXECUTE
-- grant to PUBLIC. Later migrations replaced the TEXT-signature function and
-- never dropped this one, so on a rebuilt database `anon` could read every
-- chunk through it, past Row Level Security. Nothing calls it.
drop function if exists public.match_proposal_chunks(
    vector, double precision, integer, uuid, text, text, text, text);

-- The six-argument function from sarvam_008 is not dropped by 010-012 either
-- (they drop and recreate the eight-argument one). It is SECURITY INVOKER, so
-- not an exposure, but production has one overload and a rebuild should too.
drop function if exists public.match_proposal_chunks(
    text, integer, integer, text, integer, integer);

-- F60. 001_init says these four buckets "cannot be created via SQL" and leaves
-- them to the dashboard, so a rebuilt project failed at its first diagram or
-- draft upload. They can be: sarvam_013 and sarvam_017 create theirs this way.
-- Private, no size or type limit, as in production. Only the brain's
-- service-role key reads or writes them, so no storage policies are added.
insert into storage.buckets (id, name, public) values
    ('source-proposals', 'source-proposals', false),
    ('proposal-images',  'proposal-images',  false),
    ('generated-drafts', 'generated-drafts', false),
    ('diagram-renders',  'diagram-renders',  false)
on conflict (id) do nothing;
