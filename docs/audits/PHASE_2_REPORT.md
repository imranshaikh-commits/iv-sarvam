# Phase 2 report: the chat tells the truth, and each colleague sees only their own

> **Update 2026-10-13:** this branch was pushed after the report was written, and CI passed all three jobs (`test`, `brain-image`, `webui-image`). Statements below that it is not pushed, or that CI has not run, are out of date.

Branch `audit/phase-2`, **stacked on `audit/phase-1`**, which is stacked on
`audit/phase-0`. None of the three is pushed. For review by the main session
against `ARCHITECT_DECISIONS.md`. 2026-10-13.

Eleven findings, eight commits (three pairs share a commit, each for the reason
given). Suite: **801 passed, 2 skipped** (780 at the end of Phase 1).

## What changed

| ID | Commit | Change | Test that fails without it |
|---|---|---|---|
| F03 | `09c3f25` | `supabase_client.may_access_row` is asked before the answer merge and on all four diagram routes; each chat turn carrying a session or proposal id first confirms both are the user's own and belong together (`marker_is_mine`). | `test_export_engine.py::test_answers_cannot_be_saved_into_another_users_session`, `…::test_another_users_diagram_cannot_be_read_or_approved`, `…::test_a_chat_marker_naming_someone_elses_proposal_is_refused`. |
| F19 | `4d19325` | `save_plan` returns whether it saved; `_mark_discovery_complete` retries once and reports; a diagram whose insert failed is not offered for approval. | `test_chat_interview_gating.py::test_a_plan_edit_that_did_not_save_is_not_shown_as_the_plan`, `…::test_discovery_is_not_called_complete_when_marking_it_failed`, `…::test_a_diagram_that_could_not_be_stored_is_not_offered_for_approval`. |
| F21 F18 | `3c1dc58` | Skip retires the on-screen diagram ("Skipped by reviewer") and records the plan title on the session (`_diagrams_skipped`). The end-of-review message is counted from stored rows. One commit: the F18 residual closes *because* skipped diagrams stop awaiting review. | `…::test_a_skipped_diagram_is_not_approved_by_the_next_approve`, `…::test_a_skip_that_did_not_save_does_not_move_on`, `…::test_all_approved_still_reads_as_all_approved`. |
| F17 | `20d2809` | Drafting needs every planned diagram approved or explicitly skipped. | `test_export_engine.py::test_a_session_that_never_reviewed_its_diagrams_cannot_draft`, `…::test_one_approved_diagram_is_not_a_finished_review`, `…::test_every_diagram_approved_or_skipped_can_draft`. |
| F69 | `d77bda5` | Approval needs a stored render, in chat and on `PATCH /v1/diagrams/{id}` (409). A diagram with no picture is not offered for approval. | `test_multi_vendor.py::test_a_diagram_that_never_rendered_cannot_be_approved`, `…::test_a_diagram_with_no_picture_is_not_offered_for_approval`. |
| F39 F40 | `8314bea` | The chat reply names a failed PDF and any storage problem. Stored file paths are written to `final_docx_path` / `final_pdf_path`; "links" in drafting mode re-signs them. One commit: both change the same reply. | `test_export_engine.py::test_a_failed_pdf_and_a_failed_upload_are_said_in_the_chat_reply`, `…::test_the_stored_file_paths_are_recorded_on_the_proposal`, `…::test_asking_for_the_links_again_signs_the_stored_files_without_drafting`. |
| F55 | `e38d638` | D2 ids are made unique within their scope. | `test_diagram_engine.py::test_two_zones_whose_names_differ_only_by_punctuation_stay_two_zones` (compiled with D2 0.9.0 here). |
| F57 F53 | `e2373bb` | Approved diagrams left out as duplicates are reported; at draft time approved diagrams are drawn with D2 only and one that cannot be drawn is left out and named. One commit: they share the "left out" report. | `test_export_engine.py::test_an_approved_diagram_left_out_as_a_duplicate_is_named`, `…::test_left_out_diagrams_travel_from_drafting_to_the_endpoint`, `…::test_an_approved_diagram_is_not_redrawn_by_another_engine_at_draft_time`. |

Existing tests changed, each because it pinned the finding: two that called a
session with no diagrams "reviewed" (F17), and the approval fixture, which now
gives its diagrams a stored render (F69).

## No database change

Nothing in this phase needs a migration. `final_docx_path`, `final_pdf_path`,
`created_by_email` and `intake_sessions.generated_proposal_id` were confirmed
present in production by a read-only catalog query. Skips are stored in the
session's `answers` under `_diagrams_skipped`.

## Things the reviewer should look at

1. **F03 costs up to two extra reads per chat turn** (session owner, proposal
   owner) when identity is forwarded, plus one before each answer save. No
   reads when it is not. A failed ownership read is a refusal: the chat says it
   could not confirm the proposal is yours and to try once more. On a database
   blip a legitimate user sees that message for one turn.
2. **F03 does not sign the marker.** It checks what the marker points at. A
   user can still edit the mode or the diagram index in their own marker; F17
   is what stops that from producing a draft.
3. **F17 counts, it does not match titles.** Finished means approved rows plus
   recorded skips of planned titles is at least the plan length. Titles are
   clipped at 120 characters and carry the client name, so matching them was
   the fragile choice. A skip for a title not in the plan counts for nothing.
4. **Sessions in mid-review when this deploys.** A consultant who skipped a
   diagram *before* the deploy has no recorded skip. Their draft will be
   refused with "N of M planned diagrams are approved or skipped", and the chat
   has no way back to that diagram. They would have to start over. Production
   has 8 diagrams in `needs_review` today; whether any belongs to a live deal
   is Imran's to know.
5. **F69 changes what a failed render means for the consultant.** They can no
   longer approve from the text representation; they regenerate or skip. With
   F17, skipping everything still lets them draft, so a broken renderer costs
   the diagrams, not the proposal.
6. **F69, a stored render now means a shown picture.** In chat the path is
   written only after the signed link exists. If that write fails, the picture
   was shown but the diagram cannot be approved; regenerate fixes it.
7. **F53 on a host with no D2 at all** still uses Graphviz for both preview
   and draft, so the two agree. Production always has D2 (the deploy fails
   without it). The solution stack follows the same rule.
8. **F53 does not embed the reviewed bytes.** The document still re-renders the
   approved spec. Embedding the stored render is the F22 remainder, parked.
9. **F40 "links" matching** is a short message (ten words or fewer) containing
   "link", "links" or "download" and no "generate", "draft" or "regenerate".
   It re-signs the files of `intake_sessions.generated_proposal_id`, the last
   draft of that session. Drafts made before this deploy have no recorded
   paths, so "links" will say there is no stored document for them.
10. **F19, "Discovery is complete and saved".** When marking the session
    complete fails after a retry, the reply says the answers are saved, the
    session could not be marked complete, and that it does not hold up the
    next step. That last part is true today: nothing reads the session status.
11. **`propose_architecture` (all diagrams in one turn) has no caller** in the
    app; only tests reference it. It has the same unchecked insert that F19
    fixed in `propose_one_diagram`. Left alone as dead code; worth deleting.

## Not verified

- **Nothing here has run in a real chat.** The decision says this phase is
  "verified in chat": that is Imran, on a deployed build. The walk I would do:
  1. Start a proposal, reach the diagram review, **skip** the first diagram,
     **approve** the second: the summary should read "1 of 2 approved, 1
     skipped".
  2. Say **generate the proposal**: it drafts. Afterwards say **links**: fresh
     links, no new draft.
  3. In a second account, paste the first account's chat marker: refused.
- **CI has not run** on any of the three branches.
- F03 was tested against a stand-in for PostgREST, not two real Open WebUI
  accounts. It depends on Open WebUI forwarding `X-OpenWebUI-User-Email`,
  which is already relied on for the session check.
- F53's draft-time path was tested with a faked renderer; no real D2 failure
  was induced.

## Left open, by decision

Unsigned markers and invalidating approvals when answers change (F17), the
empty-plan half of F21, the temp-path logo (F40), the immutable revision chain
(F22), a role-aware audit trail (F23).
