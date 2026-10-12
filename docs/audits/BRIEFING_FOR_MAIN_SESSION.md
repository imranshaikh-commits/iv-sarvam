# Deep audit: briefing for the main Shilpi session

> **Superseded 2026-10-12.** The main session answered this in
> `ARCHITECT_DECISIONS.md`, which replaces the Fix / Park / Discard calls below
> and answers the twelve questions. Kept as the record of what was proposed.

Written 2026-10-12 by the audit workstream (the session your `HANDOFF.md`
started), for you: the session that built Shilpi and fixed the primary audit.

## What Imran is asking you for

A go or no-go on the plan below before any product code changes. You know the
deliberate decisions in this codebase better than I do. Please:

1. Say whether each **Fix / Park / Discard** call is right, and name any that is
   wrong.
2. Answer the twelve numbered questions in the last section. Each is a place
   where I may be about to undo something you did on purpose.
3. Tell me about any work in flight on `main` that these changes would collide
   with.

Nothing has been changed. Two documents were added, uncommitted, in the
worktree `…/iv-sarvam/.claude/worktrees/trusting-mclean-550d00/docs/audits/`:
`RECONCILIATION.md` (all 72 findings against current code, with file and line)
and `REMEDIATION_PLAN.md` (the short plan). This file is the reasoning.

## What I did

- Read `HANDOFF.md`, `CLAUDE.md`, the whole deep-audit report,
  `11_PRODUCTION_PLAN.md`, `02_LESSONS.md` and the ESNAD session log.
- Checked every finding in the code at `bab2a5b`: the chat flow, endpoints,
  drafting, compliance, RFP intake, diagrams, scope filter, database client,
  migrations, ingestion scripts and the deploy script. I did not rely on your
  handoff table or commit messages.
- Ran the suite: 749 passed, 2 skipped.
- Read the production catalog, read-only: PostgreSQL 17.6, pgvector 0.8.2, one
  organisation, `auth.users` and `org_members` empty, RLS on every public table,
  six private buckets, `partner_product_chunks.embedding` is `vector(1536)`, no
  legacy `match_proposal_chunks` overload, the `on_auth_user_created` trigger is
  enabled, all 110 proposals have `outcome = 'won'`, all 10,984 chunks have a
  `section_topic`.

**Not verified:** no live model call, no real two-user Open WebUI session, no
rebuilt database, no generated proposal opened. I have not seen an ESNAD
document; what I know of those runs is from the docs and the dated code notes.

## Where I disagree with your handoff table

The table lists F08, F11, F17 and F18 against commits as if closed. In the code
they are partly closed:

- **F08:** a row whose status is `missing` or `needs-human` still prints
  "Compliant, subject to confirmation during Discovery" whenever a keyword
  reference exists (`document_engine.py:424-427`).
- **F11:** the 12,000-character cut sets `truncated`, but the drafting path
  reads only `unassessed` (`document_engine.py:3108`), so that warning never
  reaches the chat reply.
- **F17:** a session with no diagrams at all passes the gate
  (`app.py:2947-2953` only refuses when something is pending).
- **F18:** `approve_architecture` approves every current diagram awaiting
  review. A diagram the user skipped stays `needs_review`, so the next
  "approve" approves it unseen. If the skipped one is last, the chat says "All
  N diagrams approved" and the draft is then refused with a 409.

## The rule behind every call

1. **Fix what hides a problem from the person editing the draft.** A human
   edits every proposal, so wrong text is caught. Text removed silently, a
   warning stripped, or a "saved" that was not saved is not.
2. **Smallest correct change.** No new tables, services or job queues.
3. **Keep the deliberate ESNAD-era decisions and make them honest:** the aside
   clean-up, diagram de-duplication, the stack grid, "fuller value wins".
4. **Discard what was written for a multi-tenant product.** The production plan
   defines production as an internal tool whose output a human edits.

Result: 4 already fixed, 38 to fix, 1 to fix last (F02), 17 parked, 12
discarded.

## Already fixed (4)

| ID | Why I accept it as closed | What is left, and why I leave it |
|---|---|---|
| F04 | `pick_upload` filters to the requester's Open WebUI user id and refuses when ownership is unknown; `test_rfp_intake.py:228`. | Two files from one user are still told apart by time and name. One user confusing their own two uploads is a nuisance, not a leak. |
| F05 | Public hosts only, redirects re-checked, 8 MB streamed cap, image must decode; `test_multi_vendor.py:2359`. | DNS rebinding. IMDSv2 with hop limit 1 already blocks the valuable target. |
| F20 | Atomic merge in the database, present in production; `test_document_engine.py:2442`. | No revision check for two edits to one field. Two consultants editing the same answer at once is not a real IV case. |
| F45 | No pipe after pytest; `scripts/test_deploy_gate.py:31`. | The host gate runs the brain suite only. CI runs both on every push. |

## Phase 0: Housekeeping (11 findings, no paid run)

Nothing here changes a drafted document, so it needs no run to verify.

| ID | State now | Planned change | Why it is worth doing |
|---|---|---|---|
| F01 | Not in production. `001_init.sql:218-238` still defines the SECURITY DEFINER vector-signature function and nothing drops it. | New `sarvam_021`: `drop function if exists` for that exact signature. | A restore into a fresh project (the rehearsal still on your list) would recreate a function `anon` can call. One statement. |
| F43 | `sarvam_015:65` says `embedding vector`; the HNSW index on it fails on a clean database. Production is `vector(1536)`. | Edit `sarvam_015` in place to `vector(1536)`. | A forward migration cannot repair a failure that happens earlier in the chain. 015 documents a schema created by hand, so making it say what production has is a correction, not a rewrite of history. |
| F60 | The files create two of six buckets; the other four are comments. | In `sarvam_021`: insert the four missing private buckets, `on conflict do nothing`. | Same restore case: the first upload would fail with "create the bucket manually". The path-policy half of this finding is discarded (service role only). |
| F06 | `app.py:4211-4214` logs the first 80 characters of every vault question and each hit's client name and heading. | Log counts and similarity only. | The compose file says client-text logging was removed; this line was missed. I leave `_why()` alone: the PostgREST body in an error log was added on purpose after two rounds of blind debugging. |
| F50 | `Dockerfile:42` is a hand-kept COPY list. | `COPY *.py ./`. | `CLAUDE.md` calls a missed module the single most repeated mistake. This removes the mistake instead of documenting it. The refactoring half of F50 is discarded. |
| F48 | D2 is version-pinned but its download is not checked. | `sha256sum -c` for the two architectures. | Two lines; the last unpinned input to the image. The moving `python:3.12-slim` tag stays: `constraints.txt` already locks what matters and security patches on the base are wanted. |
| F70 | Deploy checks `/v1/keepalive` and the build tag. | Also require `"rfp_uploads_mounted": true` from `/health`. | A deploy that loses the Open WebUI mount currently goes green and the upload path dies quietly. The bucket check is not added: the brain already reports a failed upload. |
| F51 | `docs/AWS_SETUP_COMPLETE.md:102` names an old access-key id. | Remove the id; confirm the key is deleted in IAM. | The repo was public. A key id is not a secret, but there is no reason to keep it. |
| F71 | `render_gantt` divides by the largest week; two rows at week 0 crash document assembly, unguarded at `document_engine.py:1949`. | Return `None` when the largest week is under 1. | A model-written table can end a 40-minute paid run at the last step. A chart is decoration: your own "uncertain is not success" rule allows fail-soft exactly here. |
| F41 | An unknown section id silently drafts the whole template (`document_engine.py:2981-2984`). | Raise `ValueError`; the endpoint already turns that into a 400. | Only the eval harness sends `sections`, and a typo there buys a full paid draft. The other parsing cases (list-shaped body, numeric names) are discarded: only Open WebUI and Imran call this API, behind the service key. |
| F52 | The vault stream falls back to the second model even after the first has sent text (`app.py:4254-4273`); `:4246` assumes a JSON error body. | Track whether output started; after that, end with an error line. Tolerate a non-JSON body. | Consultants use vault chat daily; two answers glued together reads as one wrong answer. |

## Phase 1: The document tells the truth (10 findings, one ESNAD run)

| ID | State now | Planned change | Why |
|---|---|---|---|
| F14 | `app.py:3653-3654`: any message containing "continue" passes the eligibility gate. "Do not continue; mandatory qualification is not met" advances. | A message with a negation word does not advance; Shilpi asks which condition is unmet. Matched on shape (any negation), not a phrase list, per lesson 3. | This is the same gate you fixed for "not all met". It is half fixed. A disqualifying tender that proceeds wastes the whole bid effort. |
| F61 | `_looks_like_saas` (`document_engine.py:463-469`) returns true for "SaaS is not permitted; deploy in our private data centre". | Same negation shape, checked before the substring match. | The flag drives all four sizing tables and the stack's product names. A sizing table is the part an editor is least likely to re-derive. |
| F72 | `_DOMAINS` (`diagram_engine.py:1304-1312`) puts fixed feature lists on the stack: "SSO, adaptive MFA, federation", "vaulting, just-in-time access, session recording". | Labels name the domain only. The SaaS product mapping stays. | The function says "everything shown is a fact from answers". The feature lists are not. An SSO-only deal with MFA out of scope prints "adaptive MFA". The picture is added to every proposal without a review step (`app.py:2976-2987`). |
| F54 | `build_stack_d2` never reads `spec.edges`; it draws an arrow between every pair of neighbouring columns from a fixed label table (`diagram_engine.py:517-523`). Two vendors side by side always get "identity data". | Keep the grid. Draw a column-to-column arrow only when `spec.edges` has an edge between members of those columns, with that label. | `build_stack_spec` already builds the true edges from the answers; the renderer throws them away. The grid was chosen for legibility and stays. I will measure arrow count and printed label size against the current ESNAD stack before claiming it works (lesson 4). |
| F42 | `_post_draft` (`document_engine.py:832-859`): after one retry a response still cut off is trimmed to its last full sentence and returned as ordinary text. | Append `[SME REVIEW]` to that section. | The trim was right in 09-24 (no more "is available but N"). But a clean-looking half-section is the one thing an editor cannot see. The marker already exists, survives the QA pass and `_trim_mid_sentence_end`, and is counted by the scorecard. |
| F10 | (a) `_REVIEW_ASIDE_RE` deletes "(needs SME confirmation)" and keeps the claim it qualified. (b) `_LABELLED_CITATION_RE` (`document_qa.py:51`) deletes any short bracket holding a digit, so "[90 days]" goes. | (a) See question 1; my default is to leave `[SME REVIEW]` where the aside was. (b) Do not strip a bracket whose content is a quantity with a unit or a version. | (a) The docstring of `strip_review_markers` says gaps belong "in a review appendix" and "callers collect what is removed". Nothing collects them. That is built-but-never-wired: the warning is deleted and goes nowhere. (b) A retention period or a version list is client content, not a citation. |
| F24 | (a) `_explicitly_excluded` searches `out_of_scope` for keywords, so "No exclusions for training; training remains in scope" drops Knowledge Transfer. (b) Under five sections, the whole template comes back, exclusions included (`scope_filter.py:195-198`). | (a) Ignore clauses that say "in scope", "included" or "no exclusions". (b) The fallback restores sections dropped for lack of answers, never ones the client excluded. | The filter exists because "a section the answers exclude is worse than a missing one". (b) breaks that promise on exactly the small engagements it was written for. The third case (an emptied subsection list is restored) I leave: it is deliberate and commented. |
| F08 | See "where I disagree" above. | `missing` and `needs-human` rows print "To be confirmed" even with a reference. Only `partial` with a reference keeps the conditional wording. | A keyword match says where to look; your own comment says "it proves nothing". Printing "Compliant, …" on a row with no evidence is the original finding, softened. See question 2. |
| F13 | `describe_extraction` says "I will follow it instead of IV's house template" (`rfp_intake.py:415-418`). The structure is never stored or used. | Show the mandated section list and say Shilpi drafts in IV's structure, so the consultant must reorder. | Imran chose the honest message over building the feature. A promise the code does not keep is the "all met" bug again. |
| F31 | `build_reference_book` heads its block "DELIVERED ENGAGEMENTS -- IV's won work, the ONLY engagements you may describe as delivered". Every one of the 110 corpus rows is `won`. | Say "won engagements" and tell the drafter to write "engaged by" or "selected by", not "delivered". | Imran chose to soften the wording. With all 110 rows marked `won`, today any past proposal can be described as delivered work. See question 3. |

## Phase 2: The chat tells the truth (8 findings, verified in chat)

| ID | State now | Planned change | Why |
|---|---|---|---|
| F19 | You fixed the answer saves. Still unchecked: `save_plan` (`app.py:1738`), a failed `complete_intake_session` followed by "Discovery is complete and saved" (`:3861-3872`), a diagram whose insert returned nothing but is shown as stored (`:1818-1842`). | Each reply is built from the write's result. On failure, say so and stay at the same step. | Same class you already fixed four times in this file; these are the remaining sites. `CLAUDE.md`: "uncertain is not success". |
| F21 F18 | Skip leaves the diagram `needs_review`. The next approve approves it; or the chat says "All N approved" and the draft is refused. | Skip records the diagram as rejected with the comment "Skipped by reviewer". The summary says "N approved, M skipped". | With skipped diagrams no longer pending, the only diagram awaiting review is the one on screen, which closes the F18 residual without touching `approve_architecture`. The empty-plan half of F21 is discarded: the V1 gate needs an architecture. |
| F69 | A render or upload failure is swallowed and the diagram can still be approved, in chat and through `PATCH /v1/diagrams/{id}`. | Approval requires a stored render. Otherwise the reply says the picture failed and offers regenerate. | Approving a picture nobody saw is lesson 10: a gate that cannot be read through launders a decision. |
| F17 | A session with no diagrams drafts freely. | Drafting needs at least one approved diagram for the session (unless `SHILPI_ALLOW_DIRECT_GENERATE`). | `06_PRODUCT_DECISIONS` makes "no draft before approval" a product principle; the code enforces "no draft while something is pending", which is weaker. Unsigned markers and invalidating approvals after answer edits are not picked up. See question 6. |
| F39 | `_build_export_response` returns the PDF error and storage notes; `generate_proposal_from_chat` reads neither. | Add one line to the reply for each. | Today a failed PDF shows only as a missing link. The likely response is a second $0.50 run. |
| F40 | Links last an hour. `generated_proposals.final_docx_path` and `final_pdf_path` exist (`001_init.sql:178-179`) and are never written. | Write the paths. In drafting mode, "links" returns fresh signed URLs. | The files are already in storage. Re-signing costs nothing; regenerating costs 40 minutes. The temp-path logo half is discarded. |
| F57 | `_is_duplicate` drops an approved diagram with only a log line. | Return the dropped titles and name them in the chat reply. | The de-duplication was right (09-28 printed three JML flows). Dropping something a human approved without saying so is not. |

## Phase 3: Reading the RFP and the evidence (9 findings, one run on a second deal)

| ID | State now | Planned change | Why |
|---|---|---|---|
| F11 | See "where I disagree". Requirement text is also cut at 600 characters (`rfp_intake.py:676`). | Pass `truncated` through to the chat reply. Raise the 600 cap to the 2,000 used for field values. | You already fixed this cut once at the render ("…achieves access to a" shipped to ESNAD); this is the same cut one step earlier. Lesson 2. |
| F12 | `needs_vision` averages characters over the whole document. A text PDF with scanned annexures is read as text and the annexures vanish. | Decide per page: a page under the text threshold is rasterised and read with vision. | Mixed PDFs are the normal shape of a tender. This is the one place a mandatory requirement can be lost with no flag at all. Cost: one vision call per thin page. |
| F15 | The same requirement on two pages is listed twice. | Merge exact repeats (same reference, same normalised text). | Repeats use up the 60-requirement budget and pad the matrix. "Fuller value wins" for fields stays; conflict review is discarded. |
| F16 | `_DOC_SUFFIXES` accepts `.docx .doc .txt .md .rtf`; extraction is `pdftotext` and `pdftoppm` only. | Accept `.pdf` only, with a message to export to PDF. | A Word upload today is picked as "the RFP" and fails with "Reading that document failed". I found no path that reads a non-PDF. See question 9. |
| F28 | `discovery_context_for` lists universal fields first and cuts at 3,500 characters. | Section-specific fields first. | A long client description can push sizing out of the sizing section. Reordering is free. The 4,000-character RFP prefix and requirement-to-section allocation are not picked up: with an uploaded RFP the facts arrive as discovery answers, not as `rfp_text`. |
| F30 | `has_client_facts` is true once the context passes 200 characters, which names and country alone can do. | Count the section's own fields only. | The weak-evidence banner is the signal that tells an editor a section was written from thin air. Identity fields should not switch it off. |
| F26 | Fan-out de-duplicates on the first 160 characters. | Use the chunk id where present, else the whole text. | Proposal chunks share boilerplate openings; different sizing rows after the same intro are dropped. |
| F27 | Product chunks from all vendors are pooled, sorted and cut. The comment says "top_k PER vendor". | Cut per vendor. | The pool is Ping-heavy; on a Ping plus Saviynt deal the second vendor can end up with nothing. The code does not do what its comment says. |
| F62 | `detect_vendor` is a substring match over seven names: "mapping" selects Ping, and Saviynt and CyberArk are never detected. | Whole-word match over the full partner list. | It reorders evidence on every retrieval, including each compliance requirement that mentions attribute mapping. |

## Phase 4: F02 (last, at Imran's request)

The trigger that adds every new Supabase auth user to the IV organisation is
enabled. `auth.users` is empty and nobody signs in through Supabase, so nothing
is exposed today. Plan: Imran checks the signup setting; the automatic
membership line is removed from `handle_new_user`. That changes a production
function, so it waits for his explicit yes.

## Parked (17): real, but not worth doing until something changes

| ID | Why not now | Trigger |
|---|---|---|
| F32 F33 F34 F35 F36 F37 F65 F68, and F67 | All are in the ingestion and asset tools. Imran says no ingestion is planned. I checked the effect of F68 on current data: every one of the 10,984 chunks has a topic. | Before any new corpus or asset load. F33 to F35 first: they are the ones that corrupt a corpus quietly. |
| F25 F63 | The reranker is off by default, so there is no order to undo. The scorecard's blind spot matters only when retrieval is being tuned. | If reranking is switched on, or retrieval is tuned beyond the three bug fixes above. |
| F09 | Telling "supports" from "does not support" needs a model judging entailment, not a tighter regex. After the F08 change, a wrong plain "Compliant" needs both a wrong `covered` from the classifier and a keyword match, and everything else is on the bid team's list. | A run that shows a wrong "Compliant" row. |
| F03 | The service key and session ownership are done. Ownership on the answer merge and the diagram endpoints protects one IV consultant from another editing chat markers by hand. | Before accounts go beyond the first small group. |
| F29 | Storing source chunk ids per section is cheap, but nothing reads them yet. | The first time a wrong claim has to be traced. |
| F38 | One draft per session and two overall are enforced. Resumable drafts need a job table and a worker; the README already lists this. | A restart kills a paid draft more than once. |
| F46 | Backups are complete; only the restore rehearsal is missing, and it is on your list. | Yours. |
| F47 | The network guard and real-image CI are in. Testing the migration chain needs a Postgres in CI. | With F02, when the database starts to carry access control. |

## Discarded (12): will not be done

| ID | Why |
|---|---|
| F44 F59 | Organisation ids and cross-organisation foreign keys. One organisation, one hard-coded id that matches production. |
| F22 F23 | An immutable revision chain and an append-only audit trail. The report's target design adds about ten record types. That is a rebuild for a tool with a handful of users whose every output is edited by a human. |
| F49 | Budgets and unknown-cost states. Spend is already bounded by the OpenRouter monthly cap and the two-draft limit, at about $0.50 a run. |
| F07 | Evidence text in the system prompt. The corpus is IV's own reviewed proposals and vendor documents; nobody untrusted can publish into it, and the report itself says no exploit was shown. |
| F53 | Graphviz fallback loses shapes. The deploy and CI fail without D2 (`SHILPI_REQUIRE_STACK=1`), so the fallback cannot run in production. |
| F55 F56 F58 | D2 id collisions, compaction, the connectivity heuristic. All happen before or during the render the reviewer approves, so what they approve is what ships. Compaction was a measured legibility fix (3.8pt to 4.9pt). |
| F64 | The reference cache lives an hour and a restart clears it. |
| F66 | Recurrence scoring feeds library images, which are switched off. |

## Questions for you

1. **F10, the stripped asides.** `strip_review_markers` says gaps belong "in a
   review appendix" and "callers collect what is removed", but nothing collects
   them. Option A: leave an inline `[SME REVIEW]` where the aside was. Option B:
   collect them and list them in the chat reply, like `compliance_to_confirm`.
   I lean to A because the sentinel already means exactly this. Was a
   marker-free body the intent? Run 3 had 45 such asides: would 45 red markers
   break a scorecard threshold or the read?
2. **F08 wording.** After 09-27 printed 30 of 56 rows as "Partial", you moved to
   the current labels. Is "To be confirmed" acceptable for `missing` and
   `needs-human` rows that have a section reference, or will that swing a real
   matrix back to mostly unconfirmed?
3. **`outcome = 'won'` on all 110 proposals.** The README said `unknown` for all
   110. Was the blanket update deliberate, to make the reference book non-empty?
   If so the F31 softening matters more than Imran may realise.
4. **F54.** Is there any reason the stack renderer ignores `spec.edges` other
   than layout? If ELK routing was the problem, I keep the grid and only choose
   which neighbouring arrows to draw.
5. **F72.** Were the feature lists in `_DOMAINS` copied from IV's own stack
   slide on purpose? If IV always shows them, an alternative is to keep them and
   drop the ones the answers place out of scope.
6. **F17 and skip.** Is "at least one approved diagram" right, or should a
   consultant be able to skip every diagram and still draft? Does the automatic
   solution stack count?
7. **F42.** Any path where a trailing `[SME REVIEW]` on a cut-off section would
   be stripped or would break table rendering that I have missed?
8. **F50.** Any `.py` in `backend/brain/` that must not ship in the image? I see
   thirteen modules, all imported by the app.
9. **F16.** Has a non-PDF RFP ever worked, through any route?
10. **F26, F27, F62 together.** `CLAUDE.md` says one variable at a time for
    retrieval. These are three deterministic bug fixes with unit tests, measured
    together on one run. The retrieval scorecard cannot see them (it calls the
    RPC directly). Acceptable, or split them?
11. **F43.** Any objection to correcting `sarvam_015` in place rather than adding
    a forward migration?
12. **Runs.** I planned ESNAD after Phase 1 and BTPN or Amlak after Phase 3. You
    have those runs pending anyway. Should they be the same runs?

## How the work will be done

- Branch `audit/deep-audit` off `main`; merged only when Imran says.
- One commit per finding, each with a test that fails before the fix.
- Imran deploys after each phase with `~/iv-sarvam/deploy/deploy.sh`.
- Two paid runs in total, scored with `scripts/score_proposal.py` against the
  previous run of the same deal.
- Database: `sarvam_021` is a no-op on production; the F02 change is not. Both
  wait for Imran's yes.
