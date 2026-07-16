# Sarvam — New Account Startup Prompt

> Copy the block below and paste it as your **first message** in the new Perplexity account (the one with credits). It orients a fresh agent to the project and gets it working on Pass 3 immediately, without burning credits on re-discovery.
>
> The full handover is at [`docs/HANDOVER.md`](HANDOVER.md) — the agent reads it as step 1.

---

## Copy from here ↓

@GitHub @Supabase @AWS I'm continuing the **Sarvam** project (Inspirit Vision's Proposal Architect — a conversational, retrieval-grounded AI that drafts client proposals from IV's 100+ historical proposal bank) on this account. Pick up exactly where we left off — **do NOT re-discover or re-analyze anything already done.**

**Do these first, in order:**
1. Read the handover doc end-to-end: https://github.com/imranshaikh-commits/iv-sarvam/blob/main/docs/HANDOVER.md — it has the full project status, architecture, the 5-pass enhancement sprint, hard rules, and exact next steps.
2. Read `docs/PROJECT.md` (the original 6-phase/12-sprint blueprint) and `README.md` on `main`.
3. Connect the **GitHub / Supabase / AWS** connectors if not already connected (repo `imranshaikh-commits/iv-sarvam`; Supabase project `imranshaikh-iv-sarvam`, Tokyo region; Sarvam EC2 host in Mumbai). I will reconnect them when prompted — **do not ask me for keys or paste any secrets.**
4. Recreate the **daily Supabase keep-alive** scheduled task (recipe in handover §15): cron `9 0 * * *` UTC, background, ping `SELECT count(*) FROM organizations`, restore the project if paused, notify me only on failure. If your system needs confirmation before creating a scheduled task, ask me once; otherwise proceed from the handover recipe.
5. Verify live state for a fresh baseline: confirm `main` and `sprint5-doc-engine` HEADs and the Supabase table/row counts (8 tables, 11 proposals, 1,413 chunks, 5 migrations expected).

**Where we are:**
- Phases 0–3 done. Phase 4 partial (Open WebUI deployed; Supabase Auth/Worker/multi-tenancy not wired). Phase 5 in progress via a 5-pass enhancement sprint.
- Pass 1 (intake + persistence, `7622a4d`, migration 005 applied) — DONE. Pass 2 (DOCX branding, `b4d42b0`) — DONE. Both merged to `main`.
- LLM stack: GLM 5.2 primary + Qwen3 235B fallback, hardcoded. DeepSeek removed. Embeddings: text-embedding-3-small (1536-dim).

**Immediate next task — Pass 3 (long-form depth):**
- `proposal_depth` tiers `brief` / `standard` / `full` (control via number of subsections drafted + retrieval fan-out, not a single giant call).
- `full` adds multi-subsection drafting + appendices: RACI, timeline, sizing, integration inventory, risks.
- Preserve per-call token caps + frequency penalty (prevents the repetition spiral that killed DeepSeek).
- Existing `/v1/generate-proposal` calls must still work with a safe default.
- Acceptance criteria + full scope are in handover §9.

**Load the `coding` skill and delegate Pass 3 to a `codebase` subagent** (managed clone from `https://github.com/imranshaikh-commits/iv-sarvam`, branch from `sprint5-doc-engine`); do not manually inspect source code unless your system requires it.

**Hard rules (full list in handover §10):** don't touch WordPress Lightsail; never commit `.env` or `data/raw/`; RLS enforced — don't disable; the brain is localhost-only; **no EC2 SSH for the agent** (I run all host commands — you give me the exact commands); public repo — never put the EC2 public IP, Supabase project ref, or any key in committed docs.

Read the handover doc, confirm the live state to me, then begin Pass 3.

## ↑ Copy to here
