> **⚠ Stale as of the 2026-09-21 Claude Code migration.** This snapshot
> predates the ESNAD pilot run and Sprints 1–4 (vendor/capability parsing,
> sizing coherence, heading depth, RACI/BOQ fan-out, new house sections,
> the partner-product corpus). Treat `README.md`'s Progress Dashboard and
> Known Gaps as more current, and see
> `SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md` for everything that changed
> since this was written. This file needs a fresh-eyes rewrite, not just a
> few edits — left as originally written below so nothing is lost in the
> meantime.

---

# Shilpi — Current State (as of 2026-08-22)

A snapshot, not a plan. The plan is `docs/PHASES.md` in the repo. Update this
file when something material changes, and delete claims that are no longer true
rather than accumulating history.

---

## What works, verified

- **Full pipeline end to end**: intent router → 22-area discovery interview →
  diagram plan approval → per-diagram approval → drafting → DOCX/PDF with
  approved diagrams and reusable images embedded. Eight scored runs against the
  Amlak benchmark.
- **Discovery reaches drafting.** Two classes of silent loss were fixed in
  `parse_bucket_answers`: it split replies on `;` (so a multi-item value ended
  at its first semicolon, losing 9 of 10 out-of-scope items and 5 of 6 pain
  points) and it treated "HA and DR requirements" as a different field from the
  schema's "HA / DR requirements". Both produced a confident *partial* parse
  that short-circuited the LLM fallback, so nothing was chased and nothing was
  logged.
- **Corpus: 112 proposals, 11,060 chunks, 50 clients, 15 vendors.** Composition
  61 implementation / 39 migration / 14 MSS, against 10/0/1 before. Curated by
  reading document CONTENT, not filenames.
- **Retrieval is measured, not asserted.** `scripts/eval_retrieval.py` scores 20
  probes drawn from real template subsections. Current baseline: type match
  0.768, topic match 0.700, 6.55 distinct proposals per probe, max 2 hits from
  any one proposal.
- **Three proposal types**, each with a template. Asserted as an invariant over
  every type the intake offers.
- **Image placement.** 946 assets recovered from the bank, 210 approved, matched
  to sections on their vision descriptions. Run 8 placed 16 images against run
  7's 6.
- **288 tests passing**, pytest and script mode in agreement.

## What is built but unproven

- **Decision-node shapes.** The model writes correct branch logic into flow
  diagrams and then marks every node `process`, so diamonds never render. Two
  rounds of prompt instruction failed to move it. Now enforced by spec
  validation with corrective retry, using the mechanism that already existed —
  but **not yet observed in a fresh generation**. Run 9 is the test.
- **The run-8 fixes**: captions removed, prose token cap raised from 420 to 900
  (it was truncating `[SME REVIEW]` markers mid-word), paragraph length
  instructed at 60 words against IV's 29-word median. Also untested in a live
  run.

## Known broken / missing

**Nobody except Imran has read a Shilpi draft.** Eight scored runs, one reader,
one opinion. Ashish's "would you sign this" benchmark has never been applied.
This is the largest untested assumption in the project and no amount of further
building removes it.

**Structural parity with IV's house style — largely closed.** Measured against
the real Amlak proposal:

| | IV original | Run 6 | Run 8 |
|---|---|---|---|
| Prose words | 6,648 | 6,976 | 7,166 |
| Table words | 3,252 | 2,355 | 2,287 |
| Tables | 25 | 19 | 19 |
| Images | 37 | 6 | 16 |
| Top-level sections | 11 | 11 | 11 |
| Subsections | 53, all unique | 54, all unique | 54, all unique |
| Median paragraph | 29 words | — | 55 words |

Prose and structure match. Tables and images are the remaining gap, and both
have a floor set by the corpus rather than the code: `sizing_dr` evidence scores
0.12 tabular against `sizing_prod` at 1.00, and only about a third of IV's 37
images are reusable at all.

**Image placement has no position.** Images land at the END of whatever section
matched, so an engagement-approach graphic sat under "Workforce and
Capabilities" in run 8.

**Company Profile is thin.** 493 words but generic, because the
`company_profile` chunks behind it are mostly headings and fragments rather than
IV's actual profile prose. A corpus problem, not a template one.

**`outcome` is `unknown` for all 112 proposals.** Recording won/lost and
weighting retrieval toward what actually won is the single change that would
compound more than anything else here. It needs a human who knows the answers.

**Port 8080 is still plain HTTP** with the whole proposal bank behind it. Open
since the first session; the only outstanding item with a live security
consequence.

**The repo is still public.** Client review artefacts were published to it once
(see `02_LESSONS.md`). Purged, and every artefact that touches client data is
now verified with `git check-ignore` rather than assumed — but the class of
problem remains until the repo is private.

## Model configuration

- **Primary**: `anthropic/claude-sonnet-5`. **Fallback**: `z-ai/glm-5.2`.
  Overridable via `SHILPI_PRIMARY_MODEL` / `SHILPI_FALLBACK_MODEL`; any override
  is logged at startup and reported by `GET /health`, so it is never silent.
- Chosen by controlled comparison, not preference: same 22 inputs, model as the
  only variable. GLM produced 4 degenerate paragraphs including two thesaurus
  walks of 645 and 677 words with no full stop; Sonnet produced 0, worst case
  104. Verbosity and em-dash habit barely moved, which is what identified those
  as template and prompt problems rather than model problems.
- Diagram chain: `anthropic/claude-sonnet-4.6, google/gemini-2.5-flash,
  z-ai/glm-5.2`.
- `GET /health` reports the active model chain **and** `assets_enabled`. Every
  runtime switch that changes what lands in a client document is visible there,
  because a silent config difference makes a scored run meaningless.

## The open question

Not "does it work" — it does. The question is whether the output is good enough
for a senior IAM architect to sign. That has never been tested, and it cannot be
answered by another run scored by the person who built it.
