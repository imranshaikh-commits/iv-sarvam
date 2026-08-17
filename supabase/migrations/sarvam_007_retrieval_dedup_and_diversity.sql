-- =====================================================
-- sarvam_007 — retrieval deduplication and source diversity
--
-- WHY
-- ---
-- Measured after the corpus grew 11 -> 114 proposals (1,413 -> 11,286 chunks):
--
--   exact duplicate chunk texts   4,842 of 11,286  (42.9%)
--
-- Not near-duplicates. Byte-identical text. IV names revisions by suffix and
-- the reviewed manifest deliberately keeps them: TASNEE has six revisions, KAU
-- six, the NWC Oracle Access Manager upgrade fourteen files across four partner
-- variants. Each revision changes a few sections and leaves the rest identical,
-- so unchanged content sits in the corpus four to six times over.
--
-- The effect on retrieval, measured by using an existing chunk's embedding as
-- the query vector:
--
--   sizing query        BEFORE  7 of 10 hits were ONE table from ONE proposal
--                       AFTER   7 distinct proposals, max 3 from any one
--   certification query BEFORE  6 of 8 hits were ONE section from ONE proposal
--                       AFTER   5 distinct proposals, max 3 from any one
--
-- At top_k=8 a single document could consume the entire evidence budget, so a
-- section was drafted from one unrelated bank's proposal rather than from the
-- breadth of the corpus.
--
-- The duplicate rows are legitimate provenance, so this is fixed at QUERY time
-- rather than by deleting data.
--
-- Over-fetches internally (match_count * 6, floor 60) because dedup and the cap
-- both discard rows; without a wider candidate pool the function would return
-- fewer than match_count results.
--
-- CAUTION FOR FUTURE CHANGES
-- --------------------------
-- Adding a parameter with a default does NOT replace the function, it OVERLOADS
-- it. Both signatures then exist and Postgres raises "function is not unique"
-- on every 2-argument call -- which is every call the brain makes. The old
-- signature must be dropped explicitly (see the DROP at the end). This was
-- caught only by re-running a query after the migration reported success.
-- =====================================================

CREATE OR REPLACE FUNCTION public.match_proposal_chunks(
    query_embedding text,
    match_count integer DEFAULT 8,
    max_per_proposal integer DEFAULT 3
)
RETURNS TABLE(
    chunk_id uuid, proposal_id uuid, client_name text, iam_vendor text,
    industry text, heading text, chunk_text text, similarity double precision
)
LANGUAGE sql
STABLE
AS $function$
    with candidates as (
        select c.id as chunk_id, c.proposal_id, p.client_name, p.iam_vendor,
               p.industry, c.heading, c.text as chunk_text,
               1 - (c.embedding <=> query_embedding::vector) as similarity,
               md5(c.text) as text_hash
        from proposal_chunks c
        join proposals p on p.id = c.proposal_id
        where c.embedding is not null
        order by c.embedding <=> query_embedding::vector
        limit greatest(match_count * 6, 60)
    ),
    deduped as (
        -- One row per distinct TEXT. Where the same text exists under several
        -- proposals, keep the closest match.
        select distinct on (text_hash) *
        from candidates
        order by text_hash, similarity desc
    ),
    diversified as (
        select *, row_number() over (
            partition by proposal_id order by similarity desc) as rn
        from deduped
    )
    select chunk_id, proposal_id, client_name, iam_vendor, industry,
           heading, chunk_text, similarity
    from diversified
    where rn <= greatest(max_per_proposal, 1)
    order by similarity desc
    limit match_count;
$function$;

-- Must run AFTER the CREATE above: existing 2-argument callers resolve to the
-- 3-argument version via its default once the old signature is gone.
DROP FUNCTION IF EXISTS public.match_proposal_chunks(text, integer);

-- =====================================================
-- END OF MIGRATION sarvam_007
-- =====================================================
