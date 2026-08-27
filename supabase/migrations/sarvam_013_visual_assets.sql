-- =====================================================
-- sarvam_013 — visual_assets: keep the images, not just their descriptions
--
-- THE GAP
-- -------
-- IV's human Amlak proposal carries 37 images. Shilpi has produced 6 in every
-- run since run 3. The pipeline already does the expensive half: it extracts
-- every embedded image, OCRs the text-heavy ones and sends the diagram-like
-- ones to a vision model -- then folds the DESCRIPTION into a text chunk and
-- DISCARDS THE BYTES. The corpus holds 1,849 'diagram' chunks describing
-- pictures nobody can retrieve.
--
-- WHAT IS ACTUALLY REUSABLE
-- -------------------------
-- Of IV's 37 images, roughly 12 are corporate and case-study visuals, 6 are
-- vendor product screenshots, and only ~6 are architecture drawings specific to
-- that deal. Most of what makes an IV document look rich is REUSABLE brand and
-- product material, not per-deal artwork.
--
-- THREE KINDS, DIFFERENT REUSE RULES
-- ----------------------------------
--   corporate     IV brand, org charts, maturity models, case-study visuals.
--                 Most reusable, largest share of IV's image count.
--   product       vendor UI screenshots. Reusable per vendor. Never fabricate
--                 or edit one -- a fake SailPoint UI is a misrepresentation.
--   architecture  drawn for a specific client estate. Rarely reusable, and
--                 embedding one would leak another client's topology.
--
-- APPROVAL GATE
-- -------------
-- `approved` defaults FALSE. Nothing may be embedded in a client document until
-- a human sets it true -- the same discipline as the per-diagram approval gate
-- in the chat flow. An unreviewed vendor screenshot inserted silently is worse
-- than no image.
--
-- Bytes live in Supabase Storage (bucket 'visual-assets', private, 10 MB cap),
-- not in a bytea column: a 20 MB proposal can carry 60 images.
--
-- Populated by scripts/extract_visual_assets.py, which needs NO API calls: the
-- vision descriptions are recovered from the existing chunks by the index in
-- their heading ("Diagram #5" -> image 5), because extraction order is
-- deterministic.
-- =====================================================

CREATE TABLE IF NOT EXISTS visual_assets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL,
    proposal_id       UUID REFERENCES proposals(id) ON DELETE CASCADE,

    storage_path      TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    mime_type         TEXT,
    width             INTEGER,
    height            INTEGER,
    size_bytes        INTEGER,

    -- Provenance. The (section_heading, image, caption) triple is the reuse
    -- signal: an image that sat under "Identity Maturity Journey" in three
    -- proposals belongs under that heading in the next one.
    section_heading   TEXT,
    caption           TEXT,
    page_order        INTEGER,

    asset_kind        TEXT CHECK (asset_kind IN
                          ('architecture', 'product', 'corporate', 'unknown')),
    vision_description TEXT,
    ocr_text          TEXT,

    approved          BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by       TEXT,
    approved_at       TIMESTAMPTZ,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The same IV logo appears in 100+ proposals: one row per distinct image.
CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_assets_org_hash
    ON visual_assets (org_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_visual_assets_kind
    ON visual_assets (org_id, asset_kind, approved);
CREATE INDEX IF NOT EXISTS idx_visual_assets_heading
    ON visual_assets (org_id, section_heading);
CREATE INDEX IF NOT EXISTS idx_visual_assets_proposal
    ON visual_assets (proposal_id);

COMMENT ON TABLE visual_assets IS
  'Reusable images extracted from the proposal bank. Nothing may be embedded '
  'in a client document unless approved = true.';

-- Private bucket. Applied separately via storage.buckets; recorded here so a
-- fresh environment is reproducible.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('visual-assets', 'visual-assets', false, 10485760,
        ARRAY['image/png','image/jpeg','image/gif','image/bmp','image/webp'])
ON CONFLICT (id) DO NOTHING;

-- =====================================================
-- END OF MIGRATION sarvam_013
-- =====================================================
