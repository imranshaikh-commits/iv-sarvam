# Shilpi — Operating Manual

Everything needed to make a change and get it running. This is the file to read
when you need to *do* something, as opposed to understand the plan
(`docs/PHASES.md`) or the architecture (`README.md`).

---

## Infrastructure map

| Thing | Value |
|---|---|
| GitHub repo | `imranshaikh-commits/iv-sarvam` (**PUBLIC** — see warnings below) |
| Local clone (Imran's Mac) | `~/iv-sarvam` |
| EC2 host | `13.206.20.25` (ap-south-1 Mumbai), user `ubuntu` |
| SSH key | `~/Downloads/sarvam-server-key.pem` |
| Host repo path | `~/iv-sarvam` |
| Supabase project ref | `jthrjmiulefmyrqtwsnz` (region ap-northeast-1 / Tokyo, free tier) |
| Secrets file (host only) | `~/iv-sarvam/scripts/sarvam.env` — **never read or edit; Imran does** |

**The repo directory and Docker container names are still `sarvam`, deliberately.**
The product was renamed Sarvam → Shilpi in Aug 2026, but these internal
identifiers were left alone: renaming the containers would break Open WebUI's
stored connection to the brain (it points at `http://sarvam-brain:8000`, held in
OWUI's own database inside the persistent volume), and renaming the repo would
break every existing clone and remote for zero functional benefit.

### Containers

| Container | Port | Exposure |
|---|---|---|
| `sarvam-webui` (Open WebUI) | 8080 | **PUBLIC, plain HTTP** — known gap, Phase 7 |
| `sarvam-brain` (FastAPI) | 8000 | `127.0.0.1` only (correct) |

### Environment variables (host, in `scripts/sarvam.env`)

All prefixed `SHILPI_` after the rename. The important one:

```
SHILPI_DIAGRAM_MODELS=anthropic/claude-sonnet-4.6,google/gemini-2.5-flash,z-ai/glm-5.2
```

This routes **only** diagram-spec generation to a stronger model. General
drafting and chat stay on the default chain (GLM 5.2 primary, Qwen fallback).
Diagram specs are the hardest structured output in the system and GLM proved
marginal at them — it returned empty and edgeless specs that were schema-valid.

Other tunables: `SHILPI_DIAGRAM_SPEC_TIMEOUT_S` (180), `SHILPI_ARCH_ROUND_BUDGET_S`
(240), `SHILPI_ARCH_CONCURRENCY` (3), `SHILPI_D2_LAYOUT` (elk),
`SHILPI_MIN_SPEC_NODES` (2).

---

## The deploy workflow

Claude has no host access. Every change follows this path:

**1. Claude** edits its sandbox clone, runs the tests, stages files to
`/mnt/user-data/outputs/`, and presents them.

**2. Imran — Mac.** One command per paste:

```bash
cd ~/iv-sarvam && git pull origin main
```
```bash
S="$HOME/Downloads/<folder>"; cp "$S/<file>.py" backend/brain/
```
```bash
git --no-pager status --short
```
> Always check this before committing. `--no-pager` matters: without it the git
> pager swallows batched commands and the sequence appears to hang.

```bash
git add -u backend/brain && git commit -m "<message>"
```
```bash
git push origin main
```

**3. Imran — host** (only if `backend/brain/` or `deploy/` changed; docs-only
changes never need this):

```bash
ssh -i ~/Downloads/sarvam-server-key.pem ubuntu@13.206.20.25
```
```bash
cd ~/iv-sarvam && git pull origin main
```
```bash
cd deploy && docker compose up -d --build sarvam-brain
```
```bash
sleep 12; curl -s http://127.0.0.1:8000/health; echo
```

Healthy output includes `"model":"shilpi-architect"`.

**4. Claude verifies** by cloning the repo fresh and running the suite against
the pushed commit — not against its own working copy.

---

## Gotchas that have bitten before

- **Dockerfile COPY allowlist.** `backend/brain/Dockerfile` lists source files
  explicitly. A new `.py` module not added there is missing at runtime and the
  container crash-loops. Tests are not copied into the image.
- **Env var changes need a container restart**, not just a rebuild of code:
  `docker compose up -d sarvam-brain` after editing `sarvam.env`.
- **Angle-bracket placeholders get pasted literally.** Writing
  `SHILPI_DIAGRAM_MODELS=<your model>` resulted in the literal string
  `<your model>` landing in the env file. Give real values or name the file to
  edit and let Imran fill it in.
- **Supabase free tier idle-pauses at 7 days.** A host cron curls
  `GET /v1/keepalive` daily (`54 0 * * *`) to touch Postgres. If the project
  pauses, everything fails with connection errors.
- **`docker compose` must run from `~/iv-sarvam/deploy`** or it reports
  "no configuration file provided". Plain `docker logs sarvam-brain` works from
  anywhere.

---

## Running the tests

From `backend/brain/`, with `d2` on PATH:

```bash
for t in test_chat_state test_chat_interview_gating test_proposal_depth \
         test_intake_template test_export_engine test_document_qa; do
  python3 tests/$t.py
done
python3 -m pytest tests/test_diagram_engine.py tests/test_document_engine.py -q
```

Roughly 190 tests. They are the safety net for every change — several times a
"clean" refactor was caught by a test asserting old behaviour, which is exactly
the point. When a test fails after a change, decide honestly whether the test is
stale (update it, with a comment explaining why the behaviour changed) or the
change is wrong.

---

## Where things live

```
backend/brain/
  app.py                  FastAPI, chat state machine, endpoints, model chains
  chat_state.py           Conversation modes, intent classification, diagram plan
  document_engine.py      Section drafting, discovery routing, DOCX assembly
  document_qa.py          Deterministic QA gate (degeneration, citations, meta)
  diagram_engine.py       DiagramSpec -> D2/ELK -> librsvg -> PNG, IV brand style
  proposal_templates.py   Section specs per proposal type, depth tiers
  intake_template.py      The 22-area discovery schema
  branding.py              IV colours, fonts, title page, headings
  supabase_client.py      Postgres + storage access
  export_engine.py        PDF export
deploy/                   docker-compose.yml, Dockerfile.webui, patch-webui.py
scripts/                  Ingestion, corpus curation; sarvam.env (host only)
supabase/migrations/      Schema
docs/                     PHASES.md (plan), evals/ (gitignored fixtures)
```
