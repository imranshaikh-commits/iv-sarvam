# Design: reliable RFP reading and requirement-to-section matching

Written 2026-10-14 by the main session, approved by Imran, for the audit
workstream to build after Phase 3 (and the F28 follow-up). Both are release
blockers for v1.0: they affect every proposal, and a tender scores the
compliance matrix line by line. Same working rules as `ARCHITECT_DECISIONS.md`:
one commit per change, a behavioural test that fails first, phase report,
main-session review, merge to `main`, deploy.

## Why this is needed (Run 1, ESNAD, 2026-10-14)

- `proposal_type` came back "SOW", which the database rejects.
- `required_diagram_types` gave one type (run 0930 gave five).
- `population_by_domain` was missing and `user_count` was wrong
  ("10,000 users including 50 privileged accounts"; the RFP has 5,000
  workforce, 10,000 customers, 50 privileged).
- 17 of 56 matrix rows had no section reference although IV has precedent for
  them; about 7-8 of those are really addressed in the draft.

Root causes: IV's own decisions are being extracted from a client document;
pages are read one at a time and values merged afterwards, so facts spread
over pages are never combined; nothing ties a value to the text it came from;
two reads of one file can disagree; and requirements are matched to sections
only after drafting, by keyword overlap.

## Part 1: RFP reading

### 1a. Three kinds of intake field

Every field in `intake_template` is tagged with one kind, in the template
itself (one definition):

| Kind | Examples | Rule |
|---|---|---|
| `client_fact` | user counts per population, environments, applications, integrations, deployment constraints, in/out of scope, regulations, deadlines | Read from the RFP, with evidence (1b). |
| `iv_decision` | `iam_vendor` and the vendor scope map, `proposal_type`, `proposal_depth`, pricing model | **Never extracted.** Asked once, in one message, with the valid choices listed (for `proposal_type`, `VALID_PROPOSAL_TYPES`). |
| `derived` | engagement domains, `required_diagram_types` and `diagram_count`, engagement scale | **Computed** from facts and decisions; never extracted and never asked. The diagram plan follows from the domains in scope (the 0930 five-diagram set for a Ping+Saviynt deal). |

Acceptance: an RFP that states "SOW" cannot set `proposal_type`; the diagram
plan for the ESNAD answers is the same on every run.

### 1b. Two-pass reading with evidence

- **Pass 1, per page:** for each `client_fact` field, return verbatim quotes
  with the page number. No interpretation. Each quote is checked against that
  page's text (text layer or the vision transcription), with the same
  quote-grounding test `validate_coverage` uses; an ungrounded quote is
  dropped. The per-page 6,000-character prompt cap (F11 remainder) goes: a
  dense page is read in parts so no text is unread.
- **Pass 2, per field:** one call receives every grounded quote for that field
  across the document and returns the value with the pages it rests on (e.g.
  "WIAM 5,000 (p.4); CIAM 10,000 (p.4); privileged 50 (p.7)"). A value with no
  grounded quote is "not found", never guessed.
- Store, per field: value, pages, quotes.

### 1c. Same file, same answers

The reading is stored under the file's SHA-256 plus an extractor version. The
same file uploaded again reuses the stored reading: no model call, identical
answers. A new extractor version reads it fresh. Storage: a small table
(`rfp_readings`: sha256, extractor_version, fields jsonb, created_at); an
additive migration, may be applied and reported.

### 1d. One-screen confirmation

Before the architecture step, show the consultant the critical facts with
their pages: populations, environments, application count, deployment model
(SaaS / self-hosted), key integrations, out-of-scope items, mandated structure.
One reply corrects any of them (the existing late-answer path). A fact the
consultant corrects is marked as theirs and is never overwritten by a re-read.
Missing critical facts are listed as missing, not hidden.

### 1e. A measured test set

Five real tenders: ESNAD SOW (scanned), ANB `CIAM RFP V1.0.docx` (Word), NWC
RFP (PDF), Tiqmo RFP (PDF), C-DAC GeM bid (PDF). For each, the expected values
of the critical facts, written down once by Imran or the main session
(fixtures are client content: `docs/evals/`, gitignored). A script reports
field accuracy per tender. It calls models, so it runs on demand, not in CI.
Target before v1.0: every critical fact right on all five; two consecutive
reads identical.

## Part 2: requirement-to-section matching

1. **Allocate before drafting.** One structured call assigns each extracted
   requirement to the section (and subsection where the template has one) that
   must answer it, or `not_in_template`.
2. **Draft with the requirements in hand.** Each section's prompt lists its
   allocated requirements by id as must-address items. The drafter marks the
   paragraph that answers each one with an internal tag (e.g. `[[REQ-012]]`),
   stripped before export like citation markers.
3. **The matrix reference comes from the tags**: the exact section and
   subsection. `requirement_references` keyword matching stays only as the
   fallback for a requirement with no tag.
4. **Verify.** A requirement allocated but not tagged, or whose tagged
   paragraph is not about it (embedding similarity between requirement and
   paragraph under a threshold set on the ESNAD run), prints "To be confirmed"
   and goes on the bid team's list.
5. **Requirements no section covers** go into a short "Additional
   Requirements" subsection in Our Understanding of Scope, each answered
   briefly, so nothing is silently unaddressed.

Target: more than 90% of ESNAD's 56 rows carry a reference that a reader
agrees with (hand-checked once, then compared run to run). Cost: one extra
structured call per proposal and slightly longer section prompts.

## Order and runs

1. 1a (field kinds) and 1c (cache): small, remove two of the three Run 1 errors.
2. 1b (two-pass reading) and 1d (confirmation).
3. 1e (test set) and the accuracy report.
4. Part 2.
5. One ESNAD run to measure both, then BTPN or Amlak through the interview to
   confirm nothing regressed for deals with no RFP.

## Not in scope here

Role-based access, an immutable audit trail and resumable drafts (v1.1), and
anything parked in `ARCHITECT_DECISIONS.md`.
