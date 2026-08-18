-- =====================================================
-- sarvam_008 — proposal-type-aware retrieval
--
-- Applied to the database in four steps (008, 008b, 008c, 008d) because the
-- first three did not work. This file is the FINAL state plus the reasons, so
-- the failures are not repeated.
--
-- MEASURED PROBLEM (retrieval scorecard, 20 probes, top_k=8)
-- ----------------------------------------------------------
-- Corpus by chunk: implementation 57.3%, migration 35.5%, mss 7.2%.
-- Share of results from a migration proposal on a migration question:
--     mig_strategy 0.25, mig_credentials 0.25, mig_decommission 0.375
-- Retrieval was performing WORSE than random on proposal type. Every migration
-- section would be drafted from greenfield implementation proposals: documents
-- about building from nothing, for a client who already runs a working
-- identity platform.
--
-- THREE BUGS, STACKED
-- -------------------
-- 1. (008)  The reservation was cosmetic. `reserved` and `remainder` were
--           unioned then re-truncated by `order by similarity desc limit
--           match_count`, which evicted the reserved rows precisely because
--           they score lower -- that being the entire point. Measured
--           identical with the feature on and off.
--
-- 2. (008c) Still identical after fixing 1. pgvector's HNSW index returns at
--           most `hnsw.ef_search` candidates, DEFAULT 40, however large the
--           LIMIT. The over-fetches of 60 (sarvam_007) and 120 (008) never
--           happened. In those 40 candidates for a migration query, 39 were
--           implementation and ONE was migration: the reservation had nothing
--           to reserve. Silent truncation of a pool nobody knew was capped.
--
-- 3. (008d) Raising ef_search needs SET LOCAL, which needs PL/pgSQL, which
--           then failed twice more: SET is illegal in a STABLE function, and
--           RETURNS TABLE column names shadow CTE columns in PL/pgSQL unless
--           `#variable_conflict use_column` is set.
--
-- RESULT, measured on a credential-migration probe:
--     without filter_proposal_type   1 of 8 chunks from a migration proposal
--     with it                        5 of 8      (similarity 0.870 -> 0.807)
-- The similarity drop is the correct trade: slightly less lexically similar
-- text, from documents that are actually about migrations.
--
-- WHY A RESERVATION AND NOT A HARD FILTER
-- ---------------------------------------
-- A hard filter would cut the pool to 4,004 chunks and discard genuinely
-- cross-type content -- company profile, RACI, knowledge transfer and vendor
-- positioning read the same whichever engagement type they belong to. Source
-- diversity is currently good (6.55 distinct proposals per probe) and a hard
-- filter would put that at risk. `type_reserve_pct = 0` disables the behaviour
-- without a migration.
-- =====================================================

CREATE OR REPLACE FUNCTION public.match_proposal_chunks(
    query_embedding text,
    match_count integer DEFAULT 8,
    max_per_proposal integer DEFAULT 3,
    filter_proposal_type text DEFAULT NULL,
    type_reserve_pct integer DEFAULT 60,
    ef_search integer DEFAULT 200
)
RETURNS TABLE(
    chunk_id uuid, proposal_id uuid, client_name text, iam_vendor text,
    industry text, heading text, chunk_text text, similarity double precision
)
LANGUAGE plpgsql
VOLATILE          -- required: SET LOCAL is illegal in a STABLE function
AS $function$
#variable_conflict use_column   -- RETURNS TABLE names shadow CTE columns
BEGIN
    -- Without this the ANN index returns at most 40 candidates, whatever the
    -- LIMIT below says. Transaction-scoped, so it cannot leak.
    EXECUTE format('SET LOCAL hnsw.ef_search = %s',
                   greatest(ef_search, match_count * 4));

    RETURN QUERY
    with candidates as (
        select c.id as chunk_id, c.proposal_id, p.client_name, p.iam_vendor,
               p.industry, c.heading, c.text as chunk_text, p.proposal_type,
               1 - (c.embedding <=> query_embedding::vector) as similarity,
               md5(c.text) as text_hash
        from proposal_chunks c
        join proposals p on p.id = c.proposal_id
        where c.embedding is not null
        order by c.embedding <=> query_embedding::vector
        limit greatest(match_count * 12, 120)
    ),
    -- 42.9% of chunks are byte-identical duplicates: IV keeps revisions, and
    -- each one repeats the unchanged 90% of the document. Keep the closest.
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
    reserved as (
        select * from capped
        where filter_proposal_type is not null
          and type_reserve_pct > 0
          and proposal_type = filter_proposal_type
        order by similarity desc
        limit case
                when filter_proposal_type is null or type_reserve_pct <= 0 then 0
                else ceil(match_count * least(type_reserve_pct, 100) / 100.0)
             end
    ),
    remainder as (
        select c.* from capped c
        where not exists (select 1 from reserved r where r.chunk_id = c.chunk_id)
        order by c.similarity desc
        -- Only the slots the reservation left, so the union is already exactly
        -- match_count and the final ordering cannot evict a reserved row.
        limit greatest(match_count - (select count(*) from reserved), 0)
    )
    select m.chunk_id, m.proposal_id, m.client_name, m.iam_vendor, m.industry,
           m.heading, m.chunk_text, m.similarity
    from (select * from reserved union all select * from remainder) m
    order by m.similarity desc;
END;
$function$;

-- Adding parameters OVERLOADS rather than replaces. Both signatures then exist
-- and Postgres raises "function is not unique" on every existing call.
DROP FUNCTION IF EXISTS public.match_proposal_chunks(text, integer);
DROP FUNCTION IF EXISTS public.match_proposal_chunks(text, integer, integer);
DROP FUNCTION IF EXISTS public.match_proposal_chunks(text, integer, integer, text, integer);

-- =====================================================
-- END OF MIGRATION sarvam_008
-- =====================================================
