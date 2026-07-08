# IV Proposal Architect — Ground-Zero Project Document

**Project Name:** IV Proposal Architect
**Persona / Product Name:** **Sarvam** (सर्वम् — Sanskrit for "all, everything, the whole")
**Owner:** Imran (Head of Marketing, Inspirit Vision)
**Version:** 1.0 (Planning — Ground Zero)
**Date:** July 2026
**Status:** Pre-execution planning; no services provisioned, no code written, no accounts subscribed.

---

## 1. Executive Summary

Inspirit Vision (IV) currently spends multiple person-days manually drafting each client proposal from scratch. With a bank of 100+ historical proposals spanning SailPoint, Ping Identity, IBM Security Verify, Keycloak, and ForgeRock engagements, there is enough reusable intellectual property to power a conversational, agentic AI system that drafts, diagrams, and delivers client-ready proposals in hours instead of days.

**IV Proposal Architect** is a conversation-first, self-improving proposal generator built on the Hermes Agent framework, powered by a tiered LLM routing strategy through OpenRouter, backed by a Supabase RAG store, and delivered to users through a persona-driven Open WebUI chat interface hosted on Cloudflare Pages.

### Success Criteria (V1)
- Reduce first-draft time from 2–5 days to under 2 hours.
- Generate both **Implementation** and **Managed Support (MSS)** proposal types.
- Human-in-loop architecture approval gate before content generation begins.
- Auto-compress output files from 20MB+ to under 5MB while preserving diagram quality.
- Self-improvement loop: every approved proposal enriches the RAG bank.
- Multi-user access with role-based data isolation (RLS).

---

## 2. Ground-Zero Inventory

**What we have today:**
- 100+ historical proposals (Word/PDF, ~20MB each due to embedded diagrams).
- 10 confirmed sample proposals analysed (Al Qadsiah, STC, ABB, DFCC, Mannai, NWC, Security Bank ForgeRock MSS, and 3 others).
- Clear pattern recognition: ~60% static/reusable, ~40% deal-specific dynamic content.
- Team: Imran (project lead), Ashish (technical reviewer), plus IV delivery team.

**What we do NOT have:**
- No cloud accounts (Oracle, AWS, Cloudflare, Supabase).
- No domain registered for this project.
- No OpenRouter account or API keys.
- No development environment, no repo, no CI/CD.
- No proposal ingestion pipeline built.
- No design system for the frontend.

**Assumption:** All costs must stay within free tiers for MVP; graduate to paid only after proving value.

---

## 3. Final Architecture (Baked-In)

### 3.1 Layer Stack

| Layer | Component | Role | Free-Tier Notes (July 2026) |
|---|---|---|---|
| Client | User Browser | Access point | — |
| Edge Frontend | Cloudflare Pages | Static Open WebUI shell | Unlimited bandwidth, 500 builds/month, 20K files per site ([Cloudflare docs](https://developers.cloudflare.com/pages/platform/limits/)) |
| Edge API | Cloudflare Workers | Auth proxy, request routing | 100K requests/day free ([Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)) |
| Agent Runtime | Hermes Agent (Docker) on AWS EC2 t4g.small | Reasoning, memory, skills | $200 credits over 6 months (new AWS accounts post July 2025); ~$15/month VM cost thereafter |
| Memory | Claude-Mem skill | Persistent, compressed session memory | Local SQLite/vector on VM disk |
| Research | Crawl4AI skill | Prospect enrichment | Runs in Hermes container |
| Retrieval | Custom `retrieve_similar_proposals` skill | RAG lookup against Supabase pgvector | — |
| Model Routing | OpenRouter | Provider-agnostic LLM gateway | Pay-per-use, no free tier |
| Data | Supabase (Auth + Postgres + Storage + pgvector) | Users, files, RAG vectors | 500MB DB, 1GB storage, 50K MAU, pauses after 7 days idle ([Supabase](https://supabase.com/pricing)) |
| Domain | Cloudflare-registered domain | Public URL + free HTTPS | ~$10/year (only unavoidable cost) |

### 3.2 LLM Routing Tier (OpenRouter)

- **Primary (bulk drafting):** DeepSeek V4 Flash — fastest, cheapest, 1M context.
- **Fallback 1:** GLM 5.2 — auto-triggered on primary rate-limit/outage.
- **Fallback 2 & escalation (compliance, pricing, final polish):** Claude Sonnet 4.6.
- **Never use as primary:** Kimi K2.6/K2.7 (mandatory thinking-mode latency tax).

### 3.3 Persona: Sarvam

The agent is not a generic assistant — he is **Sarvam**, IV's in-house Proposal Architect. Full persona, character traits, voice guide, and production system prompt live in the companion document **`Sarvam_Persona_Spec.md`**.

**Core traits (7):** Consultative not compliant · Precise on scope, conservative on claims · Vendor-agnostic by conviction · Bilingually/culturally aware · Structured but never robotic · Curious about the deal, not just the RFP · Self-aware about his limits.

**Signature opening:** *"Sarvam here — IV's Proposal Architect. New deal, or picking up something from earlier?"*

---

## 4. Conversational Workflow (Four Stages)

### Stage 1 — Discovery Interview
The agent asks targeted questions one at a time:
- Client name, industry, geography
- Proposal type: Implementation vs Managed Support (MSS)
- IAM/CIAM vendor preference
- Scale (users, applications, environments)
- Deployment model (on-prem / cloud / hybrid, HA/DR)
- Key integrations (HR, AD/Entra, SIEM, PAM, core banking, CRM)

### Stage 2 — Architecture Proposal & Human-in-Loop Gate
- Retrieve closest-matching past architecture from RAG bank.
- Render as MermaidJS diagram inline in Open WebUI chat.
- User approves OR rejects with comments.
- On rejection: agent regenerates, loops until approved.
- **Hard gate: no proposal drafting begins until architecture is locked.**

### Stage 3 — Full Proposal Assembly
- Static sections (Company Profile, Why-Vendor, Methodology) pulled near-verbatim from RAG.
- Dynamic sections (Executive Summary, Sizing, RACI, Timeline, Commercial) generated fresh, grounded in retrieved chunks.
- Compliance/pricing sections auto-escalate to Claude Sonnet 4.6.
- Compression module runs before final export.

### Stage 4 — Review, Deliver, Learn
- Section-level edit requests preserve approved sections untouched.
- Final approval → proposal saved back into RAG bank as new reference material.
- Delivery formats: "Full" (high-res, print-ready) and "Lite" (email-friendly, <5MB).

---

## 5. Contingency Matrix

| Failure Scenario | Response |
|---|---|
| Primary LLM (DeepSeek) rate-limited/down | OpenRouter auto-fallback to GLM 5.2, then Claude Sonnet 4.6 |
| Low retrieval confidence (weak match) | Flag draft as "template-based, needs review" — never force weak content |
| Validation fails twice | Escalate to human with failure notes, no third auto-retry |
| Crawl4AI blocked by prospect site | Skip enrichment gracefully, proceed with internal data |
| Cloudflare Worker can't reach backend VM | Return clear "temporarily unavailable" message, no silent timeout |
| Supabase quota hit (500MB DB / 5GB egress) | Queue writes, alert admin, degrade to read-only |
| EC2 instance stopped accidentally | Elastic IP retained, restart via console; volume state preserved |
| Supabase project pause (7 days idle) | Same — scheduled ping keeps project active |

---

## 6. Phase-Wise Delivery Plan

The build is organized into **6 Phases over 12 sprints of 1 week each** (~3 months total for a working MVP with the team's part-time availability). Each sprint is scoped tightly so it can be demoed or aborted independently.

### PHASE 0 — Foundation & Account Setup (Sprint 0, Week 0)
**Goal:** Every service account exists, every credential is stored securely, nothing is deployed yet.

### PHASE 1 — Data Foundation (Sprints 1–2, Weeks 1–2)
**Goal:** Supabase live, proposal bank ingested and embedded, retrieval works from a Python notebook.

### PHASE 2 — Agent Backend (Sprints 3–4, Weeks 3–4)
**Goal:** Hermes Agent running on Oracle VM, connected to OpenRouter with tiered fallback, Claude-Mem installed.

### PHASE 3 — Skills & Retrieval Integration (Sprints 5–6, Weeks 5–6)
**Goal:** Custom `retrieve_similar_proposals` skill working end-to-end; Crawl4AI installed; agent can draft a proposal section from a real RFP prompt in terminal.

### PHASE 4 — Conversational Frontend (Sprints 7–8, Weeks 7–8)
**Goal:** Open WebUI deployed to Cloudflare Pages, Worker auth proxy live, Supabase Auth wired in, Mermaid diagrams rendering, persona configured.

### PHASE 5 — Human-in-Loop, Compression & Export (Sprints 9–10, Weeks 9–10)
**Goal:** Architecture approval gate working, validation gates active, compression module producing <5MB output DOCX files.

### PHASE 6 — Pilot, Hardening & Rollout (Sprints 11–12, Weeks 11–12)
**Goal:** End-to-end testing against 5–10 real historical RFPs, security hardening, team rollout, feedback-loop close.

---

## 7. Detailed Sprint Instructions

### SPRINT 0 — Foundation & Account Setup (Week 0)

**Objective:** Zero-cost setup of every account and credential. No production resources yet.

**Deliverables:**
1. **Oracle Cloud Free Tier account** created — verify Ampere A1 quota (2 OCPU / 12 GB post-June 2026).
2. **Cloudflare account** created; register a domain (e.g. `proposals.inspiritvision.com` — the only unavoidable cost, ~$10/year).
3. **Supabase account** created; project provisioned in a region close to Pune (Singapore/Mumbai).
4. **OpenRouter account** created; funded with a small starter credit ($20 is enough for weeks of dev).
5. **GitHub organisation/repo** created: `iv-proposal-architect` (private).
6. **Password manager entry** for every credential; `.env.example` committed to repo.
7. **Project management board** (GitHub Projects or Linear) with all 12 sprints scaffolded as milestones.

**Definition of Done:**
- All login URLs bookmarked, credentials in 1Password/Bitwarden.
- Domain purchased and pointing to Cloudflare nameservers.
- Empty Git repo cloned locally with README, `.gitignore`, `.env.example`, and licence.
- No secrets committed. Ever.

**Cost this sprint:** ~$10 (domain) + $20 (OpenRouter credit) = **~$30 total.**

---

### SPRINT 1 — Proposal Bank Preparation (Week 1)

**Objective:** Convert the 100+ raw proposals into clean, chunked, metadata-tagged text ready for embedding.

**Deliverables:**
1. Central folder of all 100+ source files (DOCX/PDF) mirrored to a working directory.
2. Python ingestion script (`scripts/ingest_proposals.py`) that:
   - Extracts text from DOCX/PDF using `python-docx` and `pypdf`.
   - Extracts and separately stores embedded images/diagrams.
   - Splits each proposal into **logical sections** (Exec Summary, Company Profile, Scope, Architecture, Methodology, RACI, Timeline, Commercial) — not arbitrary character chunks.
3. Metadata schema per proposal:
   - `client_name`, `industry`, `country`, `iam_vendor`, `proposal_type` (implementation/MSS), `user_count`, `app_count`, `deal_size_bucket`, `outcome` (won/lost/pending), `year`.
4. Manual tagging pass — Imran + Ashish spend 2–3 hours tagging all 100+ files with the above metadata in a CSV.
5. Output: `data/processed/*.json` — one JSON per proposal containing chunked sections + metadata.

**Definition of Done:**
- 100+ proposals processed with no ingestion errors.
- Sample QA: pick 5 random files, verify sections were split correctly and metadata is accurate.
- CSV tagging file version-controlled (as anonymised metadata only — never commit raw client content).

**Blocker to watch:** OCR quality on scanned PDFs. If >10% of the bank is scanned, plan a Tesseract OCR pass.

---

### SPRINT 2 — Supabase Setup & Vector Ingestion (Week 2)

**Objective:** Supabase live with schema, RLS policies, and all 100+ proposals embedded and queryable.

**Deliverables:**
1. **Schema migration** (`supabase/migrations/001_init.sql`):
   - `organizations`, `org_members` (multi-tenant foundation).
   - `profiles` (linked to `auth.users`, holds preferences).
   - `proposals` (metadata table).
   - `proposal_chunks` (chunk text + `vector(1536)` column for embeddings).
   - `generated_proposals` (drafts created by the system).
   - `architecture_diagrams` (approved Mermaid definitions).
2. **RLS policies** on every table — users can only see their org's data. Test with two seed users in different orgs.
3. **pgvector HNSW index** on `proposal_chunks.embedding`.
4. **Embedding pipeline** (`scripts/embed_and_upload.py`):
   - Reads processed JSONs from Sprint 1.
   - Embeds each chunk with `text-embedding-3-small` (cheap, 1536-dim).
   - Batch-uploads to Supabase via the Postgres client.
5. **Retrieval smoke test** — Python notebook that queries "give me top 5 chunks similar to 'SailPoint IdentityIQ deployment for a Middle East bank'" and returns sensible results.

**Definition of Done:**
- Supabase project shows 100+ rows in `proposals`, ~2,000–4,000 rows in `proposal_chunks`.
- Retrieval notebook returns relevant chunks in <500ms.
- RLS blocks cross-org reads (verified with two test JWTs).
- Storage buckets created: `source-proposals` (raw files, private) and `generated-drafts` (private, RLS-scoped).

**Cost check:** Embedding 100 proposals @ ~30K tokens avg = ~3M tokens × $0.02/M = **~$0.06 one-time.**

---

### SPRINT 3 — AWS EC2 Provisioning & Docker Base (Week 3)

**Objective:** A hardened AWS EC2 instance running Docker, reachable via SSH, with Caddy ready to serve HTTPS.

**Deliverables:**
1. **EC2 instance provisioned:** `t4g.small` (ARM Graviton, 2 vCPU, 2GB RAM), Ubuntu 24.04 LTS ARM, 30GB gp3 EBS, in `ap-south-1` (Mumbai). Elastic IP attached for stable DNS.
2. **Security group:** ports 22 (SSH from your IP only), 80, 443 (open to world).
3. **SSH hardening:** key-only auth, root login disabled, fail2ban installed, ufw as second layer.
4. **Docker + Docker Compose** installed.
5. **Caddy** installed as system service — configured with the domain from Sprint 0, auto-obtains Let's Encrypt cert.
6. **Basic monitoring:** `htop`, `docker stats`, CloudWatch basic metrics (free tier), lightweight uptime cron.
7. **Cost alarm:** AWS Budget alert set at $10/month with email notification — catches runaway spend immediately.

**Note on RAM:** t4g.small has 2GB RAM. Hermes needs ~2-4GB comfortably. If we hit memory pressure in Sprint 4, upgrade to `t4g.medium` (4GB, ~$24/month) — the $200 AWS credit covers this fully for the MVP window.

**Definition of Done:**
- `https://propgen.<yourdomain>` returns a Caddy "hello world" page with valid HTTPS.
- SSH lockdown verified (password login fails, key login succeeds).
- Docker daemon runs on boot.
- VM survives a reboot with all services auto-starting.

---

### SPRINT 4 — Hermes Agent + OpenRouter Integration (Week 4)

**Objective:** Hermes Agent running in Docker, connected to OpenRouter with tiered fallback config, chatting successfully from the terminal.

**Deliverables:**
1. **Hermes docker-compose.yml** with two services (gateway + dashboard) sharing a named volume `hermes-data`.
2. **Gateway bound to `127.0.0.1:8642` only** — never exposed publicly. All external access goes through Caddy → dashboard.
3. **OpenRouter config** in `~/.hermes/config.yaml`:
   ```yaml
   model:
     provider: openrouter
     default: deepseek/deepseek-v4-flash
     fallback_chain:
       - deepseek/deepseek-v4-flash
       - z-ai/glm-5.2
       - anthropic/claude-sonnet-4.6
   ```
4. **Claude-Mem installed** as a Hermes skill; verify memory persists across container restarts.
5. **Caddy routes** `https://propgen.<domain>` → dashboard container on `127.0.0.1:8643`, with basic auth as an interim gate until Supabase Auth is wired in Sprint 8.
6. **First live chat** — Imran interacts with the agent via browser, asks "Summarize the SailPoint IdentityIQ product line" — verifies routing works.

**Definition of Done:**
- Agent responds via browser dashboard with valid content.
- Fallback verified by temporarily setting an invalid DeepSeek route and confirming GLM 5.2 handles the request.
- `.env` file has 600 permissions (`chmod 600`), never in Git.
- Container restart preserves memory (Claude-Mem test conversation still visible after `docker compose restart`).

**Cost check:** ~$2–5 in OpenRouter usage during Sprint 4 dev/testing.

---

### SPRINT 5 — Custom Retrieval Skill (Week 5)

**Objective:** Hermes can call a `retrieve_similar_proposals` skill that queries Supabase and returns ranked chunks with metadata filters.

**Deliverables:**
1. **Skill definition file** (`skills/retrieve_similar_proposals/skill.md`) with clear input/output schema:
   - Inputs: `query_text`, `filters` (vendor, industry, proposal_type, min_similarity).
   - Outputs: array of `{chunk_text, source_proposal_id, similarity_score, section_type}`.
2. **Skill implementation** — Python function that:
   - Embeds the incoming query.
   - Runs a pgvector similarity query against Supabase with metadata filters applied.
   - Returns top-N chunks above a **confidence threshold (default 0.75)**.
3. **Confidence gate logic:** if no chunk exceeds threshold, return `{status: "low_confidence", fallback: "template"}` — the agent must handle this gracefully.
4. **Section-type awareness** — retrieval prefers same-section-type matches (Exec Summary chunks match Exec Summary queries, not Commercial).
5. **Unit tests** covering: high-confidence match, low-confidence fallback, filter enforcement, RLS isolation.

**Definition of Done:**
- Skill callable from Hermes via natural language: "Find similar past proposals for a SailPoint deployment at a Middle East bank."
- Returns ranked chunks in <1s.
- Confidence gate confirmed: nonsense query returns `low_confidence` status, not garbage matches.

---

### SPRINT 6 — Crawl4AI & Proposal Drafting Loop (Week 6)

**Objective:** End-to-end draft generation from a real RFP text file, running entirely on the backend without any UI yet.

**Deliverables:**
1. **Crawl4AI skill** installed and callable — takes a company domain, returns cleaned Markdown snapshot of their About/Products pages.
2. **`draft_proposal_section` skill** — orchestrates: parse requirements → retrieve → check confidence → draft (via primary or escalation model) → return structured markdown.
3. **Proposal-type router** — detects "Implementation" vs "MSS" from user input, branches to correct section list (Implementation gets HLD/LLD, MSS gets SLA tiers).
4. **Full end-to-end test:** feed one of the past RFPs into the agent, let it produce a full draft as a single markdown file. Compare against the actual proposal that was originally sent.
5. **Log every retrieval + generation call** to a `traces/` folder for later analysis.

**Definition of Done:**
- Agent produces a coherent 8–15 page markdown draft from a real RFP in under 5 minutes.
- Draft matches the original proposal's structure and pulls in genuine static content from the RAG bank.
- Compliance/pricing sections are visibly stronger (Claude escalation working).
- Manual quality review by Imran + Ashish: rate draft on 1–10 scale; target ≥6/10 at this stage.

---

### SPRINT 7 — Open WebUI Deployment (Week 7)

**Objective:** Open WebUI backend containerised on the Oracle VM, frontend built and deployed to Cloudflare Pages.

**Deliverables:**
1. **Open WebUI Docker service** added to the Oracle VM docker-compose stack; connected to Hermes gateway as its "model provider" via the OpenAI-compatible API.
2. **Custom system prompt** for the "IV Proposal Architect" persona configured in Open WebUI's per-model settings.
3. **Frontend build pipeline** — clone Open WebUI, configure API base URL to point to a Cloudflare Worker (Sprint 8), build static assets, deploy to Cloudflare Pages via `wrangler pages deploy`.
4. **Custom domain** on Pages: `https://propgen.<yourdomain>` (or subdomain of choice).
5. **Mermaid rendering smoke test** — ask the agent "draw a simple architecture diagram for me", verify inline zoomable diagram appears.

**Definition of Done:**
- Static frontend loads globally via Cloudflare CDN.
- Chat works end-to-end through the frontend against the Hermes backend.
- Mermaid diagrams render inline with pan/zoom.
- Persona system prompt is active (agent introduces itself as "IV Proposal Architect" on first message).

---

### SPRINT 8 — Auth, Multi-Tenancy & Worker Proxy (Week 8)

**Objective:** Real user login working, JWTs propagated end-to-end, per-user data isolation confirmed.

**Deliverables:**
1. **Cloudflare Worker** at `api.propgen.<yourdomain>` that:
   - Validates Supabase JWTs on every request.
   - Forwards authenticated requests to Hermes backend.
   - Rejects unauthenticated requests with 401.
2. **Supabase Auth** enabled — email/password + magic link. Optional OAuth (Google) for convenience.
3. **Signup flow** creates `profiles` row and adds user to default `organizations` (IV) — Imran manually admits new users initially.
4. **User preferences UI** (basic): default LLM model, preferred output format, tone slider (formal/consultative).
5. **RLS verification** — create two test users in two different orgs; confirm one cannot see the other's drafts or uploaded RFPs.
6. **Remove interim Caddy basic auth** from Sprint 4 — replaced entirely by JWT gate at the Worker layer.

**Definition of Done:**
- Users log in via web UI, see only their own data.
- Worker rejects tampered/expired JWTs with clear error messages.
- Two-org isolation test passes.
- Auth traces (login, refresh, logout) visible in Supabase dashboard.

---

### SPRINT 9 — Human-in-Loop Architecture Approval Gate (Week 9)

**Objective:** The full architecture approval loop works — propose diagram, review, reject/refine, approve, then unlock drafting.

**Deliverables:**
1. **`propose_architecture` skill** — takes the discovery interview outputs, retrieves closest past architecture, renders as Mermaid, presents in chat.
2. **State machine** for proposal lifecycle:
   - `discovery` → `architecture_review` → `architecture_approved` → `drafting` → `review` → `final`.
   - Drafting cannot start unless `architecture_approved = true`.
3. **Rejection loop** — user rejects with comments in chat → agent regenerates diagram incorporating feedback → re-presents. Cap at 3 iterations, then escalate to human designer.
4. **Approved diagrams persisted** to `architecture_diagrams` table for future retrieval and reuse.
5. **Discovery interview flow** — agent asks the 6 discovery questions one at a time, stores answers to session context.

**Definition of Done:**
- Full test session: fresh chat → 6 discovery questions → architecture proposed → Imran rejects twice with different comments → third iteration approved → drafting begins.
- Approved Mermaid diagrams saved and retrievable.
- Drafting truly blocked when architecture is not approved (attempted bypass returns clear error).

---

### SPRINT 10 — Compression Module & Export Pipeline (Week 10)

**Objective:** Produce polished DOCX/PDF output that is <5MB, with images optimized and a "Lite" version available.

**Deliverables:**
1. **`assemble_and_export` skill** — takes the approved draft markdown, assembles into a templated DOCX using `python-docx` with IV branding (fonts, colors, logo, footer).
2. **Diagram library** — the 15–20 recurring architecture diagrams (SSO flow, JML flow, standard deployment topologies) stored as pre-optimized SVGs/PNGs in a shared asset folder, referenced by ID rather than re-embedded.
3. **Compression pass:**
   - All images resampled to 150 DPI max.
   - Converted to WebP/PNG-8 where possible.
   - Unused fonts stripped.
   - Tracked-change history and hidden bookmarks removed.
4. **Dual export** — one "Full" version (high-res for print) and one "Lite" version (<5MB, email-friendly).
5. **PDF export** via LibreOffice headless conversion (already available on Ubuntu VM).
6. **Delivery to user** — final files uploaded to Supabase Storage, signed URL returned in chat.

**Definition of Done:**
- A test proposal exports as both DOCX and PDF, Lite version under 5MB, Full version under 15MB.
- Visual quality review: diagrams still crisp, no visible degradation.
- IV branding correct (font, header/footer, page numbers, ToC).

---

### SPRINT 11 — Pilot Against Historical RFPs (Week 11)

**Objective:** Real-world validation. Feed 5–10 historical RFPs to the system, compare output to what was actually sent.

**Deliverables:**
1. **Pilot RFP set** — pick 10 diverse historical RFPs (mix of implementation/MSS, mix of vendors, mix of won/lost).
2. **Run each end-to-end** through the system: discovery → architecture approval → drafting → export.
3. **Scoring rubric** (per proposal):
   - Structural completeness (all expected sections present): /10
   - Static content accuracy (matches IV boilerplate): /10
   - Dynamic content quality (client-specific tailoring): /10
   - Architecture diagram accuracy: /10
   - Time to first draft (target <2 hours): mins
   - File size (target <5MB Lite): MB
4. **Gap report** — document every issue found (missing sections, wrong retrievals, hallucinated content, formatting bugs).
5. **Prioritized fix list** for Sprint 12.

**Definition of Done:**
- 10 test runs completed with scoring rubric filled in.
- Average score ≥7/10 across all dimensions.
- Zero hallucinated client references or fabricated pricing.
- Time-to-draft averaging under 2 hours per proposal.

---

### SPRINT 12 — Hardening, Rollout & Feedback Loop (Week 12)

**Objective:** Ship it. Fix the top gaps, harden security, onboard the IV delivery team, close the self-improvement loop.

**Deliverables:**
1. **Fix top 5 issues** from Sprint 11 gap report.
2. **Security hardening pass:**
   - All secrets in Docker Compose `.env` with 600 permissions.
   - Gateway confirmed bound to localhost only.
   - Worker rate-limits: max 60 requests/user/minute.
   - Supabase RLS re-audited.
   - No third-party Hermes skills installed from untrusted sources.
3. **Team onboarding docs** — 5-page runbook: how to log in, how to start a new proposal, how to review architecture, how to export.
4. **Self-improvement loop closed** — approved final proposals are automatically added to the `proposals` table and re-embedded, so the next generation is smarter.
5. **Monitoring dashboard** — simple page showing: today's requests, error rate, avg draft time, OpenRouter spend this month.
6. **Formal rollout** — Imran, Ashish, and 2 delivery team members granted access. Weekly office hours for the first month.

**Definition of Done:**
- System has drafted at least 3 real client proposals used in actual submissions.
- Team members can complete a full proposal without Imran's intervention.
- Monitoring dashboard live and checked daily for the first 2 weeks.
- Feedback capture process in place (in-app "was this section helpful?" thumbs).

---

## 8. Roles & Responsibilities

| Role | Person | Responsibility |
|---|---|---|
| Project Lead | Imran | Overall direction, prompt engineering, persona tuning, sprint reviews |
| Technical Reviewer | Ashish | Architecture quality gate, IAM technical accuracy, pricing review |
| Data Curator | Imran + delivery team | Proposal ingestion, metadata tagging, RAG bank curation |
| Backend Dev | Imran (or contracted) | Hermes skills, Supabase schema, Worker code |
| Frontend Dev | Imran (or contracted) | Open WebUI theming, IV branding, Cloudflare Pages deploy |
| Security | Imran | Auth, RLS, secrets, hardening pass in Sprint 12 |

---

## 9. Budget Estimate (12-Week MVP)

| Item | Cost |
|---|---|
| Domain registration | ~$10/year |
| AWS EC2 t4g.small | $0 for 6 months (covered by $200 signup credits), ~$15/month after |
| Cloudflare Pages + Workers | $0 (free tier) |
| Supabase | $0 (free tier during dev) |
| OpenRouter dev usage (12 weeks) | ~$100–200 |
| Embedding 100+ proposals (one-time) | <$1 |
| **TOTAL MVP** | **~$110–210** |

**Post-MVP paid graduation (when volume justifies):**
- Supabase Pro: $25/month (removes 500MB DB cap, adds backups, no idle-pause).
- OpenRouter production usage: budget $50–200/month depending on proposal volume.
- AWS EC2 post-6-month: ~$15/month (t4g.small) or ~$24/month (t4g.medium if we need more RAM).

---

## 10. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| AWS credit runs out unexpectedly | Low | Medium | Cost alarm at $10/month; t4g.small at $15/month is well within IV budget |
| Need more RAM than t4g.small offers | Medium | Low | Upgrade path to t4g.medium is 1-click and still ~$24/month |
| Supabase free-tier limits hit early | Medium | Medium | Monitor egress weekly; graduate to Pro before ingestion of proposal 200+ |
| Kimi K2.7-like slowness across all models | Low | High | Tiered fallback already mitigates; can add self-hosted small model if needed |
| RAG returns wrong past client's content | Low | Very High | Confidence gate + section-type filtering + human review gate |
| Hermes framework abandoned/deprecated | Low | High | Skills are markdown; RAG lives in Supabase; migration to another agent runtime feasible in ~1 sprint |
| Team adoption resistance | Medium | High | Sprint 12 office hours; keep manual proposal path available as fallback for first month |
| Client data leak via prompt injection | Medium | Very High | RLS enforced at DB layer, not app layer; audit all skills for input sanitization |

---

## 11. Out of Scope (V1)

Explicit non-goals to prevent scope creep:

- Real-time collaborative editing (single-editor per session in V1).
- Direct CRM integration (Salesforce/HubSpot push).
- Multi-language proposal generation (English only in V1).
- Automated pricing model — commercials always flagged for human input.
- SSO integration for IV corporate identity (email login sufficient for MVP).
- Public/prospect-facing portal — internal use only.
- Mobile-native app (responsive web is enough).

---

## 12. Post-V1 Roadmap (Not Committed)

- Multi-agent split (Research Agent, Pricing Agent, Compliance Agent) using Claude Flow patterns.
- Presenton integration for slide-deck versions of proposals.
- Fine-tune a small open-weight model on won proposals once corpus reaches 300+.
- Analytics: win/loss correlation with section content.
- Client-facing preview portal (read-only shared links).

---

## 13. Immediate Next Actions (Post-Sign-Off)

1. **Imran:** Complete Sprint 0 in the next 3–5 days (all account setups).
2. **Imran + Ashish:** Confirm the metadata schema for proposal tagging (Sprint 1 dependency).
3. **Imran:** Decide on domain name (`proposals.<something>.com`).
4. **Imran:** Set up weekly 30-min sprint review cadence (Fri afternoons?).
5. **Kickoff Sprint 1** the Monday after Sprint 0 close.

---

*Document owner: Imran. Version-controlled in `iv-proposal-architect/docs/PROJECT.md`. Update at the end of every sprint with actuals vs plan.*
