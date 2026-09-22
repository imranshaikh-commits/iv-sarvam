-- =====================================================
-- sarvam_017 — partner_product_assets: images from the partner product corpus
--
-- WHY A NEW TABLE, NOT visual_assets
-- -----------------------------------
-- visual_assets.proposal_id is a hard FK to `proposals` (IV's own past
-- engagements). Reusing it for vendor-sourced product screenshots would
-- blend two corpora this project has deliberately kept separate throughout:
-- a vendor's marketing material and IV's own delivery history must never
-- compete in the same retrieval pass or be citable as if they were the same
-- kind of source (see partner_products' own comment, sarvam_015). Same
-- reasoning applied to images: a new table, mirroring visual_assets' shape,
-- FK'd to partner_products instead.
--
-- CHEAP PASS FIRST
-- -----------------
-- extract_visual_assets.py recovers vision descriptions FOR FREE because
-- those descriptions already existed -- paid for once, during the original
-- 112-proposal text ingestion. Sprint 7's partner-product ingestion never
-- ran vision/OCR on the 18 documents, so there is nothing to recover here.
-- Populated first by scripts/extract_partner_product_images.py, which
-- classifies by heuristics only (size, aspect ratio, optional Tesseract OCR)
-- -- no vision-model calls, no new cost. vision_description stays NULL until
-- a deliberate, separately-costed enrichment pass, if one is ever wanted.
--
-- APPROVAL
-- --------
-- approved defaults FALSE, same discipline as visual_assets and the
-- per-diagram chat approval gate. Nothing reaches a client document until a
-- human says so.
-- =====================================================

CREATE TABLE IF NOT EXISTS partner_product_assets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES organizations(id),
    product_id        UUID REFERENCES partner_products(id) ON DELETE CASCADE,

    storage_path      TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    mime_type         TEXT,
    width             INTEGER,
    height            INTEGER,
    size_bytes        INTEGER,

    source_url        TEXT,

    asset_kind        TEXT CHECK (asset_kind IN
                          ('architecture', 'product', 'corporate', 'unknown')),
    vision_description TEXT,
    ocr_text          TEXT,

    approved          BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by       TEXT,
    approved_at       TIMESTAMPTZ,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Same vendor logo can appear in several of that vendor's documents.
CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_product_assets_org_hash
    ON partner_product_assets (org_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_partner_product_assets_kind
    ON partner_product_assets (org_id, asset_kind, approved);
CREATE INDEX IF NOT EXISTS idx_partner_product_assets_product
    ON partner_product_assets (product_id);

ALTER TABLE partner_product_assets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "org members see org partner product assets" ON partner_product_assets;
CREATE POLICY "org members see org partner product assets"
    ON partner_product_assets FOR SELECT
    USING (is_org_member(org_id));

DROP POLICY IF EXISTS "org members insert partner product assets" ON partner_product_assets;
CREATE POLICY "org members insert partner product assets"
    ON partner_product_assets FOR INSERT
    WITH CHECK (is_org_member(org_id));

DROP POLICY IF EXISTS "org members update org partner product assets" ON partner_product_assets;
CREATE POLICY "org members update org partner product assets"
    ON partner_product_assets FOR UPDATE
    USING (is_org_member(org_id));

COMMENT ON TABLE partner_product_assets IS
  'Images extracted from the partner product corpus (vendor datasheets, '
  'architecture guides). Structurally separate from visual_assets (IV''s own '
  'proposal bank) for the same reason partner_product_chunks is separate '
  'from proposal_chunks. Nothing may be embedded in a client document unless '
  'approved = true.';

-- Private bucket, separate from visual-assets for the same reason the table
-- is separate. Applied here so a fresh environment is reproducible.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('partner-product-assets', 'partner-product-assets', false, 10485760,
        ARRAY['image/png', 'image/jpeg', 'image/gif', 'image/bmp', 'image/webp'])
ON CONFLICT (id) DO NOTHING;

-- =====================================================
-- END OF MIGRATION sarvam_017
-- =====================================================
