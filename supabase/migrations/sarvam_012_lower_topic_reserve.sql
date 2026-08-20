-- =====================================================
-- sarvam_012 — lower the default topic reservation from 50% to 30%
--
-- MEASURED COST OF 50% (run 7 vs run 6, first run with topic-aware retrieval)
-- --------------------------------------------------------------------------
--     tables        19 -> 16
--     table words 2,355 -> 1,620      (IV's human original: 25 tables, 3,252)
--
-- Three tables disappeared, ALL sizing:
--     Proposed DR Hardware Sizing           7x2 -> gone
--     Proposed UAT Hardware Sizing          8x3 -> gone
--     Proposed Development Hardware Sizing  6x3 -> gone
-- and every surviving table lost rows: RACI 20->13, Deliverables 16->12,
-- Licence BOQ 6->4, Payment Milestones 8->6.
--
-- Cause: `sizing` covers only 51 of 114 proposals, the thinnest mapped topic.
-- Four sizing subsections each reserved HALF their evidence from that narrow
-- pool. Production sizing is the most common sizing content in the corpus, so
-- it won every reservation and DR/UAT/Dev were crowded out by more production
-- sizing -- leaving the model nothing distinct to tabulate. The row shrinkage
-- everywhere has the same cause: half the slots on one topic means less varied
-- evidence, so shorter tables.
--
-- WHY 30 AND NOT 0
-- ----------------
-- The reservation is doing real good elsewhere. Run 7's Company Profile and
-- Similar Experience came alive for the first time -- specific IV facts (Pune
-- delivery centre) and a real GOSI case study with volumetrics -- because the
-- topic-scoped query (sarvam_011) reaches sections that are 1-3% of the corpus.
-- Turning the reservation off would lose that.
--
-- 30% reserves 2-3 slots of 8 rather than 4. The parameter is per-call, so
-- drafting can override it per section later if a finer answer is wanted.
--
-- WHAT THE SCORECARD MISSED
-- -------------------------
-- The retrieval scorecard measured relevance (type_match 0.429 -> 0.714) and
-- APPROVED the change that caused this. It did not measure table richness,
-- which is what IV proposals are made of. A metric that does not measure the
-- thing you care about will happily approve a regression in it. A
-- `tabular_pct` metric was added to scripts/eval_retrieval.py in response.
--
-- NOTE: CREATE OR REPLACE cannot change a function's default AND leave the old
-- one gone in one step when the signature is identical -- it simply replaces,
-- which is what happens here. The rename/drop dance in the applied migration
-- was belt and braces to guarantee a single signature afterwards.
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
    topic_reserve_pct integer DEFAULT 30
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
    -- SECOND, TOPIC-SCOPED query. Filters to the topic FIRST so the distance
    -- sort runs over that topic's rows only. This is what reaches small topics
    -- the global pool never contains.
    topic_scoped as (
        select c.id as chunk_id, c.proposal_id, p.client_name, p.iam_vendor,
               p.industry, c.heading, c.text as chunk_text,
               p.proposal_type, c.section_topic,
               1 - (c.embedding <=> query_embedding::vector) as similarity,
               md5(c.text) as text_hash
        from proposal_chunks c
        join proposals p on p.id = c.proposal_id
        where c.embedding is not null
          and filter_section_topic is not null
          and topic_slots > 0
          and c.section_topic = filter_section_topic
        order by c.embedding <=> query_embedding::vector
        limit greatest(topic_slots * 4, 20)
    ),
    pooled as (
        select * from candidates
        union all
        select * from topic_scoped
    ),
    deduped as (
        select distinct on (text_hash) * from pooled
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

-- =====================================================
-- END OF MIGRATION sarvam_012
-- =====================================================
