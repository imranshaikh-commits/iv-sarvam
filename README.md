# Sarvam — IV Proposal Architect

**Sarvam** (सर्वम्, Sanskrit for *"all, everything, the whole"*) is Inspirit Vision's in-house Proposal Architect — a conversational AI agent that drafts, structures, and refines client proposals grounded in IV's 100+ historical proposal bank.

## Quick links

- **Project doc:** [`docs/PROJECT.md`](docs/PROJECT.md) — full ground-zero plan, sprints, budget, risks.
- **Persona spec:** [`docs/SARVAM_PERSONA.md`](docs/SARVAM_PERSONA.md) — character, voice, system prompt.
- **Meet Sarvam:** [`docs/MEET_SARVAM.md`](docs/MEET_SARVAM.md) — one-pager for the IV delivery team.

## Repository layout

```
sarvam/
├── README.md                    # You are here
├── docs/                        # Project docs, persona spec, runbooks
├── scripts/                     # One-off scripts (ingestion, embedding, tagging)
│   └── ingest_proposals.py      # Sprint 1 — DOCX/PDF processing
├── skills/                      # Hermes agent skills
│   ├── retrieve_similar_proposals/
│   ├── propose_architecture/
│   ├── draft_proposal_section/
│   ├── assemble_and_export/
│   └── crawl_prospect/
├── supabase/
│   └── migrations/              # SQL migrations (schema + RLS)
│       └── 001_init.sql
├── backend/
│   └── hermes/                  # docker-compose, config, .env.example
│       ├── docker-compose.yml
│       └── .env.example
├── frontend/
│   └── cloudflare-worker/       # Auth proxy Worker code
├── data/
│   ├── raw/                     # Source proposals (gitignored, never commit)
│   ├── processed/               # Chunked JSON output from ingestion
│   └── tagging/                 # Metadata CSVs
├── traces/                      # Runtime logs (gitignored)
└── assets/
    └── diagrams/                # Reusable architecture diagrams (SVG/PNG)
```

## Status

**Current phase:** Sprint 0 (Foundation & Account Setup).

**Progress tracker:** See [`docs/PROJECT.md#7-detailed-sprint-instructions`](docs/PROJECT.md#7-detailed-sprint-instructions).

## Getting started (once Sprint 0 is complete)

```bash
# 1. Clone
git clone git@github.com:<your-org>/sarvam.git
cd sarvam

# 2. Copy env template and fill in
cp backend/hermes/.env.example backend/hermes/.env
# Edit backend/hermes/.env with your keys

# 3. Ingest proposals (Sprint 1)
python scripts/ingest_proposals.py --input data/raw --output data/processed

# 4. Apply Supabase schema (Sprint 2)
# Use Supabase dashboard SQL editor or supabase CLI

# 5. Embed and upload
python scripts/embed_and_upload.py

# 6. Start Hermes (Sprint 4)
cd backend/hermes && docker compose up -d
```

## Security notes

- **Never commit `.env` files** or anything under `data/raw/` (contains client proposals under NDA).
- **RLS is enforced at the database layer** — do not disable in production.
- **Hermes gateway is bound to localhost only** — all external access via Cloudflare Worker.
- Rotate API keys quarterly.

## Team

- **Imran** — Project Lead, Head of Marketing at IV
- **Ashish** — Technical Reviewer

## Licence

Proprietary — Inspirit Vision internal use only. Not for redistribution.
