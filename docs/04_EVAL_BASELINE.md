# Shilpi — Evaluation Baseline

The instrument for answering the only question that matters: **is Shilpi's output
sendable?** Not page count, not test count — how much a reviewer would have to
rewrite before putting it in front of a client.

---

## The benchmark: Amlak International (SailPoint IdentityIQ)

**Why this proposal.** Amlak is *not* in the ingested corpus, while SailPoint as a
vendor *is* (Al Qadsiah, Ministry of Energy). That gives the realistic condition:
familiar vendor, unseen client. A fixture built from an ingested proposal would
measure retrieval of its own source and prove nothing.

**Always run the leakage check before choosing a new benchmark:**

```sql
select client_name, iam_vendor, proposal_type, year from proposals order by client_name;
```

> A near-miss worth remembering: `CIAM_Mannai_IV_Technical_Proposal_V3_0.docx`
> looks like a Mannai proposal but is actually **Ahlibank** (Mannai is the
> partner). Ahlibank *is* in the corpus. Check the client inside the document,
> not the filename.

**The corpus is now 112 proposals across 50 clients and 15 vendors**, so listing
it here would go stale immediately. Run the leakage query instead.

> **Amlak WAS ingested during bulk ingestion (2026-08-14) and has since been
> deleted.** Run 7 was drafted with its own answer available in the corpus: its
> Similar Experience quality is genuine but its overall score is inflated.
> Confirmed removed on 2026-08-21 — `select count(*) from proposals where
> client_name ilike '%amlak%'` returns 0. Re-check before every scored run;
> re-ingesting the bank would silently restore it.

---

## Baseline: what the human proposal contains

Recorded **before** any run, so assessment cannot be rationalised afterwards.

| Attribute | IV original |
|---|---|
| Prose words | 6,648 |
| Table words | 3,252 |
| Tables | **25** |
| Images | **37** |
| Top-level sections | **12** |
| Structure | Executive Summary · Company Profile · Similar Experience & Customer References · Our Understanding of Scope · Solution Overview (incl. Why SailPoint) · Proposed Solution (incl. 4 sizing tables) · Implementation Approach · Project Management & Timeline (tranche tables) · Key Assumptions & Responsibilities (RACI) · Knowledge Transfer & Training · Commercial (License BOQ, Total BOQ, 4 payment-milestone tables) |
| Sizing | Prod: 4 app servers @ 4 CPU / 16 GB / 100 GB on Tomcat, split 2 UI + 2 Task; 1 DB @ 8 CPU / 64 GB / 500 GB RAID, MSSQL or Oracle. DR = Prod. UAT reduced to 32 GB / 250 GB. Dev provisioned |
| Timeline | 42 weeks, 7 sub-phases, 4–5 releases |

### The seven specific checks

Details a generic IAM proposal would miss. Presence or absence is the most
informative signal in the whole test:

1. Is **Keycloak** named as the incumbent being replaced?
2. Is **service account discovery (non-PAM)** covered as its own scope area?
3. Is **off-network password reset** handled (reset from the Windows login screen
   when not on the corporate network, syncing on reconnect)?
4. Are **certification campaigns** scoped by manager / application owner /
   entitlement owner?
5. Are **four environments** (Prod, DR, UAT, Dev) carried through into sizing and
   deployment, not collapsed?
6. Is sizing rendered **split by role** (UI vs Task servers), not flattened?
7. Does the **out-of-scope list** protect IV the way the original's does (no
   app-side changes, no data cleansing, no pentest)?

---

## Scoring sheet

Score 1–5 with a one-line justification. **The justification matters more than
the number.**

| Dimension | Question |
|---|---|
| Structural completeness | Does it contain the sections the human proposal has? What is missing entirely? |
| Technical accuracy | Vendor product names, roles, integration patterns correct? Any invented capability? |
| Specificity | Does it name actual systems and counts, or retreat into generic IAM prose? |
| Grounding | Are claims supported? Are review markers where evidence genuinely ran out? |
| Diagram fidelity | Do diagrams reflect the stated architecture (zones, regions, clusters, protocols)? |
| Presentation | Tables vs prose, images, TOC, logo, no artefacts |
| **Rework estimate** | **What fraction would a reviewer rewrite before sending? ← the headline number** |
| Length | Recorded, **not optimised for** |

---

## Run history

### Run 1 — 2026-07-29 (pre-fidelity-fix)

**Verdict: failed.** Root cause found: only 1 of 22 discovery areas reached the
drafting engine. Everything else was captured, stored in Supabase, and discarded.

- 15,744 words (38% longer than the human original)
- All sizing specifics **missing** — the model's own complaint that it lacked
  "Amlak-specific volumetrics/timeline/pricing" was literally true
- 7 verbatim repetitions of an internal deliberation paragraph + a raw `</think>`
- ~109 words of degenerate synonym chains
- TOC rendered as the Word F9 placeholder
- Diagrams spanning 2+ pages

### Run 2 — 2026-08-07 (post-fidelity-fix, pre-QA-gate)

**Verdict: fidelity fixed, hygiene and structure not.**

Fixed and confirmed: sizing (16 GB / 64 GB / 500 GB RAID / MSSQL / Tomcat / UI
vs Task split), 25 applications, four environments, Keycloak, service accounts.
Zero `<think>` leaks, zero "Note on Evidence Applicability", zero literal
markdown, no F9 placeholder.

Still broken:

- **260 citation markers** — user directive is now *remove completely*
- **4 of 117 paragraphs degenerate** at phrase level (word-level check missed it);
  worst were 992 and 1,054 words
- **66 "needs SME confirmation"** + 10 "Evidence does not…" — the model rephrased
  around the blocklist
- Client logo rendered as a placeholder box
- TOC unstyled
- "42 weeks" and off-network password reset **still absent** despite being supplied

**Structural deviation measured** (this is now the main gap):

| | IV original | Shilpi run 2 |
|---|---|---|
| Prose words | 6,648 | 18,008 |
| Tables | 25 | 9 |
| Images | 37 | 5 |
| Sections | 12 | 7 |

Missing entirely: Company Profile, Similar Experience & Case Studies, Why-Vendor
positioning, Identity Maturity Journey, License BOQ, Payment Milestone tables,
Resident Engineer. Every section carried the identical three subsections
(Overview / Detailed Design / Considerations & Dependencies) where IV writes
content-specific ones.

### Run 3 — 2026-08-11 (post-QA-gate)

**Verdict: mixed.** QA gate half-worked; the structural gap was untouched.

| Check | Run 2 | Run 3 |
|---|---|---|
| Citation markers | 260 | 10 |
| Citation Appendix | present | **still present, 78 paragraphs** |
| Degenerate paragraphs | 4 | **4** |
| "needs SME confirmation" | 66 | 45 |
| Client logo | placeholder | **wrong client's logo (input error)** |
| TOC as formatted table | no | yes |

**Two findings that mattered more than the score.**

The degeneration detector scored the bad paragraphs at ZERO because the failure
mode had changed: run 2 repeated phrases, run 3 produced a single-pass thesaurus
walk that never repeats anything. A metric that catches one shape of a failure
does not catch the class.

The Citation Appendix named Al Qadsiah 51 times, plus Ministry of Energy and
NWC, in a document addressed to Amlak. The inline `[N]` markers had been
stripped but the appendix they pointed at was left behind.

---

### Run 4 — 2026-08-14 (model swap only: GLM → Claude Sonnet 5)

**Verdict: the model question, answered.** One variable changed.

| | Run 3 (GLM) | Run 4 (Sonnet) |
|---|---|---|
| Degenerate paragraphs | 4 | **0** |
| Longest unpunctuated run | 677 words | **104** |
| "needs SME confirmation" | 45 | 5 |
| Prose words | 13,914 | 12,194 |
| Em-dashes per 1k words | 14.7 | 13.4 |

Gibberish was a model problem. Verbosity and em-dashes barely moved, which is
what identified them as template and prompt problems. Structure was
byte-identical to run 3 — no model can change a hardcoded subsection list.

---

### Run 5 — 2026-08-14 (template rebuilt to IV's house structure)

**Verdict: first run whose skeleton is IV's.**

| | IV original | Run 4 | Run 5 |
|---|---|---|---|
| Top-level sections | 11 | 7 | **11** |
| Subsections | 53, all unique | 32, only 14 unique | **54, all unique** |
| Tables | 25 | 10 | **19** |
| Em-dashes | 15 | 163 | **16** |
| List Bullet uses | — | 0 | **184** |
| Embedded newlines | — | 42 | **0** |

Cost: prose ballooned to 16,051 words against IV's 6,648, because 54 subsections
each drafted to their own budget.

---

### Run 6 — 2026-08-14 (prose budget, appendix dedup)

**Verdict: prose at parity.** 16,051 → 6,976 against IV's 6,648. Swimlanes
worked for the first time: the joiner flow came back with six named actor lanes
and real branch logic. Zero leakage, zero degeneration.

Decision nodes rendered as rectangles — every node came back `shape: process`.

---

### Run 7 — 2026-08-20 (corpus 112, topic-aware retrieval)

**Verdict: sections came alive, tables regressed.** Company Profile carried real
IV facts and Similar Experience cited a genuine GOSI engagement with
volumetrics — both first-time, both from the enlarged corpus.

Tables fell 19 → 16 and table words 2,355 → 1,620. Three sizing tables (DR, UAT,
Development) disappeared entirely. Cause: the topic reservation at 50% drew half
the evidence from `sizing`, which covers only 51 of 114 proposals, so production
sizing crowded out the rest. Lowered to 30%.

**The retrieval scorecard approved the change that caused this**, because it
measured relevance and not table richness. `tabular_pct` was added in response.

---

### Run 8 — 2026-08-21 (images enabled)

**Verdict: a client data leak.** Images went 6 → 16, hygiene stayed perfect
(0 degenerate, 0 citations, 0 SME markers, 0 literal pipes, 0 embedded
newlines), tables recovered to 19.

But **another client's Microsoft Project plan was placed under Case Studies** —
BTPN's and STC's task names, durations and resource assignments, in a proposal
addressed to Amlak. See `02_LESSONS.md`. 131 assets withdrawn in response.

Three further defects, all traceable to limits that were set too tight:

| Symptom | Cause |
|---|---|
| `[SME REV`, `[SME RE` truncated mid-word | 420-token cap against a 220-word instruction |
| Paragraphs of 180 words (IV median: 29) | the instruction capped the SUBSECTION, not paragraphs |
| "Gantt chart, a type of project management diagram that…" | captions built from vision descriptions |

---

### Run 9 — pending

**What it tests:** captions removed, prose token cap raised to 900, paragraph
length instructed at 60 words, decision-node shapes enforced by spec validation,
131 assets withdrawn.

**Before running:** confirm `select count(*) from proposals where client_name
ilike '%amlak%'` returns 0, and that `GET /health` reports
`"assets_enabled": true`.

**Judge on:** SME markers complete rather than truncated; median paragraph near
IV's 29 words; decision nodes as diamonds; no client name in any image or
caption; images placed and relevant.

---

## Reading the results

**Length is not the target.** Shilpi already produces 2.7× the human original's
prose while conveying *less* structured information. The 100+ page goal was
closed as an unvalidated proxy. If a run is shorter and tighter, that is
progress.

**Watch for loss, not invention.** With rich inputs the failure mode is detail
supplied at intake quietly failing to reach the output. Two silent truncation
bugs have already done exactly that. If specifics go missing, suspect truncation
before suspecting the model.
