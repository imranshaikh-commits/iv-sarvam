-- =====================================================
-- sarvam_009 — semantic section labels, alongside the structural ones
--
-- MEASURED PROBLEM
-- ----------------
-- section_type is 46% 'other' (5,144 of 11,286). Across 114 proposals it finds
-- 34 why_vendor chunks and 16 company_profile chunks -- yet every IV proposal
-- has both. Metadata-filtered retrieval is impossible against labels this poor:
-- filtering "Why SailPoint" to why_vendor would search 34 chunks and miss the
-- hundreds that exist unlabelled.
--
-- WHY A NEW COLUMN RATHER THAN FIXING section_type
-- ------------------------------------------------
-- section_type conflates two things. 'table', 'diagram', 'page' and 'ocr'
-- (5,135 chunks) are STRUCTURAL facts about how content was extracted;
-- 'scope', 'pricing', 'architecture' are SEMANTIC facts about what it is about.
-- Overwriting would destroy the structural information, which the retrieval
-- scorecard uses to track fragment share.
--
-- A table inside a Commercial section is both a table AND commercial. Two
-- columns express that; one cannot. It also makes the backfill non-destructive
-- and re-runnable, which matters when relabelling 11,286 rows.
--
-- Backfilled by scripts/classify_sections.py.
-- =====================================================

ALTER TABLE proposal_chunks ADD COLUMN IF NOT EXISTS section_topic TEXT;

COMMENT ON COLUMN proposal_chunks.section_topic IS
  'Semantic section label (scope, architecture, company_profile, ...). '
  'section_type stays as the structural label (table, diagram, page, ocr).';

CREATE INDEX IF NOT EXISTS idx_chunks_topic ON proposal_chunks (section_topic);

-- Retrieval filters on topic AND proposal together, so index the pair.
CREATE INDEX IF NOT EXISTS idx_chunks_proposal_topic
    ON proposal_chunks (proposal_id, section_topic);

-- =====================================================
-- END OF MIGRATION sarvam_009
-- =====================================================
