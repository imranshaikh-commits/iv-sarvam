# Brief for Claude: Sarvam Executive Budget-Approval Deck

**Audience of this brief:** Claude (the AI you'll paste this into to generate a PowerPoint)
**Ultimate audience of the deck:** Imran's bosses at Inspirit Vision (IV)
**Deck's purpose:** Convince IV leadership to approve budget for Perplexity Computer credits to finish building **Sarvam**, IV's in-house AI Proposal Architect.

---

## SECTION 1 — What Claude should know before starting

### 1.1 About the presenter and the audience

- **Presenter:** Imran Shaikh, Head of Marketing, Inspirit Vision
- **Audience:** IV senior leadership (founder/directors) — technical enough to understand cloud infrastructure at a high level, but the deck must lead with **business value, not tech jargon**
- **Presenter's ask:** Approval of a spending budget (rough order: ₹30,000–₹50,000, exact framing in Section 8 below) to complete Sarvam over the next ~10 weeks
- **Emotional posture of the deck:** confident, evidence-based, humble about what's left, transparent about costs

### 1.2 About the project

**Sarvam** (सर्वम् — Sanskrit for "all, everything, the whole") is IV's in-house AI Proposal Architect. IV writes hundreds of technical proposals for IAM/cybersecurity deals — currently taking **2 to 5 days of senior-consultant time each**. Sarvam brings that down to **under 2 hours** while preserving quality and enforcing consistency across the delivery team.

Two days of intensive work (July 7–8, 2026) have already produced a live infrastructure stack. The remaining ~8–10 weeks build the agentic brain, retrieval pipeline, human-in-loop gates, and export system.

### 1.3 About branding and tone

- **Product name:** Sarvam (never "the AI", "the bot", "the tool")
- **Company:** Inspirit Vision (abbreviated IV in internal use only — spell out "Inspirit Vision" on the title and closing slides)
- **Colors:** IV logo colors (blue-dominant, corporate palette) — Claude should ask user to attach or describe
- **Tone:** Consultative, no hype language, no emojis, no exclamation points. Data-forward. Direct.
- **No corporate cliches:** avoid "revolutionize", "game-changer", "disrupt", "cutting-edge", "harness the power of AI"

### 1.4 Deck length

Target **22–28 slides**. Include an appendix (3–5 slides) that can be skipped in-meeting but referenced if asked. Total meeting time should be **~15 minutes presentation + 15 minutes Q&A**.

---

## SECTION 2 — Slide-by-slide structure

Claude, use this as your slide backbone. Each entry says: slide title, purpose, and content to include. Match this order.

### Slide 1 — Title
- **Title:** Sarvam — IV's In-House Proposal Architect
- **Subtitle:** Budget approval request | Q3 2026 | Imran Shaikh, Head of Marketing
- Include IV logo
- Small caption at bottom: "सर्वम् — Sanskrit for 'all, everything, the whole'"

### Slide 2 — The 60-second pitch
One slide, three bullets:
- **The problem:** IV proposals cost 2–5 days of senior time each. We write hundreds a year.
- **The fix:** Sarvam — an AI Proposal Architect trained on IV's own past proposals. Drafts a full proposal in under 2 hours, human-approved architecture, IV-branded output.
- **The ask:** ₹30,000–₹50,000 to finish building it. 40% of the work is already done at no incremental cost. ROI recovers in the first two proposals it drafts.

### Slide 3 — Why now, why us
- IAM proposal writing is a solved pattern: ~60% of every proposal is reusable content, ~40% is deal-specific.
- Every senior consultant hour spent on boilerplate is an hour NOT spent on architecture, client meetings, or delivery.
- LLMs finally crossed the reliability threshold in 2025–26 to be trusted for technical drafting (Vectara hallucination leaderboard shows top models at 4–5% — an acceptable rate for human-reviewed output).
- IV has the data moat: **100+ past proposals** across SailPoint, Ping, IBM Security Verify, Keycloak, ForgeRock. No competitor consultancy of our size has this dataset ready.

### Slide 4 — What Sarvam is (in one visual)
Draw a simple diagram (Claude, use PowerPoint SmartArt or a clean box-and-arrow):

```
[RFP arrives] → [Sarvam interviews delivery team]
              → [Sarvam proposes architecture]
              → [HUMAN APPROVAL GATE 🖐️]
              → [Sarvam drafts full proposal]
              → [Sarvam compresses to <5MB DOCX/PDF]
              → [Delivery team reviews + sends to client]
              → [Won proposals feed back into Sarvam's memory]
```

Include a caption: "Sarvam does one thing extremely well — help IV win business by drafting sharp, honest, technically credible proposals faster than any human alone."

### Slide 5 — Who is Sarvam? (the persona)
Two-column layout:

**Left column — Character:**
- Sanskrit for "all, everything, the whole"
- Consultative, not compliant (pushes back on unclear requests)
- Precise on scope, conservative on claims (never invents client references or metrics)
- Structure-first thinking (proposals follow IV's proven section flow)
- Human-in-loop, no shortcuts on architecture

**Right column — What Sarvam WON'T do:**
- Won't invent client names or success metrics
- Won't fill in pricing (that's always the delivery team's call)
- Won't start drafting until architecture is human-approved
- Won't be sycophantic or generate marketing fluff
- Won't touch production systems (like the WordPress website)

### Slide 6 — The AI brain: how Sarvam thinks
Show a table with a header row and 5 rows:

| Role | Model | Why this one | Cost |
|---|---|---|---|
| **Primary brain (drafting)** | DeepSeek V3.2 (via OpenRouter) | 5.3% hallucination rate — near the top of the Vectara leaderboard | $0.14 per million input tokens |
| **Fallback #1** | Llama 3.3 70B (Meta, via OpenRouter) | 4.1% hallucination — the safest option — used when DeepSeek is uncertain | $0.59 per million input tokens |
| **Fallback #2** | Qwen 3 14B (Alibaba, via OpenRouter) | 5.4% hallucination, cheapest — used for quick jobs | $0.05 per million input tokens |
| **Embeddings (memory)** | OpenAI text-embedding-3-small (via OpenRouter) | Industry standard, 1536 dimensions, matches our database | $0.02 per million tokens |
| **Vision (diagram OCR)** | Qwen 3 VL 8B (via OpenRouter) | Reads architecture diagrams from past proposals | Cheap ($0.05–0.10) |

Caption: "One vendor (OpenRouter) routes to five different specialist models. If any model goes down or degrades, we switch with a one-line config change."

### Slide 7 — How Sarvam collects RFP details from the delivery team
This is a critical slide. Show as a numbered flow:

1. **Delivery team member opens Sarvam in the browser** (currently at http://13.206.20.25:8080, will move to sarvam.inspiritvision.com in Sprint 7)
2. **Sarvam runs a structured discovery interview** — asks specific questions:
   - Client name, industry, geography
   - What did they ask for? (paste the RFP or upload the doc)
   - Preferred vendor stack (SailPoint / Ping / IBM / Keycloak / ForgeRock / open)
   - Scale: users, apps, integrations
   - Timeline expectations
   - Any known constraints (regulatory, budget, existing infra)
3. **Sarvam summarizes what it heard, back to the team member,** for correction — same way a senior consultant would repeat back their understanding before drafting
4. **Sarvam retrieves the 3–5 most similar past proposals from its memory** (Supabase pgvector database) to anchor its drafting on IV's real historical patterns

### Slide 8 — How Sarvam proposes the architecture (and the human gate)
Show a two-stage diagram:

**Stage 1: Sarvam's proposal**
- Sarvam generates a text description of the recommended architecture
- Sarvam generates a Mermaid or PlantUML diagram code (an architecture diagram in code form)
- Sarvam explains WHY: "I recommend Ping AIC for this deal because — Bank X, Bank Y, and Telco Z in your past proposals used the same stack for similar user counts."

**Stage 2: Human approval gate**
- Delivery team member sees the proposed architecture
- **Three response options** for the human:
  - ✅ **Approve** — Sarvam proceeds to draft the full proposal
  - ✏️ **Approve with edits** — human adjusts the diagram/text, Sarvam accepts
  - ❌ **Reject with feedback** — human explains what's wrong, Sarvam re-proposes

Add a highlighted callout box:
**"Sarvam NEVER starts drafting until a human approves the architecture. This is a hard gate — not a suggestion."**

### Slide 9 — 🆕 What if Sarvam's architecture proposal keeps missing the mark?
**(This is the new capability Imran wants to build in — see Section 4 of this brief for full detail. Claude, make this a strong slide because it addresses a legitimate concern: "what if the AI keeps getting it wrong?")**

Title: "When Sarvam is stuck, it asks for help — in three ways."

Show three options, each with a small illustration:

**Option A — Hand-drawn sketch upload**
- Delivery team sketches the architecture on paper or a whiteboard
- Snaps a photo, uploads to Sarvam
- Sarvam's vision model (Qwen 3 VL) reads the sketch and converts it to a clean digital diagram

**Option B — Flowchart or diagram file upload**
- Team uploads a Draw.io, Lucidchart, Visio, or PowerPoint diagram
- Sarvam parses it and generates a matching official-format IV diagram
- Preserves the team's intent while applying IV branding and standards

**Option C — Algorithmic step-by-step input**
- Team types a plain-English sequence: "Step 1: user hits load balancer. Step 2: SSO redirects to Ping AIC. Step 3: SAML assertion posted to app…"
- Sarvam converts the steps to a formal architecture diagram
- Team can iterate ("no, put the WAF before the load balancer") until it's right

Caption: **"This means Sarvam never becomes a blocker. If the AI can't figure it out, the human takes 5 minutes to sketch, and Sarvam takes it from there."**

### Slide 10 — How Sarvam drafts the proposal (section by section)
Show as a timeline:

- **Section 1: Company Profile** — pulled from IV's canonical company deck (static, reused)
- **Section 2: Understanding of Scope** — Sarvam writes fresh, based on RFP content
- **Section 3: Similar Experience** — Sarvam retrieves 2–3 relevant past proposals from memory, presents anonymized case studies
- **Section 4: Solution Architecture** — the human-approved architecture from Slide 8
- **Section 5: Implementation Approach** — Sarvam retrieves the closest matching implementation plan from past proposals
- **Section 6: Team & RACI** — pulled from IV's team roster, filtered for relevant expertise
- **Section 7: Timeline** — Sarvam estimates based on similar past deals
- **Section 8: Commercials** — **Sarvam sets up the table structure. Numbers are ALWAYS filled by the delivery team.** Never automated.
- **Section 9: Assumptions & Exclusions** — Sarvam drafts standard language, team edits

Below the timeline, add: "Every section links back to the source proposals in Sarvam's memory. Full traceability. No black box."

### Slide 11 — Compression and delivery
Two-part slide:

**Left:** The current pain
- Standard IV proposals: 20–30 MB DOCX (embedded diagrams, images)
- Too big to email
- Clients complain
- Manual compression takes another hour

**Right:** How Sarvam solves it
- Automatic image compression (95% quality preserved)
- Vector-based diagrams instead of raster
- Font subsetting
- Target output: **under 5 MB** without visual quality loss
- One-click DOCX + PDF export

### Slide 12 — Self-improvement loop
Show a circular diagram:

1. Sarvam drafts a proposal
2. Delivery team edits and sends
3. **When the deal is WON**, the final version gets fed back into Sarvam's memory
4. Next time a similar deal comes in, Sarvam has a stronger anchor
5. Repeat

Caption: "Every won proposal makes the next one 5% sharper. Institutional knowledge compounds."

---

### Slide 13 — Where we are today (the honest status update)

This is a CRUCIAL slide. Show a Gantt-style timeline or a progress bar.

**Total project scope:** 13 sprints across 6 phases (12 weeks planned)

**Current position:** End of Sprint 2 (mid-Phase 1)

| Phase | Sprints | Weeks | Status | % Complete |
|---|---|---|---|---|
| **Phase 0 — Foundation** (accounts, servers, DB) | Sprint 0 | Week 0 | ✅ **DONE** | 100% |
| **Phase 1 — Data Foundation** (proposal bank, ingestion) | Sprints 1–2 | Weeks 1–2 | 🟡 **In progress** | ~75% |
| **Phase 2 — Agent Backend** (Hermes brain, LLM integration) | Sprints 3–4 | Weeks 3–4 | ⏳ Not started | 0% |
| **Phase 3 — Skills & Retrieval** (RAG, drafting loops) | Sprints 5–6 | Weeks 5–6 | ⏳ Not started | 0% |
| **Phase 4 — Conversational Frontend** (Open WebUI polish, auth) | Sprints 7–8 | Weeks 7–8 | ⏳ Not started | 0% |
| **Phase 5 — HITL, Compression, Export** | Sprints 9–10 | Weeks 9–10 | ⏳ Not started | 0% |
| **Phase 6 — Pilot & Rollout** | Sprints 11–12 | Weeks 11–12 | ⏳ Not started | 0% |

**Overall project completion: ~28%**
**Overall project remaining: ~72%**

Show a big number in the corner: **"28% done in 2 days of intensive work."**

### Slide 14 — What we already have running (the receipts)
Show a checklist with green checkmarks, to prove this isn't vaporware:

- ✅ AWS EC2 server live in Mumbai (13.206.20.25)
- ✅ Elastic IP attached (static, survives reboots)
- ✅ Docker container running Open WebUI on port 8080
- ✅ IV logo branded into favicon, splash, and title
- ✅ Supabase database live in Tokyo region (Postgres 17.6 + pgvector 0.8.2)
- ✅ 7 database tables with Row Level Security enabled
- ✅ Vector search function ready (`match_proposal_chunks`)
- ✅ GitHub repo `imranshaikh-commits/iv-sarvam` with 739 lines of ingestion code
- ✅ OpenRouter account with API key working
- ✅ Model stack locked: DeepSeek V3.2 + Llama 3.3 + Qwen 3 + OpenAI embeddings — all tested and responding
- ✅ Ingestion pipeline written (DOCX/PDF → OCR → chunks → embeddings → database)
- 🟡 Ingestion batch scheduled to run tonight on EC2 (10 sample proposals)

### Slide 15 — Infrastructure diagram (the whole system)

Draw a layered diagram from top to bottom, or left-to-right. Include ALL the components:

```
┌─────────────────────────────────────────────────────────────┐
│  DELIVERY TEAM (browsers, Mac/Windows)                       │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  http://13.206.20.25:8080 → sarvam.inspiritvision.com       │
│  Open WebUI (Docker container on EC2)                        │
│  • Multi-user auth, chat history, model switcher              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  HERMES AGENT (Python container, to be built in Sprint 4)    │
│  • Discovery interview logic                                  │
│  • Architecture proposal engine                               │
│  • Human-in-loop gate                                         │
│  • Section-by-section drafter                                 │
│  • Compression + export                                       │
└──────┬──────────────────────────────────────┬────────────────┘
       │                                       │
       ▼                                       ▼
┌──────────────────────┐            ┌──────────────────────────┐
│  OPENROUTER          │            │  SUPABASE (Tokyo)         │
│  Single API for:     │            │  • Postgres + pgvector    │
│  • DeepSeek V3.2     │            │  • 100+ past proposals    │
│  • Llama 3.3 70B     │            │  • Row-level security     │
│  • Qwen 3 14B        │            │  • Auth (user login)      │
│  • Qwen 3 VL 8B      │            │  • Vector similarity      │
│  • OpenAI embeddings │            │    search                 │
└──────────────────────┘            └──────────────────────────┘

Hosting: AWS EC2 t4g.small in Mumbai (ap-south-1), Elastic IP 13.206.20.25
Version control: GitHub (imranshaikh-commits/iv-sarvam)
Model routing: OpenRouter (single vendor, 300+ models available on demand)
Database: Supabase (managed Postgres + auth + pgvector)
Do NOT touch: IV's separate Lightsail WordPress instance
```

Notes for Claude drawing this: use a clean IT-architecture style (boxes, arrows, layers). Group related boxes visually (frontend/agent/model-and-data as three horizontal bands works well).

### Slide 16 — 🆕 Why we don't need Cloudflare Pages (revised architecture decision)

**Context for Claude:** The original PROJECT.md plan called for Cloudflare Pages hosting a static frontend. In practice, Open WebUI + EC2 + Elastic IP + a simple DNS A-record does the same job for zero extra cost. Simplification win.

Content:

**Original plan (July 2026):**
- Cloudflare Pages hosts a custom React frontend
- Cloudflare Workers proxy API calls
- Complex multi-service setup

**Revised plan (July 8, 2026 update):**
- Open WebUI already gives us a beautiful multi-user frontend for free
- EC2 + Elastic IP already gives us a static public endpoint
- Just add a DNS A-record: `sarvam.inspiritvision.com` → `13.206.20.25`
- HTTPS via Let's Encrypt / Caddy (auto-renew, free)
- **No Cloudflare required for the MVP**

**When we'd revisit Cloudflare:**
- If we need global CDN performance (unlikely for internal-only tool)
- If traffic grows past ~50 users concurrent
- If we want DDoS protection at the edge (probably yes eventually)

**Cost saved by dropping Cloudflare Pages from V1:** setup complexity + ~5% of dev time. No hard-cash saving, but simpler = faster to production.

### Slide 17 — Running costs at different scales

**This is a critical slide for CFO-style questions.** Present as a big table.

Assumptions per proposal:
- Average proposal generation: ~15,000 input tokens + ~8,000 output tokens (retrieval context is heavy)
- Assume 90% run on DeepSeek V3.2 (primary), 10% fall back to Llama 3.3

**Per-proposal LLM cost:**
- DeepSeek V3.2: (15K × $0.14 + 8K × $0.28) / 1M = **~$0.0043 per proposal**
- Llama 3.3 fallback: (15K × $0.59 + 8K × $0.79) / 1M = **~$0.015 per proposal**
- Weighted average: **~$0.005 per proposal**
- Embeddings (query-time): negligible (~$0.0001)
- **Round up to $0.01 per proposal for safety margin**

| Proposals per month | LLM cost/month | AWS EC2 (post-credit) | Supabase | Total/month | Total/year |
|---:|---:|---:|---:|---:|---:|
| 10 | $0.10 (~₹9) | $15 (~₹1,250) | $0 free tier | **~$15 (~₹1,260)** | ~₹15,120 |
| 20 | $0.20 (~₹17) | $15 | $0 | **~$15 (~₹1,270)** | ~₹15,240 |
| 30 | $0.30 (~₹25) | $15 | $0 | **~$15 (~₹1,280)** | ~₹15,360 |
| 40 | $0.40 (~₹34) | $15 | $0 | **~$15 (~₹1,290)** | ~₹15,480 |
| 50 | $0.50 (~₹42) | $15 | $25 (Pro if we cross free tier) | **~$40 (~₹3,360)** | ~₹40,320 |

Add key insight below the table:
**"LLM cost is trivial. The real cost is AWS EC2 hosting (~₹1,250/month) which we already pay for. Even at 50 proposals/month, total running cost is under ₹3,500/month — less than one senior consultant hour."**

Also mention:
- ✅ AWS free tier (₹16,000 worth of credits) covers first 6 months at zero
- ✅ Supabase free tier covers up to ~500 MB database (10x more than we need at MVP)
- ✅ OpenRouter is pay-as-you-go, no minimums
- ⚠️ At 100+ proposals/month, upgrade to Supabase Pro (~₹2,100/month) is recommended for backups + no idle-pause

### Slide 18 — Development cost (what we're asking for)

**The direct budget ask.**

Break this into two parts:

**A. What's been spent so far:**
- Perplexity Computer credits used: ~3,370 credits
- Rupee equivalent: approximately **₹4,000–₹4,500 already invested**
- Delivered: complete infrastructure + database schema + model stack + ingestion pipeline (28% of total project)

**B. What's needed to complete:**
- Estimated remaining Perplexity credits: **25,000 – 40,000** (generous estimate)
- Most likely landing: **28,000 – 32,000 credits**
- Rupee equivalent: approximately **₹30,000 – ₹40,000**
- Timeline: 8–10 weeks of part-time work

**C. Total project spend to production V1:**
- **Approximately ₹35,000 – ₹45,000 all-in**
- One-time build cost. Running cost after that is ~₹1,500/month.

**Framing for the boss:**
- One senior consultant costs IV **₹15,000–₹25,000 per proposal** in labor (2–5 days at loaded rate)
- Sarvam pays for itself after **2 proposals**
- After 100 proposals (roughly one year of use), Sarvam saves IV **₹15–25 lakh** in consultant time

### Slide 19 — Timeline for the remaining work

Show a Gantt bar with weeks:

- **Weeks 3–4:** Hermes agent (the brain) — Sprints 3 & 4
- **Weeks 5–6:** Retrieval & drafting loop — Sprints 5 & 6
- **Weeks 7–8:** Frontend polish, auth, multi-tenancy — Sprints 7 & 8
- **Weeks 9–10:** Human-in-loop gates, compression, export — Sprints 9 & 10
- **Weeks 11–12:** Pilot against 3–5 real historical RFPs, hardening, team rollout — Sprints 11 & 12

**Go-live target:** Mid-September 2026 (10 weeks from today)

### Slide 20 — Risks and how we're managing them

Show as a 4-column table:

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM hallucinates client details | Medium | Very High | Every section reviewed by human before send. Sarvam cites its sources. |
| Model vendor pricing changes | Low | Medium | OpenRouter routes to 300+ models — we can switch with one config change |
| AWS costs exceed forecast | Low | Low | ₹1,250/month is trivial. Cost alerts set at $10/month. |
| Team resistance to adoption | Medium | High | Sprint 12 includes team training + parallel manual process for first month |
| Client data leakage | Low | Very High | Row-level security in Postgres. Sarvam never sees data outside IV org. |
| Regeneration takes too long | Low | Medium | Timeout + fallback to cheaper model. Full proposal in under 3 min target. |

### Slide 21 — What Sarvam solves BEYOND the main use case

**This is the "and one more thing" slide that surprises the audience.** Bosses will approve budget faster if they see multiple wins from one investment.

Content (spend one bullet each):

- **📚 Institutional memory preservation** — When senior consultants leave IV, their knowledge is retained in Sarvam's proposal bank. No more "we lost that whole SailPoint knowledge when X left."
- **🎓 Junior consultant training** — New hires can query Sarvam: "Show me how we approached the DFCC Bank deal" and get a summarized breakdown instantly. Onboarding accelerator.
- **🔍 Deal pattern intelligence** — Ashish can ask Sarvam: "What are our top 3 most-won architecture patterns for banking clients in Southeast Asia?" and get a data-backed answer.
- **📊 Proposal quality baseline** — Sarvam enforces consistent structure across every proposal, so every deal from IV feels equally professional regardless of which consultant drafted it.
- **⚡ Rapid response to RFPs** — When a hot RFP arrives with a 48-hour deadline, IV can respond credibly instead of passing. More at-bats = more wins.
- **🛡️ Compliance and audit trail** — Every generated proposal has a full trace: which past proposals informed it, which sections were AI-drafted vs human-edited. Audit-ready.
- **💼 Sales enablement collateral** — The same underlying content bank can spin off case studies, capability decks, and one-pagers with minor tuning.
- **🌍 Multi-vendor authority** — IV is authorized across SailPoint, Ping, IBM, Keycloak, ForgeRock. Sarvam knows all of them and picks the right fit per deal — no more consultant bias toward "the vendor I know best".

### Slide 22 — The user journey (a delivery team member's day)
Show a horizontal journey map. Add small illustrations if possible.

**"Ashish's Tuesday morning — before Sarvam:"**
- 9:00 AM — New RFP arrives from a Singapore bank via email
- 9:30 AM — Ashish blocks his calendar for 3 days
- 10:00 AM — Digs through shared drive for similar past proposals
- 11:30 AM — Copies boilerplate sections into a fresh Word doc, starts editing
- 4:00 PM Wed — First rough draft ready
- 11:00 AM Thu — Review + revise + compress
- 3:00 PM Thu — Proposal sent. **~20 hours of Ashish's time spent.**

**"Ashish's Tuesday morning — with Sarvam:"**
- 9:00 AM — New RFP arrives
- 9:15 AM — Ashish opens Sarvam, uploads RFP, spends 20 min on the discovery interview
- 9:35 AM — Sarvam proposes architecture; Ashish approves after 10 min of review
- 9:45 AM — Sarvam drafts full proposal (takes ~3 min)
- 10:00 AM — Ashish reviews Sarvam's draft, spends ~1 hour on edits + pricing
- 11:00 AM — Sarvam compresses to <5MB, exports DOCX + PDF
- 11:15 AM Tue — Proposal sent. **~2 hours of Ashish's time spent.**

**Time saved: 18 hours per proposal.**
**Deals responded to per week: goes from 1–2 → 5–7.**

### Slide 23 — What we're asking for (the close)
One clear slide. Big text.

**Ask:**
Budget of **₹30,000 – ₹45,000** for Perplexity Computer credits to complete Sarvam V1 over the next **10 weeks**.

**Payback:**
Recovered after **2 proposals drafted with Sarvam**.

**Recurring cost after go-live:**
**₹1,500–₹3,500 per month** all-in (hosting + LLM + database), regardless of proposal volume up to 50/month.

**What you're approving:**
- Not just a tool — a permanent IV asset
- Trained on IV's proprietary data (competitive moat)
- Deployable to every delivery team member
- Self-improving with every won deal
- Runs on infrastructure we already own

### Slide 24 — Decisions needed today
One slide, three items:

1. ✅ **Approve budget of ₹40,000** for Perplexity Computer credits (rounded generous number)
2. ✅ **Approve subdomain provisioning:** `sarvam.inspiritvision.com` → point to Elastic IP
3. ✅ **Nominate 2 delivery team members** to be first Sarvam users during Week 11 pilot (Ashish + one senior consultant)

---

## SECTION 3 — APPENDIX SLIDES (skipped in main flow, referenced if asked)

### Appendix A — Full sprint-by-sprint plan
Show a table of all 13 sprints with descriptions + estimated Perplexity credits per sprint.

| Sprint | What gets built | Est. credits |
|---|---|---:|
| 0 (done) | AWS + Supabase + GitHub + Docker + Open WebUI foundations | ~1,200 |
| 1 (done) | Proposal bank preparation, tagging schema | ~600 |
| 2 (in progress) | Vector ingestion, chunk quality tuning | ~1,600 (of ~2,000 planned) |
| 3 | EC2 hardening, monitoring, Docker Compose refactor | ~2,000 |
| 4 | Hermes agent (the brain) — the biggest sprint | ~4,500 |
| 5 | Custom retrieval skill (RAG pipeline) | ~3,000 |
| 6 | Crawl4AI + proposal drafting loop | ~3,000 |
| 7 | Open WebUI polish, DNS setup | ~2,000 |
| 8 | Auth hardening, user management | ~2,500 |
| 9 | Human-in-loop architecture approval gate | ~2,000 |
| 10 | Compression module + DOCX/PDF export | ~3,000 |
| 11 | Pilot against 3–5 historical RFPs | ~5,500 |
| 12 | Hardening, monitoring, team rollout | ~2,500 |
| Contingency buffer | Unexpected debugging, model swaps | ~4,000 |
| **Total remaining** | | **~35,000** |

### Appendix B — Technical stack details (for the technically curious)
- **Backend hosting:** AWS EC2 t4g.small (ARM64, 2 vCPU, 2 GiB RAM) in Mumbai
- **Database:** Supabase (managed Postgres 17.6 + pgvector 0.8.2) in Tokyo
- **Vector index:** HNSW (Hierarchical Navigable Small World) on 1536-dim embeddings
- **Container orchestration:** Docker (Docker Compose in Sprint 3)
- **Version control:** GitHub (private repo)
- **Frontend:** Open WebUI (open-source, self-hosted, MIT licensed)
- **LLM gateway:** OpenRouter (single API, 300+ models, pay-per-use)
- **Primary language:** Python 3.14 for the agent, JavaScript/TypeScript for the frontend
- **Security:** Row Level Security enforced at database layer; TLS via Let's Encrypt

### Appendix C — Why we chose each vendor (rejected alternatives)
- **Rejected OpenAI direct** — too expensive, we get the same models via OpenRouter cheaper
- **Rejected Pinecone/Weaviate** — Supabase pgvector is good enough at our scale (under 10K proposals) and consolidates auth + data + vectors in one service
- **Rejected LangChain/LlamaIndex frameworks** — too much abstraction for our simple pipeline. Vanilla Python + OpenRouter is easier to debug and cheaper to maintain
- **Rejected custom React frontend** — Open WebUI gives us the same UX for free
- **Rejected Cloudflare Pages** — not needed once we have EC2 + Elastic IP + DNS

### Appendix D — Success metrics (how we measure Sarvam's value)
- **Primary:** Average time-to-first-draft per proposal (target: 2 hours vs current 2–5 days)
- **Secondary:** Number of proposals delivered per month (target: 3x increase)
- **Quality:** Consultant satisfaction score on Sarvam drafts (survey after each use, target ≥4/5)
- **Business:** Win-rate change on RFPs where Sarvam was used vs manual baseline
- **Adoption:** % of new proposals started with Sarvam (target: 80% by Month 3 post-launch)

### Appendix E — What's explicitly OUT of scope for V1
- Real-time collaborative editing (single-user per session)
- Direct CRM integration (Salesforce/HubSpot push)
- Multi-language proposals (English only in V1)
- Automated pricing (commercials always human-filled)
- SSO with IV corporate identity (basic email login only)
- Public/prospect-facing portal (internal only)
- Mobile-native app (responsive web is enough)

---

## SECTION 4 — Detailed explanation of the "sketch upload" feature (Slide 9)

**Claude, this is a feature the user (Imran) wants to add to Sarvam. Below is his full thinking so you can make Slide 9 more powerful.**

### The problem
Sarvam proposes architecture diagrams using its LLM. But architecture is nuanced. Sometimes the AI's proposed diagram will miss the delivery team's actual intent — maybe it picks the wrong vendor, or misses a specific integration the client needs, or doesn't understand a compliance constraint.

If Sarvam's diagram is rejected 3 times in a row, the delivery team wastes time on the back-and-forth. That's a bad user experience.

### The solution
When Sarvam senses repeated rejection (implicitly via user feedback, or explicitly via a "give up and let me sketch it" button), it offers three fallback input modes for the human to take over architecturally:

**1. Hand-drawn sketch upload**
- User draws the architecture on paper, a whiteboard, or an iPad
- Snaps a photo, drags into Sarvam
- Sarvam's vision model (Qwen 3 VL 8B) reads the sketch
- Converts to a clean digital diagram in IV's official format

**2. Flowchart / diagram file upload**
- User has a Draw.io, Lucidchart, Visio, PowerPoint diagram from a previous internal discussion
- Uploads the file to Sarvam
- Sarvam parses the file structure (or renders + reads it as an image)
- Regenerates in IV's official diagram style + branding

**3. Algorithmic step-by-step text input**
- User types out the flow as sequential steps:
  - "Step 1: User hits load balancer at edge"
  - "Step 2: Load balancer forwards to Ping AIC on port 443"
  - "Step 3: Ping AIC handles SAML SSO"
  - "Step 4: Session token issued, user redirected to app"
- Sarvam converts step-by-step logic to a formal diagram
- User can iterate ("no, add the WAF before the load balancer") until it's right

### Why this matters for the deck
This feature closes what would otherwise be Sarvam's biggest weakness: **AI stubbornness**. When the AI can't figure something out, the human sketches it, and Sarvam takes it from there. It transforms Sarvam from "an AI that might fail you" into "an AI that adapts when it's stuck."

**Claude, make Slide 9 emphasize this — bosses love hearing that the AI has a graceful degradation path.**

---

## SECTION 5 — Design guidelines for Claude

### Visual identity
- Use IV's blue-heavy corporate palette (ask user for the exact hex codes if needed)
- Sarvam's mark: consider using सर्वम् (Devanagari script) as a subtle watermark element
- Sans-serif font throughout (Inter, Helvetica Neue, or Calibri)
- No stock illustrations of "robots" or "AI brains" — use clean line drawings, icons, or geometric shapes

### Data visualization
- All numbers should be shown as **big, unambiguous figures**
- Use color coding sparingly: green = completed, yellow = in progress, gray = future
- Every chart must have a **one-line takeaway caption** below it
- Cost tables should be in **INR (₹)** as primary, with USD ($) in parentheses

### Slide density
- Each slide has **one key message** in the title
- Body of slide supports the title, doesn't compete with it
- If a slide has 5+ bullet points, split into two slides
- Aim for slides that can be understood in **<15 seconds of glancing**

### Language
- Active voice
- Present tense for current state ("Sarvam runs on…")
- Future tense for planned work ("Sarvam will draft…")
- Never say "the AI" — always "Sarvam"
- Never say "our tool" — always "Sarvam"

### Slide numbering
- Show slide N of 24 in a corner (helps audience track progress)
- Section dividers (e.g., "Section 2: How Sarvam Works") can be full-color break slides

---

## SECTION 6 — What NOT to include

- Don't include speculative future features that aren't committed (like fine-tuning or multi-language)
- Don't compare Sarvam to competitors like OpenAI GPTs or Claude Projects (avoid inviting "why not just use ChatGPT?" pushback — the answer is data privacy + IV moat, but keep it out of the deck unless asked)
- Don't include the raw Perplexity Computer credit numbers (3,370 spent, etc.) — those are for Imran's internal reference. Convert everything to ₹ for the deck.
- Don't put technical URLs or IP addresses on slides shown to leadership — reference them as "our internal server" or "the Sarvam infrastructure"
- Don't include any secrets (API keys, passwords)

---

## SECTION 7 — Files to give Claude alongside this brief

When Imran hands this to Claude, he should also attach:
1. This brief (`EXEC_PITCH_BRIEF_FOR_CLAUDE.md`)
2. IV logo (PNG, high-res) — for the title slide and watermarks
3. Optional: `SESSION_LOG_DAY1_2.md` if Claude wants more detail on any point

Imran can prompt Claude with:

> Please create a 22–28 slide PowerPoint presentation using the attached brief. Follow the slide-by-slide structure in Section 2 exactly. Apply the design guidelines from Section 5. Use IV branding (see attached logo). Output as .pptx.

---

## SECTION 8 — Suggested opening line for the meeting

When Imran opens the deck in the meeting, suggested opening:

> "In two intensive days, I've built 28% of a system that will save Inspirit Vision 18 hours per proposal. This deck shows what's already running, what's left to build, and what I need to finish it. The total ask is ₹40,000. Payback is after 2 proposals. I'll walk you through the plan in 15 minutes and answer questions after."

---

**End of brief. Version 1.0. Ready to hand to Claude.**
