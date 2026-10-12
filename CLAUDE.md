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
  cache — per-request `asyncio` concurrency only; one draft per intake
  session and `SHILPI_MAX_CONCURRENT_DRAFTS` (2) overall, held in process.
- **Frontend** — Open WebUI, **pinned to a release by digest** in
  `deploy/Dockerfile.webui` plus a thin branding layer. Caddy terminates
  HTTPS in front of it; Open WebUI and the brain listen on loopback only.
  Do not bump the pin casually: the brain reads Open WebUI's uploads
  directory and its `file` table.
- **Data** — Supabase: Postgres 17 + pgvector for the proposal corpus and
  the partner-product corpus, six private Storage buckets, no Edge
  Functions. Row Level Security is on for every public table; the brain
  uses the service-role key, which bypasses it.
- **LLM** — OpenRouter. The defaults live in ONE place,
  `document_engine.PRIMARY_LLM_MODEL` / `FALLBACK_LLM_MODEL` (Gemini 3.8
  Flash, Qwen fallback, env-overridable); classification and diagram specs
  have their own chains in `app.py`. `GET /health` reports what is actually
  in effect. Read the code or `/health`, not a document, for model names.
- **Deploy** — `deploy/deploy.sh` on the one EC2 instance: builds
  `sarvam-brain:<commit>`, runs the suite inside that image, swaps, checks
  health, rolls back by itself. `deploy/rollback.sh` undoes it. CI (GitHub
  Actions) runs the suite on every push, plus the brain image with its real
  renderer stack and the Open WebUI image build. Operations:
  `docs/RUNBOOK.md`.

```bash
cd backend/brain
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Requires `OPENROUTER_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY` in the
environment — get these from whoever holds `scripts/sarvam.env` on the
host; never commit them.

Run the suite before any change and before calling anything done (the same
command CI runs; it needs no keys and `tests/conftest.py` blocks the network):

```bash
python -m pytest -q backend/brain/tests scripts
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
- **Never disable Row Level Security**, even to unblock local testing. It
  is on for every public table (the last one, `visual_assets`, was closed
  by `sarvam_018`); keep it that way.
- **Never read or edit `scripts/sarvam.env`.** Secrets stay host-only.
- **No client content, credentials, or infra secrets in the repo, ever** —
  including eval fixtures, corpus manifests and debug output. The repo is
  private since 2026-09-28, but it was public before and its history is
  what it is; treat it as if it still were. Verify a new ignore rule
  actually works with `git check-ignore -v <path>` before trusting it; a
  drafted-but-never-added rule has leaked client data once already.
- **Uncertain is not success.** "Fail-soft" is for optional things (a
  decorative image, a logo). A failed approval write, an unidentified
  uploader, an incomplete requirement read or an unmet precondition must be
  reported as such, never rendered as done (independent audit, 2026-10-12).
- **Authority lives in the database, not in the chat.** Session and
  proposal ids arrive inside chat markers a user can edit; check ownership
  and workflow state from stored rows before acting on them.
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

Read `11_PRODUCTION_PLAN.md` (the canonical plan), `RUNBOOK.md` (how the
service is operated) and `02_LESSONS.md` before your first change. Several
older documents below are history and say so; where a document and the code
disagree, the code and `GET /health` are right. Each line says what is inside
and when to open it.

- **`11_PRODUCTION_PLAN.md`** — release gate, sprint plan and status. The
  current plan; supersedes `09` and `PHASES.md` for anything it covers.
- **`RUNBOOK.md`** — deploy, rollback, restore, key rotation, access
  control, vendor kits, where to look when something breaks.
- **`USER_GUIDE.md`** — the one page a consultant reads.

- **`01_OPERATING_MANUAL.md`** — infra map, env vars and gotchas from the
  build phase. `RUNBOOK.md` is the current operating guide; use this for
  background on why things are the way they are.
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
- **`09_NEAR_TERM_SPRINTS.md`** — the sprint plan as of 2026-09-21.
  History: `11_PRODUCTION_PLAN.md` replaced it on 2026-09-25.
- **`10_FUTURE_VISION.md`** — speculative future state (multi-tenancy,
  outcome-weighted retrieval, regional/linguistic reach). Direction, not
  commitment — nothing here is scheduled.
- **`SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md`** — narrative record of the
  ESNAD pilot run, every bug it surfaced, Sprints 1-4, and this Claude Code
  migration itself. Read for the full story behind any of the above; skip
  it for day-to-day work.
