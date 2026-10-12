# Deep audit: architect's decisions and phased fix plan

Decided 2026-10-12 by the main Shilpi session (the one that built the system),
on Imran's instruction to take the calls. For the audit workstream to execute.
It replaces the Fix / Park / Discard calls in `BRIEFING_FOR_MAIN_SESSION.md`
and answers its twelve questions. Evidence for every finding is in your own
`RECONCILIATION.md`; this document does not repeat it.

## How the calls were made

Shilpi's goal (`10_FUTURE_VISION.md`): a reviewer opens a draft, edits rather
than rewrites, and sends it; and someone other than Imran chooses to use it.
The first colleague gets an account at release. Beyond that the direction is a
growing corpus with an outcome loop, and several IV roles working on one
proposal with a record of who approved what.

So:

1. **Fix now** anything that makes a document or a chat reply look more
   certain, more complete or more saved than it is; anything that loses a
   client requirement silently; anything that lets one colleague reach
   another's deal; anything that breaks rebuilding the system.
2. **Park, with a trigger,** anything that is real but only bites on a step
   the vision has not reached (new ingestion, reranking, a second
   organisation, role-aware multi-user). Parked is not discarded: each has the
   event that reopens it.
3. **Discard** only what cannot matter. That is two findings.
4. Where the full fix is a rebuild, take the cheap half that keeps the door
   open (marked *lite*).

**Result: 4 already fixed, 49 to fix in five phases, 17 parked, 2 discarded.**
The briefing had 12 discards; ten of them are now parked or fixed-lite, because
they sit on the path the product is meant to take.

## Who does what

- **The audit workstream implements Phases 0 to 3.** It has read the whole
  report and reconciled all 72 findings in a fresh context, and it found four
  fixes the main session had recorded as closed that were only partly closed.
  The builder should not be the only one marking its own work.
- **The main session is architect and reviewer.** It reviews each phase's diff
  against this document before it reaches `main`, scores the paid runs, owns
  Phase 4 and the release gate, and makes no brain changes on `main` while a
  phase is open.
- **Imran** runs host commands, the paid runs, and the owner actions listed
  below.

### Per-phase procedure

1. Branch `audit/phase-N` off current `main`. One commit per finding, each with
   a behavioural test that fails before the fix.
2. Push; CI must be green on all three jobs.
3. Write `docs/audits/PHASE_N_REPORT.md`: what changed, the test for each, what
   was not verified.
4. The main session reviews. Then the phase is merged into `main`.
   **`deploy.sh` pulls `main`, so nothing on a branch can be deployed.**
5. Imran deploys with `~/iv-sarvam/deploy/deploy.sh`.

### Owner actions for Imran (no code)

| When | Action |
|---|---|
| Today | Supabase dashboard: switch **off** "Allow new users to sign up" (F02). |
| Today | OpenRouter: set a credit limit on the key (F49). There is none: "No cap" was chosen in Sprint 1. |
| Phase 0 | IAM: confirm the old access key named in `docs/AWS_SETUP_COMPLETE.md` is deleted (F51). |
| Phase 0 | Say yes or no to changing `handle_new_user` in production (F02). |
| Phase 4 | Create a scratch Supabase project for the restore rehearsal (F46). |

## Already fixed (4)

F04, F05, F20, F45. Accepted as you recorded them, residual risks included.

## Phase 0: The system can be rebuilt and tells no secrets (13, no paid run)

| ID | Decision | Notes and traps |
|---|---|---|
| F01 | Fix | `sarvam_021`: drop the legacy vector-signature function. Not in production, so a no-op there. |
| F43 | Fix | Correct `sarvam_015` in place to `vector(1536)`, with a dated comment. Production never ran that file and nothing checksums it. |
| F60 | Fix | `sarvam_021`: the four missing private buckets, `on conflict do nothing`. Path-policy half: not needed while only the service role reads storage. |
| F02 | Fix | Moved up from last. Signups off today (owner action); remove the automatic membership line from `handle_new_user` once Imran says yes. It changes a production function: ask first. |
| F06 | Fix | Counts and similarity only in the vault log. Leave `_why()`: the PostgREST body in error logs was added on purpose. |
| F50 | Fix, lite | `COPY *.py ./`. All thirteen modules ship; `tests/` is not matched. Splitting the large modules is parked (below). |
| F48 | Fix, lite | Checksum the D2 download. The moving `python:3.12-slim` tag stays. |
| F70 | Fix | Deploy also requires `"rfp_uploads_mounted": true`. |
| F51 | Fix | Remove the key id from the doc. |
| F71 | Fix | `render_gantt` returns `None` when the largest week is under 1. A chart is decoration; fail-soft is right here. |
| F41 | Fix | Unknown section id raises. Other parsing cases: not needed behind the service key. |
| F52 | Fix | No fallback after output has started; tolerate a non-JSON error body. |
| F49 | Fix, lite | A missing cost is recorded as unknown (null), never as 0. Budget enforcement is parked; the credit limit is the owner action above. |

## Phase 1: The document tells the truth (11, one ESNAD run)

| ID | Decision | Notes and traps |
|---|---|---|
| F14 | Fix | A reply with a negation does not advance the eligibility gate. "No issues, continue" will be held back too: make the follow-up answerable in one word. |
| F61 | Fix | Same shape for `_looks_like_saas`. |
| F72 | Fix | **Keep the capability lists**; they were added to match IV's own stack slide, which packs bullets into each box. Take them from `proposal_templates.DOMAIN_CAPABILITIES` (one definition) and drop any item the answers place out of scope. If that filter cannot be made reliable, fall back to domain names only. |
| F54 | Fix | Keep the grid. Draw an arrow between neighbouring columns only when `spec.edges` has one, with its label. Never between non-adjacent columns: ELK routing around the picture is why the grid exists. Measure arrow count and printed label size against the current ESNAD stack. |
| F56 | Fix, lite | Compaction merges leaves only when they also share a shape. Do not remove compaction: it took printed text from 3.8 pt to 4.6 pt. |
| F42 | Fix | Mark a still-cut-off section with `[SME REVIEW]`. Two traps: `_trim_incomplete_tail` drops a last line with no sentence ending, so append after every trim; after a cut-off table it needs a blank line before it. Test that it reaches the DOCX. |
| F10 | Fix, measured | (b) Keep brackets holding a quantity with a unit or a version: yes. (a) Do not choose blind. Log the stripped-aside count per section on the ESNAD run. Ten or fewer in total: leave `[SME REVIEW]` in place of the aside. More: keep stripping and list the affected sections in the chat reply. The scorecard gate is `sme <= 20` and recent runs sit at 8 to 13. |
| F24 | Fix | As you planned. Leave the restored-empty-subsection case. |
| F08 | Fix, split | `needs-human` (including unassessed overflow rows) prints "To be confirmed". For `missing` with a reference, **measure first**: have the ESNAD run report rows by status and reference. The status grades IV's past-proposal precedent, not capability; if `missing`-with-reference is a large share, the matrix swings back to mostly unconfirmed, which Imran rejected on 09-27. Bring the count to him; he decides the wording. The scorer's `tbc <= 25` gate must count the matrix separately. |
| F13 | Fix | The honest message, as Imran chose. |
| F31 | Fix | "Won engagements"; "engaged by" or "selected by", not "delivered". All 110 rows were marked `won` on Imran's instruction of 2026-09-27 ("won all, SOWs signed"); it is not verified per deal. Only the ForgeRock case-study engagements have delivery evidence. |

## Phase 2: The chat tells the truth, and each colleague sees only their own (11, verified in chat)

| ID | Decision | Notes and traps |
|---|---|---|
| F19 | Fix | The three remaining sites. |
| F21, F18 | Fix | Skip records the diagram as rejected, "Skipped by reviewer"; the summary says "N approved, M skipped". |
| F69 | Fix | Approval needs a stored render. The chat shows the diagram through a signed link to that render, so stored does mean seen. |
| F17 | Fix, changed | Not "at least one approved". Drafting needs the architecture step **finished**: every planned diagram approved or explicitly skipped. With F69, "at least one approved" would lock a consultant out whenever renders fail. `plan_diagrams` never returns an empty plan, for any proposal type. The automatic stack does not count; nobody reviews it. |
| F03 | Fix | Moved up from parked: the first colleague arrives at release. Ownership check on the answer merge, the diagram endpoints, and the proposal id taken from the chat marker. Rows with no owner stay readable. |
| F39 | Fix | One reply line each for a PDF error and a storage note. |
| F40 | Fix | Write the paths; "links" re-signs. |
| F57 | Fix | Name the dropped diagrams in the reply. Keep the de-duplication. |
| F53 | Fix, lite | Moved up from discarded. The fallback does run in production, whenever D2 errors or times out. At draft time an approved diagram must not be silently redrawn by a different engine: report it and leave the diagram out, named in the reply. Preview rendering may still fall back. |
| F55 | Fix, lite | Moved up from discarded. Two groups whose names normalise to the same D2 id get distinct ids. A reviewer cannot be expected to notice two zones merged into one. |

## Phase 3: Reading the tender and the evidence (13, one run on BTPN, then Amlak)

| ID | Decision | Notes and traps |
|---|---|---|
| F11 | Fix | `truncated` reaches the chat reply; requirement text cap 600 to 2,000. |
| F12 | Fix | Per-page vision decision. |
| F15 | Fix | Merge exact repeats. "Fuller value wins" stays. |
| F16 | Fix, changed | Do not reject Word. Convert `.docx` and `.doc` to PDF with the LibreOffice already in the image (`export_engine.export_pdf`), then the existing path. Tenders arrive as Word. Reject `.txt`, `.md`, `.rtf` with a message. `USER_GUIDE.md` already says "PDF or DOCX"; this makes it true. |
| F28 | Fix | Section-specific fields first. |
| F30 | Fix | The section's own fields only. |
| F26, F27, F62 | Fix | Three commits, F62 last (it moves the most). For attribution without three paid runs: diff the retrieved chunk ids for a fixed set of ESNAD section queries before and after each commit, at brain level. |
| F67 | Fix, lite | Moved up from parked. `_rest_all` has no order, and the reference book that goes into every proposal is read through it. Add a stable order. The asset readers stay parked. |
| F09 | Fix, lite | Moved up from parked. No new model call: the per-requirement classification call already exists. Have it return whether the quoted evidence supports or contradicts the requirement, and downgrade a contradiction to `needs-human`. A wrong plain "Compliant" is the line an editor is least likely to re-derive across 56 rows. |
| F29, F22 | Fix, lite | Moved up from parked and discarded. In the `retrieval_trace` already stored with each generated proposal, record per section the chunk ids used, and for the document the diagram ids embedded. No new table. This is the cheap half of provenance and what the outcome loop will need. The immutable revision chain stays parked. |

## Phase 4: Release hardening (1 finding, main session and Imran)

| ID | Decision | Notes |
|---|---|---|
| F46 | Fix | Rehearse a restore into a scratch Supabase project: migrations from the files (now possible after F01, F43, F60), then tables, storage, and the Open WebUI volume. Record the result in the runbook. |

Also in this phase, not audit findings: the uptime monitor, one rollback
rehearsal, the MSS first run, the full human read, the release gate.

## Parked (17), each with its trigger

| ID | Why not now | Reopens when |
|---|---|---|
| F32, F33, F34, F35, F36, F37, F65, F68 | Ingestion and asset tools; no load is planned and current data is intact. | **Before any `ingest_*` or asset-extraction run.** This is a gate, not a suggestion: the corpus is the product's compounding asset. F33 to F35 first. |
| F25, F63 | Reranker is off. | Reranking is switched on, or retrieval is tuned beyond Phase 3. |
| F66 | Feeds library images, which are off. | Library images are switched back on. |
| F38 | One draft per session and two overall are enforced. | A restart kills a paid draft twice, or more than about five people draft in a week. |
| F47 | Network guard and real-image CI are in; Phase 4 exercises the migration chain once by hand. | The next migration that changes a policy or a function after F02. |
| F23 | Actor emails are recorded as labels. | Role-aware multi-user work begins: sales lead, architect and commercial owner on one proposal. Build with F22's remainder. |
| F44, F59 | One organisation, one id, matching production. | A second organisation. |
| F07 | The corpus is IV's own proposals and vendor documents, and the model has no tools. | Any untrusted source enters the corpus, or the model is given tools or actions. |

Parked remainders of fixed-lite items: splitting `app.py` and
`document_engine.py` (F50: do it one seam at a time when a change next touches
that seam, never as a big-bang refactor); budget enforcement (F49); the
immutable revision chain (F22); the moving base image tag (F48).

## Discarded (2)

| ID | Why |
|---|---|
| F58 | A disconnected diagram is plain to the reviewer who must approve it. |
| F64 | A withdrawn reference clears within the hour, or at once on a restart. Add one line to the runbook saying so. |

## Paid runs and scoring

- **Run 1, after Phase 1: ESNAD.** It is also the first end-to-end run since
  2026-09-30, so it carries the main session's twelve primary-audit fixes as
  well. Compare with run `20260930_100541` knowing two change sets land
  together. Config: `docs/evals/esnad.json`.
- **Run 2, after Phase 3: BTPN** (shortest; migration template), then **Amlak**.
  Answer files: `~/Desktop/Shilpi/evals/`.
- Scorer changes belong to your branch: count matrix "To be confirmed" rows
  separately from the `tbc` gate, and revisit `sme <= 20` once the F10 and F42
  counts are known.
- The main session scores each run and reports to Imran.

## Standing rules

- Never read or edit `scripts/sarvam.env`. Never disable Row Level Security.
  No client content or secrets in the repository.
- Read-only database queries are fine. `sarvam_021` is additive and may be
  applied and reported. Anything that changes existing data, policies or
  functions needs Imran's explicit yes first.
- Smallest correct change. No new tables, services or queues in Phases 0 to 3.
- Keep the deliberate decisions: the aside clean-up, diagram de-duplication,
  the stack grid, compaction, "fuller value wins", `[SME REVIEW]` as the one
  marker. Make them honest; do not remove them.
- Say what was verified and what was not, in every phase report.
