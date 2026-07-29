# Evaluation fixtures

Fixtures for measuring Sarvam's output quality against known-good human
proposals. See `docs/PHASES.md` §6.0 for the protocol and §6.3 for where this is
heading.

## These files are NOT committed

A fixture is derived from a real IV proposal and contains the client's name,
sector, scope, pain points, our proposed sizing and the shape of the commercial
structure. **This repository is public.** `.gitignore` excludes everything here
except this file.

Keep fixtures in the private Drive alongside the source proposals, or locally.
The eval *harness* (scripts that run a fixture and report metrics) can be
committed; the fixture *content* cannot.

## Naming

    NNN-<client-slug>-<vendor>.md      e.g. 001-amlak-sailpoint.md

## What a fixture contains

1. **Source of truth** — the human proposal filename, and confirmation the client
   is NOT in the ingested corpus. A fixture built from an ingested proposal
   measures retrieval of its own source and proves nothing. Verify with:

   ```sql
   select client_name, iam_vendor, proposal_type, year from proposals order by client_name;
   ```

2. **Discovery inputs** — all 22 areas, ready to paste one per turn.

   Supply everything an IV consultant would have at drafting time, *including
   IV's own expertise* — sizing, cluster topology, phasing, commercial structure.
   A client RFP is never exhaustive; IV makes it exhaustive, and the interview
   asks for those things precisely because a consultant supplies them. Use `skip`
   only where the source genuinely says nothing. Never invent an input.

3. **Comparison baseline, recorded BEFORE the run** — length, image count,
   diagrams present, sizing, timeline, commercial structure, plus a list of
   specific details a generic proposal would miss. Writing this first is what
   stops the assessment being rationalised afterwards.

4. **Score** — using the sheet in `docs/PHASES.md` §6.0. The headline number is
   the rework estimate: what fraction a reviewer would have to rewrite before
   sending.

## What the test measures

Given complete, expert inputs: does Sarvam produce a structurally complete,
correctly grounded, IV-voiced document a reviewer could send with limited rework?

It does **not** measure whether Sarvam could design the solution unaided. That is
a different question and not the product's job.

## Watch for loss, not just invention

With rich inputs the failure mode is detail supplied at intake quietly failing to
reach the output. Two silent truncation bugs have already done exactly that (a
2500-char answers cap, and guidance sliced off by a 4000-char context cap). If
specifics go missing from a generated proposal, suspect truncation before
suspecting the model.
