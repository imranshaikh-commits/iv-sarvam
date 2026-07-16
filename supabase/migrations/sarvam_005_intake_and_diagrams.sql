-- =====================================================
-- SARVAM — Intake sessions + persistence foundation
-- =====================================================
-- Migration: sarvam_005_intake_and_diagrams.sql
-- Sprint 5, Pass 1. Apply via Supabase SQL Editor or CLI.
--
-- Adds:
--   - intake_sessions table (structured discovery interview state)
--   - generated_proposals.intake_session_id FK + created_by nullable
--     (unblocks server-side persistence before real auth is wired)
--   - architecture_diagrams forward-store columns for Pass 4
--
-- Idempotent: safe to re-run (IF NOT EXISTS + DROP POLICY IF EXISTS).
-- Does NOT disable RLS, does NOT drop columns, does NOT touch
-- proposals / proposal_chunks.
-- =====================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- =====================================================
-- 1. INTAKE SESSIONS
-- =====================================================
-- Holds the state of a structured discovery interview before (and after) it is
-- turned into a generated proposal. answers is a free-form jsonb keyed by the
-- question ids defined in backend/brain/intake_template.py.

CREATE TABLE IF NOT EXISTS intake_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    status                  TEXT NOT NULL DEFAULT 'in_progress'
                            CHECK (status IN ('in_progress', 'complete', 'abandoned')),
    proposal_type           TEXT,
    client_name             TEXT,
    iam_vendor              TEXT,
    answers                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    generated_proposal_id   UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE intake_sessions IS
    'Structured discovery interview state. answers jsonb is keyed by intake_template question ids.';

CREATE INDEX IF NOT EXISTS idx_intake_org ON intake_sessions(org_id);
CREATE INDEX IF NOT EXISTS idx_intake_status ON intake_sessions(status);
CREATE INDEX IF NOT EXISTS idx_intake_proposal ON intake_sessions(generated_proposal_id);

ALTER TABLE intake_sessions ENABLE ROW LEVEL SECURITY;

-- RLS mirrors generated_proposals (001_init) — org-scoped via is_org_member().
-- Writes are performed server-side with the service-role key (which bypasses
-- RLS); these policies govern any future authenticated-role access.
DROP POLICY IF EXISTS "org members see org intake" ON intake_sessions;
CREATE POLICY "org members see org intake"
    ON intake_sessions FOR SELECT
    USING (is_org_member(org_id));

DROP POLICY IF EXISTS "org members create intake" ON intake_sessions;
CREATE POLICY "org members create intake"
    ON intake_sessions FOR INSERT
    WITH CHECK (is_org_member(org_id));

DROP POLICY IF EXISTS "org members update intake" ON intake_sessions;
CREATE POLICY "org members update intake"
    ON intake_sessions FOR UPDATE
    USING (is_org_member(org_id));


-- =====================================================
-- 2. GENERATED PROPOSALS — link to intake + nullable created_by
-- =====================================================

ALTER TABLE generated_proposals
    ADD COLUMN IF NOT EXISTS intake_session_id UUID
    REFERENCES intake_sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_generated_intake ON generated_proposals(intake_session_id);

-- TODO(auth): created_by is temporarily nullable so the brain can persist
-- server-side drafts before Supabase Auth / user identity is wired end-to-end.
-- Once real auth propagates a user id into the request, restore NOT NULL and
-- backfill any NULL rows.
ALTER TABLE generated_proposals ALTER COLUMN created_by DROP NOT NULL;

COMMENT ON COLUMN generated_proposals.created_by IS
    'TODO(auth): temporarily nullable for server-side persistence before real auth is wired. Restore NOT NULL once user identity flows through the request.';


-- =====================================================
-- 3. ARCHITECTURE DIAGRAMS — forward store for Pass 4
-- =====================================================
-- mermaid_source stays NOT NULL (existing contract). spec_json is the new
-- structured store that Pass 4 will populate; renderer defaults to graphviz.

ALTER TABLE architecture_diagrams ADD COLUMN IF NOT EXISTS diagram_type TEXT;
ALTER TABLE architecture_diagrams ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE architecture_diagrams ADD COLUMN IF NOT EXISTS spec_json JSONB;
ALTER TABLE architecture_diagrams ADD COLUMN IF NOT EXISTS renderer TEXT DEFAULT 'graphviz';
ALTER TABLE architecture_diagrams ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft'
    CHECK (status IN ('draft', 'needs_review', 'approved', 'rejected'));
ALTER TABLE architecture_diagrams ADD COLUMN IF NOT EXISTS intake_session_id UUID
    REFERENCES intake_sessions(id);

CREATE INDEX IF NOT EXISTS idx_diagrams_intake ON architecture_diagrams(intake_session_id);


-- =====================================================
-- END OF MIGRATION sarvam_005
-- =====================================================
