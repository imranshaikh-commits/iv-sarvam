# Shilpi — Forward Phase Plan

> **Rewritten 2026-09-22.** The version this file carried since 2026-07-29
> predated the entire ESNAD live pilot, the RFP-intake build, multi-vendor
> support, the corpus expansion (11 → 112 proposals), and Sprints 1-4. Most
> of it described future work that is now done. This rewrite reflects what
> `git log` and the live Supabase project actually show, not what was
> planned in July.
>
> This file stays the long-horizon layer above `docs/09_NEAR_TERM_SPRINTS.md`.
> For what to actually do next, read `09` — it is more current and more
> detailed. This file is for phases 7 onward, which `09` doesn't cover.

## Principle behind this ordering

The project was feature-rich and evidence-poor in July. It is no longer
evidence-poor: a live pilot against a real inbound RFP (ESNAD) surfaced nine
real bugs, and a direct comparison against a real IV-authored proposal for
the same deal replaced opinion with measurement. Four sprints of work came
directly out of that comparison. See
`docs/SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md` for the full narrative.

---

## Phase 6 — Validation — substantially done

The original plan here was a single-proposal recreation test plus a 5-10 RFP
scored pilot. What actually happened was broader and more direct: a live
pilot against one real inbound RFP (ESNAD), carried all the way through
intake → diagram plan → drafting → a finished document, with nine real bugs
found and fixed along the way, each with a negative control proving the fix
does something. That run then produced a direct comparison against a real
IV-authored proposal for the same deal — the first time in this project's
history that Shilpi's output was checked against a real answer rather than a
benchmark score.

| Sub-item | Status |
|---|---|
| 6.0 Recreation test | Done, in a stronger form — a live RFP pilot with a real IV comparison, not a synthetic recreation |
| 6.1 Corpus expansion | Done — 112 proposals ingested via `scripts/corpus_manifest.py`, curated by content not filename |
| 6.2 Scored pilot (5-10 RFPs) | Partial — one real RFP scored in depth (ESNAD); Ashish/Rajnish review has not happened — still the single highest-leverage open item |
| 6.3 Evaluation harness | Done — `scripts/eval_retrieval.py`, 20 probes, measures diversity/type-match/topic-match/fragment-share/tabular-share |
| 6.4 Cost + usage telemetry | Not done — cost per proposal has not been measured since the model swap to Sonnet 5 and the subsection count changes since |

**What's still open from this phase:** get Ashish's verdict on a real draft
("would you sign this" — see `docs/06_PRODUCT_DECISIONS.md`), and measure
actual cost per proposal.

---

## Phase 7 — Hardening & access (post-pilot) — not started

Goal: make it safe for people other than the builder to use.

### 7.1 — Network exposure and TLS

`deploy/docker-compose.yml` still binds Open WebUI as `8080:8080` — all
interfaces, plain HTTP, public EC2 IP. IV's proposal bank and any client
document viewed through the UI are served unencrypted over the internet.
Unchanged since July.

- Restrict the security group to known IPs — minutes of console work, does
  not need to wait for this phase.
- Terminate TLS with Caddy or nginx in front of OWUI; obtain a certificate.
- Keep the brain localhost-only (already correct).

### 7.2 — Auth / multi-tenancy

- Supabase Auth wired through OWUI so there is a real user identity.
- Populate `approved_by` on diagrams — today the approval gate records
  *that* something was approved but not *who*.
- `org_members` has zero rows today. RLS is schema-present but not actually
  load-bearing — every policy is gated on `is_org_member(org_id)`, which is
  vacuously false for everyone, so the system only works because the brain
  connects with the service-role key and bypasses RLS entirely. This is
  architecturally single-tenant right now, not multi-tenant. Worth knowing
  before treating the RLS policies as protecting anything yet.

### 7.3 — Backup and recovery

- Scheduled `pg_dump` of Supabase to S3.
- Chunks are re-derivable from the bank; intake sessions, approved diagrams
  and generated proposals are not.
- The free-tier project has already idle-paused once.

### 7.4 — CI

- Run the test suite on push (GitHub Actions).
- Several hundred tests currently execute only when run by hand.

---

## Phase 8 — Quality and scale (evidence-led)

Everything here should be justified by pilot findings, not assumed. Several
items below moved from "future" to "done" since July; a few new ones were
added by the ESNAD pilot that the July version had no way to anticipate.

- **Hybrid search** — rejected, not deferred. Measured against the research
  rather than assumed: at this corpus scale, hybrid BM25+dense and
  multi-query expansion both underperformed plain dense retrieval in the
  literature this decision was checked against. Not revisiting without new
  evidence.
- **Cross-encoder / listwise reranking** — built, **not yet measured**. A
  listwise LLM reranker exists and is off by default
  (`SHILPI_RERANK_ENABLED`) until the eval harness scorecard justifies
  turning it on. Run `scripts/eval_retrieval.py --compare` with it enabled
  before flipping the flag.
- **Model selection** — done. Sonnet 5 primary, GLM 5.2 fallback, chosen on
  measured evidence (GLM produced degenerate/repetitive paragraphs under
  length pressure; Sonnet did not). Cost was never the binding constraint
  across the full candidate range.
- **Proposal length** — closed as a target, confirmed correct by later
  evidence. The ESNAD comparison showed IV's own proposals vary hugely by
  engagement size (2,642 words for a scoped upgrade vs 21,700+ for a
  greenfield multi-vendor build) — a fixed length target was always the
  wrong instrument. Scope-aware section/subsection filtering now sizes the
  document to the engagement instead.
- **Diagram visual parity** — partially done. Swimlanes, decision-node
  shapes, and measured aspect-ratio re-layout have shipped. Auto-layout
  still cannot reproduce a fully hand-composed deck; unresolved whether
  that gap matters without a wider pilot.
- **Visual asset reuse** — done, with an open security gap. `visual_assets`
  table (939 rows), a private Storage bucket, three-way image
  classification, and a mandatory human-approval gate are all live.
  **`visual_assets` currently has Row Level Security disabled** — the anon
  key can read or write any row. Needs a real access-policy decision before
  fixing (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` alone would just
  block all access). See `docs/09_NEAR_TERM_SPRINTS.md`.
- **Partner product corpus** — new since July, not anticipated by the
  original phase plan. Schema is live
  (`partner_products` / `partner_product_chunks` /
  `match_partner_product_chunks`), structurally separate from the
  proposal-history vault so a vendor datasheet can never be mistaken for
  IV's own delivery history. **Zero rows ingested.** Gathering real content
  across all nine OEM partners is the next real piece of work — see Sprint
  7 in `docs/09_NEAR_TERM_SPRINTS.md`.
- **Multi-vendor engagements** — done. An engagement can name multiple
  products for different scope areas (e.g. Ping Identity for Access
  Management/CIAM, Saviynt for IGA/PAM); headings, retrieval, drafting
  attribution, diagram labelling and asset selection all fan out correctly
  per vendor.
- **RFP/SOW intake** — done. A user can upload a scanned RFP instead of
  running the 22-area interview; the system extracts fields, a requirement
  register, and eligibility gates from it (including from a raster PDF with
  no text layer, via a page-by-page vision pass), then asks only for what
  it couldn't read plus the decisions that are IV's to make, not the
  client's.
- **Durable diagram spec-template store** (per vendor + diagram type) —
  still deferred, unchanged from July.

---

## Phase 9 — Production — not started

- Rollout beyond the pilot group.
- Monitoring and alerting on the brain and the database.
- Operational runbook and key-rotation schedule.
- Re-apply Shilpi/IV branding in Open WebUI (see `docs/BACKLOG.md`).
- AWS budget alert (see `docs/BACKLOG.md`).

---

## Open items not yet placed

- Prompt/guidance versioning — changing diagram or drafting guidance
  silently changes all future output with no record of what produced a
  given proposal. This will matter the first time a past proposal needs
  explaining.
- Deploy friction — deploys are manual file copies by one person; CI (7.4)
  helps, but the bus factor remains.
- `03_CURRENT_STATE.md` and this file both describe "current state" with no
  stated precedence between them and the README's own Progress Dashboard.
  Same question for `06_PRODUCT_DECISIONS.md`'s Economics section vs the
  README's `## Costs` section. Worth resolving which is canonical before it
  causes drift again.
