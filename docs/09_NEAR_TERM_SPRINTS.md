> **Rewritten 2026-09-21**, replacing the stale version this file carried
> since before the ESNAD pilot. The original Sprint A–F plan below was
> never executed in that form — a live pilot run and a direct comparison
> against a real IV proposal changed what mattered. That original plan is
> kept at the bottom, clearly marked, because it's real institutional
> memory of a decision point, not because it's current. **Everything from
> here down to that marker is the actual, current plan.**

# Shilpi — Near-Term Sprints

`docs/PHASES.md` (in the repo) is the operational plan this file sits
above — **its own accuracy has not been checked as part of this
rewrite**; verify it before trusting it as current either.

---

## Completed, this session (2026-09-21)

Full detail and reasoning: `SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md`.

| Sprint | What it closed |
|---|---|
| 1 | Vendor/capability parser rebuilt (connector-word based, extensible to any future partner); sizing tables made internally coherent across Production/DR/UAT/Development; a second, unrelated extraction-merge bug found and fixed along the way |
| 2 | Heading-depth cap raised and corrected (was silently flattening a model's own structure to H3); per-vendor Solution Overview now actually asks for that structure; RACI and the licence BOQ fan out per vendor instead of a hardcoded pair |
| 3 | Project Resources, an Initial Project RAID Log, and explicit Scope Exclusions added where IV's own document places them; Post-Production Support split into its own section with real AMC/SLA tiers; two "built but never wired" gaps caught by pre-existing tests |
| 4 | Partner product corpus infrastructure — schema, retrieval, and drafting-prompt wiring live in Supabase for all nine OEM partners. **Zero content ingested.** This sprint was infrastructure only; the original "product depth catalogue" plan's content half is Sprint 7 below |

## Completed, 2026-09-22

| Item | What it closed |
|---|---|
| `sarvam_015_partner_product_corpus.sql` | Written and committed — the live Sprint 4 schema is now in the repo's migration history, not just the database |
| `docs/PHASES.md` accuracy check | Rewritten against the actual repo/DB state — the July version predated the ESNAD pilot and Sprints 1–4 entirely |
| **Sprint 7 — partner product content** | 18 documents across all 9 OEM partners, public material only (2 dropped for sitting behind a lead-gen form, confirmed by fetching each for real; 1 swapped after a live 403). Ingested: **18 products, 276 chunks, 9 vendors, 0 null embeddings, 18/18 reviewed=true** — verified against the live database, not just the ingest log. Retrieval spot-checked working (`match_partner_product_chunks` returns a sane similarity gradient on real content). |

`public.visual_assets` RLS is still open — unchanged, still needs a decision from Imran before any fix.

---

## Pending — recommended order

### Do first, cheap, no design decisions needed

1. **Write `supabase/migrations/sarvam_015_partner_product_corpus.sql`**
   reflecting Sprint 4's live schema (`partner_products`,
   `partner_product_chunks`, `match_partner_product_chunks`). Pure
   documentation-as-code — the tables already exist and work; this just
   stops the repo's migration history from being behind the live
   database. No judgement calls, should take under an hour.
2. **Check `docs/PHASES.md`'s own accuracy.** This file claims to sit
   above it strategically; that relationship is stated, not enforced, and
   nothing has verified `PHASES.md` itself is still correct.

### Needs a decision from Imran before it's code work

3. **`public.visual_assets` has Row Level Security disabled outright** —
   the Supabase anon key can read or write any of its 939 rows. Fixing it
   needs real access policies decided first (who should read/write these
   rows, under what conditions); `ENABLE ROW LEVEL SECURITY` with no
   policies just blocks all access instead of securing it. Ask before
   implementing, don't guess a policy.

### Sprint 5 — product depth completion (unblocked — Sprint 7 landed 2026-09-22)

The drafting half of the original "product depth catalogue" plan,
deferred at the time because there was nothing to draft from. Real
content now exists for all 9 partners (276 chunks). Not yet done:

- Confirm the per-vendor Solution Overview instruction (already asking
  for structured capability-area headers since Sprint 2) actually
  retrieves and cites the new partner-product corpus in a real drafted
  proposal — the wiring is live and retrieval is spot-checked working,
  but no proposal has been drafted against it yet.
- Re-measure heading depth and sub-point count against IV's own
  benchmark from the ESNAD comparison (~90 sub-points per vendor
  product, nested to H5).
- 4 of the 18 chunks skew large (PingOne Advanced Services alone is 155
  chunks of a 47,830-word technical reference) — worth checking this
  doesn't crowd out the other 8 vendors in retrieval the way the
  proposal corpus needed a per-source cap for the same reason.

### Sprint 6 — visual density (unblocked — Sprint 7 landed 2026-09-22)

- Ingest vendor architecture diagrams and product screenshots once
  Sprint 7 has gathered them.
- Per-workstream diagram sets rather than one global set per proposal.
- Placement rules so product imagery lands in the section it actually
  illustrates.
- Target: close some real fraction of the 66-vs-11 image gap the ESNAD
  comparison measured directly.

### Sprint 8 — intake enrichment (not blocked on anything, can start any time)

Independent of Sprint 7 — pure intake-schema and template work.

- Per-domain population fields (WIAM users, CIAM users, privileged
  accounts, applications per domain). ESNAD's own SOW gave one blended
  "20,000 identities" figure; IV's real proposal for the same deal broke
  this down per domain (5,000 / 10,000 / 50 / 6) from direct client
  conversations the system currently has no field to capture.
- Partner tier and certification fields to feed a genuinely detailed
  Company Profile section — currently thin and generic, flagged as a
  corpus problem in earlier runs but partly an intake-schema gap too.

### Then — the held-back validation run

A fresh, full ESNAD proposal generation, scored directly against the
same real IV proposal used for the original comparison. This is the
actual test of whether Sprints 5–8 closed real ground. **Held back
deliberately during Sprints 1–4 to control LLM cost — hold it back again
until at least Sprint 7 and Sprint 8 have landed**, for the same reason.
Sprint 7 and 8 don't depend on each other and can run in either order or
in parallel; the validation run should wait for both, not fire after just
one.

---

## Still unresolved — needs a person, not a sprint

Don't guess at these; ask Imran.

- Has the repo been made private?
- Has a reviewer other than the builder — Ashish, by name, in earlier
  planning — given a verdict on a draft against "would you sign this"?
  Still the single highest-leverage open item per `README.md`.
- Has an MSS-type run ever happened? Coverage exists in the corpus; no
  evidence one has actually been scored.
- Which is canonical: `03_CURRENT_STATE.md` or `README.md`'s Progress
  Dashboard? Same question for `06_PRODUCT_DECISIONS.md`'s Economics
  section versus the README's `## Costs` section. Two places stating
  overlapping facts with no precedence is exactly the drift that made the
  README stale before this session's update.

None of the above block Sprint 7 or Sprint 8 starting.

---

## Superseded plan (pre-ESNAD pilot) — kept for reference, not current

Everything below this line is the original Sprint A–F plan, written
before the ESNAD pilot run changed what the actual gap was measured to
be. Not executed in this form. Kept because it's a real record of a
decision point, not because anything below should be acted on.

### Sprint A — Close the validation loop *(current, small)*

The QA gate is built and unproven. One run settles it.

- Amlak run 3, judged on **hygiene only**: zero citations, no phrase padding,
  client logo on the cover, TOC as a formatted table.
- Record the result in `04_EVAL_BASELINE.md` under Run 3.
- **Exit:** a written verdict on whether output hygiene is solved.

Do not re-litigate the structural gap in this run — it is known, measured, and is
Sprint B.

### Sprint B — Template alignment to IV house structure *(the big one)*

The current gap, measured: IV writes **12 sections, 25 tables, 6,648 prose
words**; Shilpi writes **7 generic sections, 9 tables, 18,008 prose words**.
Shilpi is producing 2.7× the prose while conveying *less* structured information.

- Rebuild `proposal_templates.py` to IV's real section list rather than a generic
  scaffold.
- **Content-specific subsections** instead of the identical
  Overview / Detailed Design / Considerations triple on every section.
- **Static sections sourced from the bank**: Company Profile, Why-Vendor
  positioning, Similar Experience & Case Studies. These were always designed to
  be pulled near-verbatim; they have simply never been built.
- **Tables where IV uses tables**: sizing per environment, tranche plan, RACI,
  License BOQ, Total BOQ, payment milestones. Structured renderers, not prose.
- A real **Commercial section** (structure and basis; figures stay with the
  commercial owner).

**Exit:** a generated proposal whose skeleton a reviewer recognises as IV's.
This is the single highest-leverage change remaining, and it is mostly
deterministic template work rather than prompt tuning.

### Sprint C — Corpus expansion

Ingestion is bounded by manual download, so start it in parallel with B.

- Bulk-ingest via `scripts/corpus_manifest.py`, vendor batches, human-reviewed
  CSV.
- **Priority: MSS and migration proposals.** Coverage is 10 implementation /
  1 MSS / **0 migration** while the intake template offers all three. Ten more
  Ping implementations barely move the needle.
- Add `year`/`outcome` filtering before scaling — the bank reaches back to 2023
  and stale architectures will otherwise be cited as current.
- Retrieval work only if the pilot shows it is needed: metadata pre-filter, HNSW
  index, two-tier retrieval.

### Sprint D — Eval harness and scored pilot

- Turn the Amlak method into a repeatable script: run a fixture, report the
  measurable parts (section coverage, table count, citation count, review-marker
  count, diagram node/edge counts, page count).
- Score 5–10 real RFPs from the `RFP/` folder.
- **Involve Ashish and Rajnish.** Nobody except Imran has ever used Shilpi. That
  is the largest untested assumption in the project.

### Sprint E — Hardening

TLS and the 8080 exposure, Supabase Auth and multi-tenancy, backups, CI. Detail
in `docs/PHASES.md` Phase 7. Sequenced after the pilot by decision, though the
security-group restriction is minutes of work and need not wait.

### Sprint F — Visual assets

Design already agreed: one `visual_assets` table plus a storage bucket, three-way
image classification, placement derived from `(section_heading, image, caption)`
triples in the corpus, and a **mandatory human approval gate**. Only worth
building if the pilot says visual thinness is a real blocker.

---

### Standing risks to revisit

- **Single operator.** Deploys are manual file copies by one person. CI helps;
  the bus factor remains.
- **Public repo.** One `git add -A` from publishing client commercials. Reviewed
  and accepted for now — worth re-examining as the eval suite grows.
- **No prompt versioning.** Changing diagram or drafting guidance silently
  changes all future output with no record of what produced a given proposal.
  This will matter the first time a past proposal needs explaining.
- **Model dependency.** GLM 5.2 drafts well but proved marginal at structured
  output. The diagram chain is already split; a provider change could force the
  same for drafting.
