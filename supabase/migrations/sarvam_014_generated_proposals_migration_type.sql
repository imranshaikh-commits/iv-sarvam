-- =====================================================
-- sarvam_014 — allow `migration` in generated_proposals
--
-- sarvam_006 widened proposal_type on the CORPUS table (`proposals`) so
-- ingested migration proposals could be stored. It did not widen the same
-- constraint on `generated_proposals`, which is where a NEW proposal is
-- written.
--
-- The first migration proposal ever attempted (Bank BTPN, ForgeRock 6.5.x ->
-- 7.3) therefore failed at the very first insert. It surfaced to the user as
-- "I couldn't open a proposal record to attach the architecture to (the
-- database didn't respond)" -- a message that says nothing about the real
-- cause, because the error handler discarded the PostgREST response body. The
-- database had answered immediately and precisely:
--
--     new row violates check constraint
--     "generated_proposals_proposal_type_check"  (23514)
--
-- THIRD INSTANCE OF THE SAME SHAPE
-- --------------------------------
--   1. the intake offered `migration` while get_template("migration") raised
--      ValueError -- a consultant could answer all 22 areas and then crash
--   2. _SECTION_DISCOVERY_FIELDS kept the pre-rebuild section ids, so 11 of 12
--      sections were drafted with no discovery answers at all
--   3. this constraint kept the pre-migration type list
--
-- A value added at one end of the pipeline has to be accepted at every other
-- end, and only an end-to-end run finds the gap. Worth a startup check that
-- every proposal_type the intake offers has a template AND is accepted by both
-- tables.
--
-- Kept deliberately in step with proposals.proposals_proposal_type_check
-- (sarvam_006). If one changes, change the other.
-- =====================================================

ALTER TABLE generated_proposals
    DROP CONSTRAINT IF EXISTS generated_proposals_proposal_type_check;

ALTER TABLE generated_proposals
    ADD CONSTRAINT generated_proposals_proposal_type_check
    CHECK (proposal_type IS NULL OR proposal_type IN
           ('implementation', 'mss', 'migration', 'assessment', 'other'));

-- =====================================================
-- END OF MIGRATION sarvam_014
-- =====================================================
