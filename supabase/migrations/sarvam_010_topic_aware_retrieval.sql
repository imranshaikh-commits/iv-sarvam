-- =====================================================
-- sarvam_010 — reserve evidence slots by SECTION TOPIC as well as proposal type
--
-- Applied to the database in three steps (010, 010b, 010c). This file is the
-- FINAL state, plus the reasons, so the failures are not repeated.
--
-- WHAT THIS ADDS
-- --------------
-- sarvam_008 reserved slots for the proposal TYPE being drafted. This adds the
-- second axis: the section TOPIC. Drafting "Why SailPoint" should search IV's
-- vendor-positioning writing, not all 11,286 chunks.
--
-- Impossible before sarvam_009 and the classifier backfill: section labels were
-- 46% 'other', with 34 why_vendor and 16 company_profile chunks in the entire
-- corpus. After the backfill (scripts/classify_sections.py):
--
--     topic               chunks   proposals covered (of 114)
--     pricing                346        105
--     migration              325         93
--     company_profile        323         96
--     raci                   202         91
--     sizing                 200         51
--     why_vendor             121         73
--     similar_experience     112         94
--
-- MEASURED RESULT (retrieval scorecard, 20 probes, top_k=8)
-- ---------------------------------------------------------
--     mean_type_match     0.429 -> 0.714   (+0.285)
--     mean_topic_match        - -> 0.713   (new metric)
--     mean_recent_pct     0.775 -> 0.831
--     mean_fragment_pct   0.175 -> 0.163
--     proposals/probe      6.55 -> 6.40    (flagged; accepted, see below)
--     max_from_one            2 -> 2       (unchanged)
--
-- Type match went from WORSE THAN RANDOM (migration probes scored 0.25 against
-- a 35.5% base rate) to 0.714. The 0.15 diversity cost is accepted because
-- max_from_one held at 2: no single proposal dominates, which was the original
-- disease. The scorecard's 0.05 threshold is deliberately tight so a change has
-- to argue for itself.
--
-- KNOWN LIMITATION, MEASURED NOT ASSUMED
-- --------------------------------------
-- The why_vendor probe scored topic=0.125 despite an explicit filter and 121
-- matching chunks. The reservation draws from the top-120 ANN candidates, and
-- why_vendor is ~1% of the corpus, so the pool often contains almost none.
-- Same shape as the ef_search bug: a filter can only act on what the candidate
-- pool already holds. Small topics need a TOPIC-SCOPED query (two-tier
-- retrieval), not a filter over a global pool. Not yet built.
--
-- RESERVATION, NOT A HARD FILTER
-- ------------------------------
-- 29.9% of chunks are 'unclassified' -- mostly tables and diagrams headed with
-- the extractor's own numbering. A hard filter would make that content
-- unreachable; it is not junk, it simply has no heading worth reading.
--
-- The reservations compose: topic slots first (most specific), then
-- proposal-type slots, then pure similarity. Each stage claims only what the
-- previous one left, so the total is always match_count.
-- Set topic_reserve_pct or type_reserve_pct to 0 to disable either.
--
-- THREE TRAPS HIT WHILE BUILDING THIS
-- -----------------------------------
-- 1. (sarvam_007/008) Adding a parameter OVERLOADS rather than replaces. Both
--    signatures then exist and Postgres raises "function is not unique" on
--    every existing call. Drop the old signature explicitly.
-- 2. (sarvam_008)     SET LOCAL requires VOLATILE, and RETURNS TABLE column
--    names shadow CTE columns in PL/pgSQL unless #variable_conflict use_column.
-- 3. (010c)           CREATE OR REPLACE cannot change the row type. Adding
--    section_topic/proposal_type to the output needed DROP first -- in the SAME
--    migration, so there is no window with no function.
-- =====================================================

DROP FUNCTION IF EXISTS public.match_proposal_chunks(
    text, integer, integer, text, integer, integer, text, integer);

CREATE FUNCTION public.match_proposal_chunks(
    query_embedding text,
    match_count integer DEFAULT 8,
    max_per_proposal integer DEFAULT 3,
    filter_proposal_type text DEFAULT NULL,
    type_reserve_pct integer DEFAULT 60,
    ef_search integer DEFAULT 200,
    filter_section_topic text DEFAULT NULL,
    topic_reserve_pct integer DEFAULT 50
)
RETURNS TABLE(
    chunk_id uuid, proposal_id uuid, client_name text, iam_vendor text,
    industry text, heading text, chunk_text text, similarity double precision,
    section_topic text, proposal_type text
)
LANGUAGE plpgsql
VOLATILE                          -- required: SET LOCAL is illegal in STABLE
AS $function$
#variable_conflict use_column     -- RETURNS TABLE names shadow CTE columns
DECLARE
    topic_slots integer;
    type_slots  integer;
BEGIN
    -- The HNSW index returns at most hnsw.ef_search candidates (default 40)
    -- however large the LIMIT below. Transaction-scoped, so it cannot leak.
    EXECUTE format('SET LOCAL hnsw.ef_search = %s',
                   greatest(ef_search, match_count * 4));

    topic_slots := case
        when filter_section_topic is null or topic_reserve_pct <= 0 then 0
        else ceil(match_count * least(topic_reserve_pct, 100) / 100.0) end;
    type_slots := case
        when filter_proposal_type is null or type_reserve_pct <= 0 then 0
        else ceil(match_count * least(type_reserve_pct, 100) / 100.0) end;

    RETURN QUERY
    with candidates as (
        select c.id as chunk_id, c.proposal_id, p.client_name, p.iam_vendor,
               p.industry, c.heading, c.text as chunk_text,
               p.proposal_type, c.section_topic,
               1 - (c.embedding <=> query_embedding::vector) as similarity,
               md5(c.text) as text_hash
        from proposal_chunks c
        join proposals p on p.id = c.proposal_id
        where c.embedding is not null
        order by c.embedding <=> query_embedding::vector
        limit greatest(match_count * 12, 120)
    ),
    -- 42.9% of chunks are byte-identical duplicates: IV keeps document
    -- revisions and each repeats the unchanged bulk. Keep the closest.
    deduped as (
        select distinct on (text_hash) * from candidates
        order by text_hash, similarity desc
    ),
    diversified as (
        select *, row_number() over (
            partition by proposal_id order by similarity desc) as rn
        from deduped
    ),
    capped as (
        select * from diversified where rn <= greatest(max_per_proposal, 1)
    ),
    topic_hits as (
        select * from capped
        where filter_section_topic is not null
          and topic_slots > 0
          and section_topic = filter_section_topic
        order by similarity desc
        limit topic_slots
    ),
    type_hits as (
        select c.* from capped c
        where filter_proposal_type is not null
          and type_slots > 0
          and c.proposal_type = filter_proposal_type
          and not exists (select 1 from topic_hits t where t.chunk_id = c.chunk_id)
        order by c.similarity desc
        limit greatest(least(type_slots,
                             match_count - (select count(*) from topic_hits)), 0)
    ),
    -- Sized to exactly what the reservations left, so the final ordering cannot
    -- evict a reserved row. That was the bug in sarvam_008: a trailing
    -- `limit match_count` re-selected the global top-k and silently discarded
    -- every reserved row, because reserved rows score LOWER by construction.
    remainder as (
        select c.* from capped c
        where not exists (select 1 from topic_hits t where t.chunk_id = c.chunk_id)
          and not exists (select 1 from type_hits y where y.chunk_id = c.chunk_id)
        order by c.similarity desc
        limit greatest(match_count
                       - (select count(*) from topic_hits)
                       - (select count(*) from type_hits), 0)
    )
    select m.chunk_id, m.proposal_id, m.client_name, m.iam_vendor, m.industry,
           m.heading, m.chunk_text, m.similarity, m.section_topic, m.proposal_type
    from (select * from topic_hits
          union all select * from type_hits
          union all select * from remainder) m
    order by m.similarity desc;
END;
$function$;

-- =====================================================
-- END OF MIGRATION sarvam_010
-- =====================================================
