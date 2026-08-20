-- =====================================================
-- sarvam_011 — TOPIC-SCOPED query for small topics
--
-- MEASURED PROBLEM
-- ----------------
-- With the topic reservation live (sarvam_010) the why_vendor probe still
-- scored topic=0.125 -- one of eight -- despite an explicit filter and 121
-- why_vendor chunks across 73 proposals.
--
-- Cause: the reservation can only choose from the top-120 ANN candidates.
-- why_vendor is ~1% of the corpus, so a general query returns a pool holding
-- almost none of it. There is nothing to reserve. The same shape as the
-- ef_search bug in sarvam_008: a filter can only act on what the pool already
-- contains.
--
-- FIX
-- ---
-- When the global pool cannot fill the reserved topic slots, run a SECOND
-- query restricted to that topic. idx_chunks_topic narrows to the topic's rows
-- first, so the distance sort runs over ~121 rows rather than 11,286: exact
-- rather than approximate, and fast because the set is tiny.
--
-- The small, bounded form of the two-tier retrieval in the project notes.
--
-- MEASURED RESULT, seeded from an ARCHITECTURE chunk (a query with no natural
-- affinity to any of these topics):
--
--     topic                 before   after   (of 8, 4 slots reserved)
--     why_vendor                 1       4
--     company_profile            -       4
--     similar_experience         -       4
--
-- These are exactly the bank-sourced sections -- Company Profile, Why-Vendor,
-- Similar Experience -- that have been thin in every generated proposal.
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
