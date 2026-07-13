# Sprint 4 Spec — The Sarvam Brain (Hermes), Path B

_Approved 2026-07-13. Supersedes the original PROJECT.md Sprint 4 (which assumed a prebuilt "Hermes" Docker image that never existed)._

## Decision record

- **Path B chosen**: custom lightweight backend ("sarvam-brain"), NOT a Claude Code-style agent harness. Rationale: proposal drafting is high-stakes (compliance claims, product versions); we need grounding, per-claim citations, and human checkpoints — a controlled workflow, not an autonomous swarm.
- **Hermes** = internal codename for the agent brain. Sarvam = the product.
- Frame: **Hermes is not the writer. It is the proposal chief of staff** — reads the RFP, gathers facts, finds similar past work, outlines, drafts sections with evidence, flags gaps, learns from approved finals. Humans author; Hermes assists.

## Architecture (five organs)

| Organ | Tech | Status |
|---|---|---|
| Retrieval | Supabase pgvector (1,413 chunks, 11 proposals) | BUILT (Sprint 2) |
| Workflow loop | LangGraph (stage-gated: intake → fact pack → outline → section drafts → compliance matrix → human review → knowledge capture) | Sprint 4, phased |
| Structured verification | Instructor (compliance matrix, claim extraction) | Sprint 4 Phase 2 |
| Research | Exa (tier-1 search) + Firecrawl (tier-2 crawl) via MCP-shaped tool interface; headless browser = tier 3, DEFERRED | Sprint 4 Phase 3 |
| Memory / learning | mem0 (Playbook + lessons), re-ingest approved finals, win/loss tags | DEFERRED (Sprint 5+) |

## Key integration decision

sarvam-brain is a **FastAPI service exposing an OpenAI-compatible API** (`/v1/models`, `/v1/chat/completions`). Open WebUI adds it as a model connection ("Sarvam Architect"). Users chat with it like any model; internally it runs retrieve → draft → cite. No Open WebUI plugins/forks needed; swappable frontend later.

- Embeddings at query time MUST use `openai/text-embedding-3-small` via OpenRouter (same as ingest, ingest_v2.py).
- Drafting model: `deepseek/deepseek-v3.2-exp` default; escalation model for hard sections configurable.
- Retrieval: pgvector cosine similarity via Supabase RPC function `match_proposal_chunks` (to be created), top-k=8 default, with metadata (client_name, iam_vendor) attached.
- Every retrieved-fact sentence in output carries a citation like [STC §3.2]. System prompt enforces: no invented product versions/compliance claims; unknowns → "needs SME confirmation".

## Build order (MVP-first)

- **Phase 1 (this sprint, core)**: retrieval + grounded Q&A/drafting endpoint, deployed via docker compose next to Open WebUI, wired as a model. Prove: ask "summarize our SailPoint approach for banks" → grounded answer citing real chunks.
- **Phase 2 (this sprint)**: compliance-matrix tool — paste RFP text → structured requirement extraction (Instructor/JSON) → coverage check against corpus (covered / partial / missing / needs-human).
- **Phase 3 (this sprint, if time)**: Exa + Firecrawl research tools + verify-stage fact-checker (extract claims → re-search → flag mismatches).
- **Deferred**: mem0 memory, RAGAS eval harness, headless browser (Playwright/Camoufox), full LangGraph multi-stage orchestration (Phase 1-2 use a simple linear loop; LangGraph enters when stages multiply), Presenton deck output.

## Non-negotiable safety rails (from brainstorm w/ external review)

1. Every material technical claim: internal citation, external citation, or "needs SME confirmation" flag.
2. Every draft ships with an Assumptions & Open Questions section.
3. Hermes never finalizes/sends anything client-facing; human approval is the last gate, always.
4. Source-class labeling on all facts: internal / external / user-provided.
5. Human edits become Playbook lessons only when explicitly accepted (no silent learning from noisy diffs).

## Ops constraints

- EC2 t4g.small (2GB + 2GB swap): FastAPI service is fine (~150MB); hosted APIs for research (zero local RAM); NO resident headless browser.
- New secrets needed Phase 3: EXA_API_KEY, FIRECRAWL_API_KEY (vault + sarvam.env pattern, never in chat/git).
- Supabase access from brain: service_role key server-side only (never exposed to browser).
