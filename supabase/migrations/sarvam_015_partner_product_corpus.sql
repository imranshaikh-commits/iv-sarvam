-- =====================================================
-- sarvam_015 — partner_products / partner_product_chunks: record what's live
--
-- THE GAP
-- -------
-- Sprint 4 built a second corpus, structurally separate from the proposal-
-- history vault: partner_products / partner_product_chunks, plus a
-- match_partner_product_chunks retrieval function, mirroring the existing
-- proposal_chunks schema and RLS pattern. It was applied directly against
-- the live database via the Supabase connector, never captured as a file
-- under supabase/migrations/ -- so the repo's migration history has been
-- behind the live schema since. This file closes that gap.
--
-- PURE DOCUMENTATION-AS-CODE
-- ---------------------------
-- Every statement below uses IF NOT EXISTS / OR REPLACE. The tables, index,
-- policies and function already exist and work in production (110 proposals'
-- worth of proposal_chunks is untouched; this corpus itself holds 0 rows --
-- Sprint 7, gathering real partner content across all nine OEM partners, is
-- the next real piece of work, not yet started). Running this migration
-- against the live database is a no-op. Its purpose is a fresh environment
-- provisioned from supabase/migrations/ alone no longer missing two tables
-- and a function.
--
-- DESIGN NOTE, kept visible
-- --------------------------
-- match_partner_product_chunks filters WHERE p.reviewed = true. A partner
-- product row is never retrievable until a human has reviewed it -- same
-- discipline as visual_assets.approved, applied to vendor material instead
-- of images. Nothing here blends this corpus with proposal_chunks; a
-- well-documented product must never mask a genuine need for client-
-- specific evidence, so retrieval treats them as two separate sources.
-- =====================================================

create table if not exists public.partner_products (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null references public.organizations(id),
  vendor         text not null,
  product_name   text not null,
  capability     text,
  doc_type       text not null check (doc_type = any (array[
                   'datasheet', 'architecture_guide', 'admin_guide',
                   'analyst_report', 'solution_brief', 'other'
                 ])),
  source_url       text,
  source_filename  text,
  source_sha256    text,
  published_year   integer,
  reviewed         boolean not null default false,
  reviewed_by      text,
  reviewed_at      timestamptz,
  created_at       timestamptz not null default now()
);

create index if not exists partner_products_vendor_idx
  on public.partner_products using btree (vendor);

create table if not exists public.partner_product_chunks (
  id           uuid primary key default gen_random_uuid(),
  product_id   uuid not null references public.partner_products(id) on delete cascade,
  org_id       uuid not null references public.organizations(id),
  heading      text,
  text         text not null,
  word_count   integer not null default 0,
  embedding    vector,
  created_at   timestamptz not null default now()
);

create index if not exists partner_product_chunks_product_id_idx
  on public.partner_product_chunks using btree (product_id);

create index if not exists partner_product_chunks_embedding_idx
  on public.partner_product_chunks using hnsw (embedding vector_cosine_ops);

alter table public.partner_products enable row level security;
alter table public.partner_product_chunks enable row level security;

drop policy if exists "org members see org partner products" on public.partner_products;
create policy "org members see org partner products"
  on public.partner_products for select
  using (is_org_member(org_id));

drop policy if exists "org members insert partner products" on public.partner_products;
create policy "org members insert partner products"
  on public.partner_products for insert
  with check (is_org_member(org_id));

drop policy if exists "org members update org partner products" on public.partner_products;
create policy "org members update org partner products"
  on public.partner_products for update
  using (is_org_member(org_id));

drop policy if exists "org members see org partner product chunks" on public.partner_product_chunks;
create policy "org members see org partner product chunks"
  on public.partner_product_chunks for select
  using (is_org_member(org_id));

drop policy if exists "org members insert partner product chunks" on public.partner_product_chunks;
create policy "org members insert partner product chunks"
  on public.partner_product_chunks for insert
  with check (is_org_member(org_id));

create or replace function public.match_partner_product_chunks(
  query_embedding   text,
  match_count       integer default 8,
  filter_vendor     text default null,
  filter_capability text default null
)
returns table (
  chunk_id        uuid,
  product_id      uuid,
  vendor          text,
  product_name    text,
  capability      text,
  doc_type        text,
  published_year  integer,
  heading         text,
  chunk_text      text,
  similarity      double precision
)
language plpgsql
as $function$
BEGIN
    RETURN QUERY
    SELECT c.id as chunk_id, c.product_id, p.vendor, p.product_name,
           p.capability, p.doc_type, p.published_year,
           c.heading, c.text as chunk_text,
           1 - (c.embedding <=> query_embedding::vector) as similarity
    FROM partner_product_chunks c
    JOIN partner_products p ON p.id = c.product_id
    WHERE c.embedding IS NOT NULL
      AND p.reviewed = true
      AND (filter_vendor IS NULL OR p.vendor = filter_vendor)
      AND (filter_capability IS NULL OR p.capability = filter_capability)
    ORDER BY c.embedding <=> query_embedding::vector
    LIMIT match_count;
END;
$function$;
