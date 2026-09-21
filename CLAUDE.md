# CLAUDE.md

## What this is

Shilpi (शिल्पी, Sanskrit for *"architect, craftsman, one who shapes,"*
pronounced *SHIL-pee*) is Inspirit Vision's AI Proposal Architect: a
conversational, retrieval-grounded system that runs a 22-area discovery
interview (or reads an uploaded client RFP/SOW), generates architecture
diagrams with a human approval gate, and drafts a full IAM proposal
(DOCX/PDF) grounded in IV's own bank of past proposals.

**Naming, explained once here so it never needs re-explaining:** the
product was renamed **Sarvam → Shilpi** in August 2026. Every internal
identifier — Docker container names (`sarvam-webui`, `sarvam-brain`), the
compose project name, the GitHub repo (`iv-sarvam`) and local clone
directory, the `scripts/sarvam.env` path — was **deliberately left as
`sarvam`**. Renaming the containers would have broken Open WebUI's stored
connection to the brain (held inside OWUI's own database, pointing at
`http://sarvam-brain:8000`); renaming the repo would have broken every
existing clone and remote for zero user-visible benefit. Only what a user
actually sees changed: `WEBUI_NAME`, the model id (`shilpi-architect`),
generated filenames, persona docs. Full detail, including why each specific
identifier was or wasn't touched: `06_PRODUCT_DECISIONS.md`.

## Stack and running it locally

- **Backend** (`backend/brain/`) — FastAPI, Python 3.12. Exposes an
  OpenAI-compatible chat endpoint that Open WebUI calls. No job queue, no
  cache — per-request `asyncio` concurrency only.
- **Frontend** — Open WebUI (upstream image plus a thin branding layer,
  `deploy/Dockerfile.webui`).
- **Data** — Supabase: Postgres 17 + pgvector for the proposal corpus and
  the (currently empty) partner-product corpus, five private Storage
  buckets, no Edge Functions.
- **LLM** — OpenRouter. Drafting: Claude Sonnet 5 → GLM 5.2 fallback.
  Diagram specs and embeddings use their own model/chain — see
  `01_OPERATING_MANUAL.md`.
- **Deploy** — manual, no CI. `docker compose` on one EC2 instance; exact
  sequence in `01_OPERATING_MANUAL.md`.

```bash
cd backend/brain
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Requires `OPENROUTER_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY` in the
environment — get these from whoever holds `scripts/sarvam.env` on the
host; never commit them.

Run the suite before any change and before calling anything done:

```bash
cd backend/brain
for t in test_chat_state test_chat_interview_gating test_proposal_depth \
         test_intake_template test_export_engine test_document_qa; do
  python3 tests/$t.py
done
python3 -m pytest tests/ -q
```

## Conventions and constraints

- **New `.py` module → add it to `backend/brain/Dockerfile`'s COPY line**,
  or the container crash-loops on import. The single most-repeated mistake
  in this project's history.
- **A new parameter needs a test asserting the CALLER passes it**, not just
  that the function accepts it. "Built but never wired" — a mechanism
  implemented but never connected to the path that would invoke it — is
  this project's most recurring bug class.
- **Check the actual return value of every Supabase write.**
  `patch_intake_answers` and similar return `None` on failure rather than
  raising; code that discards that value can tell a user "saved" when
  nothing persisted.
- **Never trust a fix until measured.** For anything visual (diagrams,
  layout), produce a number and compare it to a known-good control —
  "looks right" has been wrong before.
- **One variable at a time.** Isolate changes so cause and effect stay
  legible; run a scored baseline before changing anything that touches
  drafting quality or retrieval.
- **Never disable Row Level Security**, even to unblock local testing. One
  table (`visual_assets`) already has it off by accident — see
  `03_CURRENT_STATE.md` — that is a bug to fix, not a pattern to extend.
- **Never read or edit `scripts/sarvam.env`.** Secrets stay host-only.
- **The repo is PUBLIC.** No client content, credentials, or infra secrets
  ever — including in eval fixtures, corpus manifests, or debug output.
  Verify a new ignore rule actually works with
  `git check-ignore -v <path>` before trusting it; a drafted-but-
  never-added rule has leaked client data once already.
- **Citations never reach a client-facing document.** Numbered markers are
  for internal grounding only, stripped before export — not just hidden in
  an "internal" variant.
- **Length is not a quality target.** Closed as an unvalidated proxy; do
  not reintroduce a word- or page-count goal.
- **A hard token/length cap is a safety net, never the thing shaping
  normal output.** When raising one limit, grep for every other cap on the
  same call path — the one you changed is rarely the binding one.
- **One command per code block** for anything the operator runs by hand —
  multi-command `&&` chains have repeatedly failed mid-sequence when git
  opens a pager.

## Reference docs (`docs/`)

Read `01`, `05`, and `09` before your first change — the last one is the
actual pending-work plan. The rest, as needed — each
line below says what's inside and when to open it.

- **`01_OPERATING_MANUAL.md`** — infra map, containers, env vars, the exact
  deploy sequence, gotchas, how to run the test suite. Read before
  deploying or debugging infra.
- **`02_LESSONS.md`** — 12 recurring failure patterns from this project's
  history (built-but-never-wired, silent truncation, asserting instead of
  measuring, and others). Read before touching retrieval, diagram
  rendering, or anything with a Postgres function.
- **`03_CURRENT_STATE.md`** — a point-in-time status snapshot. Stale as of
  this migration (predates the ESNAD pilot and Sprints 1-4) — treat
  `README.md`'s Progress Dashboard as more current until this is
  refreshed.
- **`04_EVAL_BASELINE.md`** — the Amlak benchmark methodology, scoring
  rubric, and full run history. Read before scoring any change to
  drafting or retrieval quality.
- **`05_SESSION_KICKOFF.md`** — the first-five-minutes checklist for
  starting a session: what to clone, what to read, how to establish a
  test baseline. Read this first, always.
- **`06_PRODUCT_DECISIONS.md`** — product principles (the V1
  no-draft-before-approval gate, no invented references, no page-count
  target), naming/identity decisions, and the cost model. Read before
  changing what Shilpi is allowed to do or say.
- **`07_CORPUS_RULES.md`** — what may and may not enter the proposal
  corpus, curation and ingestion discipline, why the bank is one Supabase
  table rather than several. Read before touching ingestion, curation, or
  retrieval scaling.
- **`08_TECHNICAL_CONVENTIONS.md`** — engineering conventions and the
  tooling-choice table (D2 over Graphviz, why hybrid search was rejected,
  and others) with the reasoning behind each. Read before swapping a
  library or reversing a prior technical decision.
- **`09_NEAR_TERM_SPRINTS.md`** — the actual current sprint plan,
  rewritten 2026-09-21. What Sprints 1-4 closed, exact recommended order
  for everything pending (Sprint 7 content gathering, Sprints 5/6/8, the
  held-back validation run), and what's still unresolved and needs a
  person rather than code. Read this before picking up any new work.
- **`10_FUTURE_VISION.md`** — speculative future state (multi-tenancy,
  outcome-weighted retrieval, regional/linguistic reach). Direction, not
  commitment — nothing here is scheduled.
- **`SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md`** — narrative record of the
  ESNAD pilot run, every bug it surfaced, Sprints 1-4, and this Claude Code
  migration itself. Read for the full story behind any of the above; skip
  it for day-to-day work.
