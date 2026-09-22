-- =====================================================
-- sarvam_016 — per-product diversity cap on the partner product corpus
--
-- WHY
-- ---
-- Sprint 7 ingested 18 documents across 9 OEM partners, 276 chunks total.
-- One document -- PingOne Advanced Services, a 47,830-word technical
-- reference -- is 155 of those 276 chunks (56%). match_partner_product_chunks
-- had filter_vendor and filter_capability, but no cap on how many chunks a
-- single product could contribute. An unfiltered or loosely-filtered query
-- could return mostly PingOne Advanced Services content regardless of which
-- vendor's section was actually being drafted.
--
-- Same failure shape sarvam_007 fixed for match_proposal_chunks (there:
-- TASNEE/KAU/NWC's many revisions could fill the whole evidence budget from
-- one document). Same fix: partition by source, keep the top N per source,
-- over-fetch a wider candidate pool first so the cap has something to choose
-- from.
--
-- No dedup pass is needed here the way sarvam_007 needed one -- this corpus
-- is curated (reviewed=true gate), not a bank of revision-numbered duplicates
-- -- so this migration is the diversity half only, not dedup+diversity.
--
-- MEASURED, before and after (query seeded from a PingOne Advanced Services
-- chunk, the worst case for this specific document dominating):
--
--   before   unbounded  -- a query could return >3 chunks from one product
--   after    max 3 chunks from PingOne Advanced Services, 3 from PingFederate,
--            in an unfiltered 20-result request
--
-- CAUTION, repeated from sarvam_007 because it bit this exact function once
-- already in an unrelated way: adding a parameter with a default OVERLOADS
-- the function rather than replacing it. The old 4-argument signature must be
-- dropped explicitly, or PostgREST's named-argument call from app.py
-- (query_embedding, match_count, filter_vendor, filter_capability -- no
-- max_per_product) becomes ambiguous between the two signatures and every
-- call from the brain fails with "function is not unique". Verified after
-- this migration with the exact 4-argument named call app.py makes.
-- =====================================================

CREATE OR REPLACE FUNCTION public.match_partner_product_chunks(
    query_embedding text,
    match_count integer DEFAULT 8,
    filter_vendor text DEFAULT NULL,
    filter_capability text DEFAULT NULL,
    max_per_product integer DEFAULT 3
)
RETURNS TABLE(
    chunk_id uuid, product_id uuid, vendor text, product_name text,
    capability text, doc_type text, published_year integer,
    heading text, chunk_text text, similarity double precision
)
LANGUAGE sql
STABLE
AS $function$
    with candidates as (
        select c.id as chunk_id, c.product_id, p.vendor, p.product_name,
               p.capability, p.doc_type, p.published_year,
               c.heading, c.text as chunk_text,
               1 - (c.embedding <=> query_embedding::vector) as similarity
        from partner_product_chunks c
        join partner_products p on p.id = c.product_id
        where c.embedding is not null
          and p.reviewed = true
          and (filter_vendor is null or p.vendor = filter_vendor)
          and (filter_capability is null or p.capability = filter_capability)
        order by c.embedding <=> query_embedding::vector
        limit greatest(match_count * 6, 60)
    ),
    diversified as (
        select *, row_number() over (
            partition by product_id order by similarity desc) as rn
        from candidates
    )
    select chunk_id, product_id, vendor, product_name, capability, doc_type,
           published_year, heading, chunk_text, similarity
    from diversified
    where rn <= greatest(max_per_product, 1)
    order by similarity desc
    limit match_count;
$function$;

-- Must run AFTER the CREATE above -- see the caution note.
DROP FUNCTION IF EXISTS public.match_partner_product_chunks(text, integer, text, text);

-- =====================================================
-- END OF MIGRATION sarvam_016
-- =====================================================
