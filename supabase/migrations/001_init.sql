-- =====================================================
-- SARVAM — Initial Supabase Schema
-- =====================================================
-- Migration: 001_init.sql
-- Sprint 2 deliverable. Apply via Supabase SQL Editor or CLI.
--
-- Creates:
--   - Multi-tenant foundation (organizations, org_members)
--   - User profiles (linked to auth.users)
--   - RAG proposal bank (proposals + proposal_chunks with pgvector)
--   - Generated proposals (drafts by Sarvam)
--   - Architecture diagrams (approved Mermaid definitions)
--   - RLS policies on every table
--   - HNSW index on vector column
--
-- Assumes Supabase project with `pgvector` extension available.
-- =====================================================

-- ---------- Extensions ----------
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- =====================================================
-- 1. ORGANIZATIONS & MEMBERSHIP
-- =====================================================

CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE organizations IS 'Tenants. Sarvam is multi-tenant from day one to isolate client data.';

-- Seed the primary org
INSERT INTO organizations (name, slug) VALUES ('Inspirit Vision', 'inspirit-vision');

CREATE TABLE org_members (
    org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, user_id)
);

CREATE INDEX idx_org_members_user ON org_members(user_id);


-- =====================================================
-- 2. USER PROFILES & PREFERENCES
-- =====================================================

CREATE TABLE profiles (
    user_id             UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name        TEXT,
    default_org_id      UUID REFERENCES organizations(id),
    preferences         JSONB NOT NULL DEFAULT '{}'::JSONB,
    -- Example preferences JSON:
    -- {
    --   "default_model": "deepseek/deepseek-v4-flash",
    --   "output_format": "docx",
    --   "tone": "formal",
    --   "compression_default": "lite"
    -- }
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-create profile row when a new auth.users row appears
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    default_org UUID;
BEGIN
    SELECT id INTO default_org FROM organizations WHERE slug = 'inspirit-vision' LIMIT 1;

    INSERT INTO profiles (user_id, display_name, default_org_id)
    VALUES (NEW.id, NEW.email, default_org);

    -- Auto-add to IV org as member (adjust for production onboarding)
    INSERT INTO org_members (org_id, user_id, role)
    VALUES (default_org, NEW.id, 'member');

    RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_user();


-- =====================================================
-- 3. PROPOSAL BANK (RAG source)
-- =====================================================

CREATE TABLE proposals (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    proposal_slug       TEXT NOT NULL,  -- from ingestion script
    source_filename     TEXT NOT NULL,
    file_type           TEXT NOT NULL CHECK (file_type IN ('docx', 'pdf')),
    total_word_count    INTEGER NOT NULL DEFAULT 0,
    image_count         INTEGER NOT NULL DEFAULT 0,
    -- Metadata tagged manually via CSV
    client_name         TEXT,
    industry            TEXT,
    country             TEXT,
    iam_vendor          TEXT,  -- 'sailpoint', 'ping', 'ibm', 'keycloak', 'forgerock', 'saviynt', 'okta'
    proposal_type       TEXT CHECK (proposal_type IN ('implementation', 'mss')),
    user_count          INTEGER,
    app_count           INTEGER,
    deal_size_bucket    TEXT CHECK (deal_size_bucket IN ('small', 'medium', 'large', 'enterprise')),
    outcome             TEXT CHECK (outcome IN ('won', 'lost', 'pending', 'unknown')),
    year                INTEGER,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, proposal_slug)
);

CREATE INDEX idx_proposals_org ON proposals(org_id);
CREATE INDEX idx_proposals_vendor ON proposals(iam_vendor);
CREATE INDEX idx_proposals_type ON proposals(proposal_type);
CREATE INDEX idx_proposals_outcome ON proposals(outcome);


-- =====================================================
-- 4. PROPOSAL CHUNKS (embedded vectors)
-- =====================================================
-- One row per section per proposal. Embedded via text-embedding-3-small (1536-dim).

CREATE TABLE proposal_chunks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id         UUID NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    section_type        TEXT NOT NULL,  -- 'executive_summary', 'company_profile', etc.
    heading             TEXT NOT NULL,
    section_order       INTEGER NOT NULL,
    text                TEXT NOT NULL,
    word_count          INTEGER NOT NULL DEFAULT 0,
    embedding           VECTOR(1536),   -- text-embedding-3-small dimensions
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chunks_proposal ON proposal_chunks(proposal_id);
CREATE INDEX idx_chunks_org ON proposal_chunks(org_id);
CREATE INDEX idx_chunks_section_type ON proposal_chunks(section_type);

-- HNSW index for fast cosine similarity search
-- m=16, ef_construction=64 are standard defaults for corpora <100K vectors
CREATE INDEX idx_chunks_embedding ON proposal_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);


-- =====================================================
-- 5. GENERATED PROPOSALS (Sarvam's drafts)
-- =====================================================

CREATE TABLE generated_proposals (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_by              UUID NOT NULL REFERENCES auth.users(id),
    -- Discovery context
    client_name             TEXT,
    proposal_type           TEXT CHECK (proposal_type IN ('implementation', 'mss')),
    iam_vendor              TEXT,
    discovery_answers       JSONB NOT NULL DEFAULT '{}'::JSONB,
    -- Lifecycle state machine
    status                  TEXT NOT NULL DEFAULT 'discovery'
                            CHECK (status IN ('discovery', 'architecture_review',
                                              'architecture_approved', 'drafting',
                                              'review', 'final', 'abandoned')),
    architecture_diagram_id UUID,  -- FK filled after Sprint 9
    -- Content
    draft_markdown          TEXT,
    final_docx_path         TEXT,     -- Supabase storage path
    final_pdf_path          TEXT,
    lite_docx_path          TEXT,
    -- Retrieval trace (for audit)
    retrieval_trace         JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_generated_org ON generated_proposals(org_id);
CREATE INDEX idx_generated_user ON generated_proposals(created_by);
CREATE INDEX idx_generated_status ON generated_proposals(status);


-- =====================================================
-- 6. ARCHITECTURE DIAGRAMS (approved Mermaid definitions)
-- =====================================================

CREATE TABLE architecture_diagrams (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    generated_proposal_id   UUID REFERENCES generated_proposals(id) ON DELETE CASCADE,
    mermaid_source          TEXT NOT NULL,
    rendered_svg_path       TEXT,  -- Supabase storage path if pre-rendered
    approved                BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by             UUID REFERENCES auth.users(id),
    approved_at             TIMESTAMPTZ,
    rejection_comments      TEXT[],
    iteration               INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_diagrams_org ON architecture_diagrams(org_id);
CREATE INDEX idx_diagrams_proposal ON architecture_diagrams(generated_proposal_id);


-- =====================================================
-- 7. RETRIEVAL FUNCTION (called by Hermes skill)
-- =====================================================

CREATE OR REPLACE FUNCTION match_proposal_chunks(
    query_embedding     VECTOR(1536),
    match_threshold     FLOAT DEFAULT 0.75,
    match_count         INT DEFAULT 5,
    filter_org_id       UUID DEFAULT NULL,
    filter_section_type TEXT DEFAULT NULL,
    filter_vendor       TEXT DEFAULT NULL,
    filter_proposal_type TEXT DEFAULT NULL,
    filter_outcome      TEXT DEFAULT NULL
)
RETURNS TABLE (
    chunk_id            UUID,
    proposal_id         UUID,
    section_type        TEXT,
    heading             TEXT,
    text                TEXT,
    similarity          FLOAT,
    client_name         TEXT,
    iam_vendor          TEXT,
    outcome             TEXT
) LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    RETURN QUERY
    SELECT
        pc.id,
        pc.proposal_id,
        pc.section_type,
        pc.heading,
        pc.text,
        1 - (pc.embedding <=> query_embedding) AS similarity,
        p.client_name,
        p.iam_vendor,
        p.outcome
    FROM proposal_chunks pc
    JOIN proposals p ON p.id = pc.proposal_id
    WHERE
        (filter_org_id       IS NULL OR pc.org_id = filter_org_id)
        AND (filter_section_type IS NULL OR pc.section_type = filter_section_type)
        AND (filter_vendor       IS NULL OR p.iam_vendor = filter_vendor)
        AND (filter_proposal_type IS NULL OR p.proposal_type = filter_proposal_type)
        AND (filter_outcome      IS NULL OR p.outcome = filter_outcome)
        AND (1 - (pc.embedding <=> query_embedding)) > match_threshold
    ORDER BY pc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- =====================================================
-- 8. ROW LEVEL SECURITY
-- =====================================================
-- CRITICAL: RLS is enforced at DB layer, not app layer.
-- Users can only see data in orgs they are members of.

ALTER TABLE organizations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_members           ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles              ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposals             ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposal_chunks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE generated_proposals   ENABLE ROW LEVEL SECURITY;
ALTER TABLE architecture_diagrams ENABLE ROW LEVEL SECURITY;

-- Helper: is the current user a member of the given org?
CREATE OR REPLACE FUNCTION is_org_member(check_org_id UUID)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM org_members
        WHERE user_id = auth.uid() AND org_id = check_org_id
    );
$$;

-- ---- organizations ----
CREATE POLICY "org members see their orgs"
    ON organizations FOR SELECT
    USING (is_org_member(id));

-- ---- org_members ----
CREATE POLICY "members see co-members"
    ON org_members FOR SELECT
    USING (is_org_member(org_id));

-- ---- profiles ----
CREATE POLICY "users see their own profile"
    ON profiles FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "users update their own profile"
    ON profiles FOR UPDATE
    USING (user_id = auth.uid());

-- ---- proposals ----
CREATE POLICY "org members see org proposals"
    ON proposals FOR SELECT
    USING (is_org_member(org_id));

CREATE POLICY "org members insert proposals"
    ON proposals FOR INSERT
    WITH CHECK (is_org_member(org_id));

-- ---- proposal_chunks ----
CREATE POLICY "org members see org chunks"
    ON proposal_chunks FOR SELECT
    USING (is_org_member(org_id));

CREATE POLICY "org members insert chunks"
    ON proposal_chunks FOR INSERT
    WITH CHECK (is_org_member(org_id));

-- ---- generated_proposals ----
CREATE POLICY "org members see org drafts"
    ON generated_proposals FOR SELECT
    USING (is_org_member(org_id));

CREATE POLICY "org members create drafts"
    ON generated_proposals FOR INSERT
    WITH CHECK (is_org_member(org_id) AND created_by = auth.uid());

CREATE POLICY "creators update their drafts"
    ON generated_proposals FOR UPDATE
    USING (created_by = auth.uid());

-- ---- architecture_diagrams ----
CREATE POLICY "org members see org diagrams"
    ON architecture_diagrams FOR SELECT
    USING (is_org_member(org_id));

CREATE POLICY "org members create diagrams"
    ON architecture_diagrams FOR INSERT
    WITH CHECK (is_org_member(org_id));


-- =====================================================
-- 9. STORAGE BUCKETS
-- =====================================================
-- Run these via Supabase Dashboard > Storage, or via API.
-- Cannot be created via SQL directly (storage lives in a separate schema).
--
-- CREATE these buckets manually (all private, RLS-scoped):
--   1. source-proposals   — raw DOCX/PDF originals
--   2. proposal-images    — extracted images from source proposals
--   3. generated-drafts   — Sarvam's output files
--   4. diagram-renders    — pre-rendered SVGs of approved architectures
--
-- Then apply this policy pattern in the Supabase dashboard for each bucket:
--   Policy name: "Org members can read their bucket files"
--   Definition:  bucket_id = '<bucket_name>' AND
--                (storage.foldername(name))[1] IN (
--                    SELECT org_id::text FROM org_members WHERE user_id = auth.uid()
--                )


-- =====================================================
-- END OF MIGRATION 001
-- =====================================================
--
-- Verify with:
--   SELECT * FROM organizations;
--   SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';
--   \d proposal_chunks
