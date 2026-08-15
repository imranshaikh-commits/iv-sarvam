-- =====================================================
-- sarvam_006 — ingestion deduplication
--
-- WHY
-- ---
-- `ingest_v2.py` inserts into `proposals` with a plain POST and no conflict
-- handling. Eleven proposals were already ingested before the Sales-SoWs bank
-- was curated, and several of them appear in the reviewed manifest. Re-running
-- ingestion would create a second `proposals` row and a second full set of
-- `proposal_chunks` — duplicate embeddings that quietly bias retrieval toward
-- whichever document happened to be ingested twice, with no error and nothing
-- in the logs to notice.
--
-- The reviewed manifest already carries `sha256_16`, a content hash computed
-- over the file bytes. That is the natural key: it survives a file being
-- renamed, refiled, or present as both .docx and .pdf under different names.
--
-- Nullable, so the eleven existing rows stay valid. Ingestion backfills the
-- hash for anything it re-encounters.
-- =====================================================

ALTER TABLE proposals ADD COLUMN IF NOT EXISTS source_sha256 TEXT;

-- Partial unique index: NULLs are allowed (the pre-existing rows), but no two
-- rows in the same org may share a hash. This is what makes the dedup check
-- authoritative rather than advisory — a race between two ingestion runs is
-- rejected by the database, not merely skipped by the script.
CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_org_sha
    ON proposals (org_id, source_sha256)
    WHERE source_sha256 IS NOT NULL;

-- Manifest tier the row was ingested under, so a proposal's provenance is
-- recorded alongside it. Everything ingested should be 'ingest'; anything else
-- appearing here means the manifest filter was bypassed.
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS source_tier TEXT;

-- `proposal_type` was previously written from an LLM prompt that could only
-- emit 'implementation' or 'mss'. The curated bank contains 39 migration files
-- across 12 engagements, so the column must accept it.
ALTER TABLE proposals DROP CONSTRAINT IF EXISTS proposals_proposal_type_check;
ALTER TABLE proposals ADD CONSTRAINT proposals_proposal_type_check
    CHECK (proposal_type IS NULL OR proposal_type IN
           ('implementation', 'mss', 'migration', 'assessment', 'other'));

CREATE INDEX IF NOT EXISTS idx_proposals_type_year
    ON proposals (proposal_type, year);

-- =====================================================
-- END OF MIGRATION sarvam_006
-- =====================================================
