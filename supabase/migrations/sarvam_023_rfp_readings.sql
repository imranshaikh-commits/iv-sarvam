-- sarvam_023: one stored reading per tender file and extractor version.
--
-- Design 2026-10-14 (DESIGN_RFP_READING_AND_MATRIX.md, part 1c). Two reads of
-- one file could disagree. The brain now stores what it read from a file under
-- the file's SHA-256 and the extractor version, and reuses it when the same
-- file is uploaded again: no model call, identical answers. A new extractor
-- version reads the file fresh.
--
-- Additive: a new table, nothing existing changes. Row Level Security is on
-- with no policy, so only the service role (the brain) reads or writes it.
-- reading holds the extraction (field values with their pages, requirements,
-- eligibility conditions, mandated structure): client content, like
-- intake_sessions.answers.
create table if not exists public.rfp_readings (
    sha256            text        not null check (sha256 ~ '^[0-9a-f]{64}$'),
    extractor_version text        not null,
    reading           jsonb       not null,
    created_at        timestamptz not null default now(),
    primary key (sha256, extractor_version)
);

alter table public.rfp_readings enable row level security;

comment on table public.rfp_readings is
    'What the brain read from one tender file (sha256) with one extractor version. Service role only.';
