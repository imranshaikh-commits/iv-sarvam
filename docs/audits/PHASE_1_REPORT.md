# Phase 1 report: the document tells the truth

Branch `audit/phase-1`, **stacked on `audit/phase-0`** (not on `main`: Phase 0
was not yet pushed or merged when this was built). For review by the main
session against `ARCHITECT_DECISIONS.md`. 2026-10-13.

Eleven findings, eleven commits. Suite: **780 passed, 2 skipped** (757 at the
end of Phase 0), run with `python -m pytest -q backend/brain/tests scripts`.

## What changed

| ID | Commit | Change | Test that fails without it |
|---|---|---|---|
| F14 | `25df14f` | A reply that asks to go on and carries a negation is held at the eligibility gate, with a message that says so and that one word, **continue**, moves on. | `test_chat_interview_gating.py::test_a_negative_reply_never_passes_the_eligibility_gate` (four phrasings; also asserts the session was not completed). `…::test_a_plain_go_ahead_still_passes_the_eligibility_gate` pins "continue", "All met", "all met, continue". |
| F61 | `e20c2d5` | `_looks_like_saas` counts a term only where it is affirmed, clause by clause. | `test_multi_vendor.py::test_a_deployment_answer_that_rules_saas_out_is_not_saas` (seven phrasings) and `…::test_a_negation_about_something_else_does_not_cancel_saas` (five, including ESNAD's own answer). |
| F24 | `c81b82d` | Only clauses that exclude are searched for exclusion keywords; the under-five-sections fallback no longer restores what the client excluded. | `test_scope_filter.py::test_saying_training_stays_in_scope_does_not_drop_knowledge_transfer`, `…::test_the_small_proposal_fallback_never_restores_what_the_client_excluded`. `…::test_not_in_scope_is_still_an_exclusion` passed before and after. |
| F13 | `3a4963f` | The RFP review lists the mandated sections and says Shilpi drafts in IV's own structure. | `test_rfp_intake.py::test_a_mandated_structure_is_shown_and_not_promised`. |
| F31 | `108b17b` | The reference block is "WON ENGAGEMENTS"; the drafter is told to write "selected by" or "engaged by" and never delivered, completed, implemented, deployed or live. One name, `proposal_templates.REFERENCE_LIST`, used by the block and the instructions. | `test_document_engine.py::test_won_work_is_not_presented_as_delivered_work`. |
| F42 | `9a49637` | A response still cut off after the retry is trimmed and ends with `[SME REVIEW]`; the marker is put back after the later clean-ups. | `test_document_engine.py::test_a_draft_still_cut_off_after_the_retry_is_marked_all_the_way_to_the_docx`: from a faked HTTP reply to the DOCX text, with a cut-off table and a normally finished control. |
| F56 | `df4483c` | Compaction groups leaves by source, zone **and shape**. | `test_diagram_engine.py::test_compact_never_merges_leaves_of_different_kinds`. |
| F72 | `12587d7` | Stack bullets come from `proposal_templates.DOMAIN_STACK_BULLETS`; a bullet is dropped when an excluding clause of `out_of_scope` mentions it. `app._stack_spec` passes `out_of_scope`. | `test_diagram_engine.py::test_the_stack_drops_a_capability_the_answers_place_out_of_scope` (through `app._stack_spec`, the caller), `…::test_a_client_who_forbids_saas_gets_no_saas_product_name_on_the_stack`, `…::test_every_stack_capability_comes_from_the_shared_domain_definition`. |
| F54 | `532489d` | `build_stack_d2` draws only edges of the spec, between neighbouring columns and to the sources band. | `…::test_the_stack_draws_no_arrow_the_spec_does_not_contain`, `…::test_the_stack_never_routes_an_arrow_across_a_column`, `…::test_the_sources_band_is_linked_only_where_the_spec_links_it`, `…::test_the_esnad_stack_keeps_its_true_links_and_loses_the_invented_one`. |
| F10 | `f7ac4ef` | (b) A bracket holding a quantity with a unit, a version, or a number too big to be a citation index is kept. (a) Stripped asides are counted per section, logged and stored in `retrieval_trace`. Stripping itself is unchanged. | `test_document_qa.py::test_bracketed_values_are_not_deleted_as_citations`, `test_document_engine.py::test_stripped_review_asides_are_counted_per_section_not_lost`. |
| F08 | `5e34d63` | `needs-human` rows print "To be confirmed" even with a reference. `matrix_breakdown` reaches the log, the endpoint JSON and the chat reply. Scorer counts the matrix separately. | `test_document_engine.py::test_a_requirement_nobody_judged_is_never_printed_as_compliant`, `test_export_engine.py::test_the_matrix_breakdown_travels_from_drafting_to_the_chat_reply`, `scripts/test_score_proposal.py::test_the_compliance_matrix_is_counted_apart_from_the_tbc_gate`. |

Three existing tests had their expectations changed, each because the behaviour
it pinned was the finding: the unmarked trimmed table (F42), the fixed sources
label "HR feed · accounts · audit" (F54), and the usage-ledger shape (Phase 0).

## Measured

**F54, on the ESNAD stack fixture in the test suite (`_esnad_stack`), D2 0.9.0
on this machine:**

| | Before | After |
|---|---|---|
| Arrows drawn | 5 | 4 |
| Canvas | 2990 x 1511 px | 2990 x 1511 px |
| Printed at 6 in wide: node text / arrow label / sources label | 3.8 / 3.2 / 2.9 pt | 3.8 / 3.2 / 2.9 pt |

The invented Ping-to-Saviynt "identity data" arrow is gone. The picture was
rendered and looked at: labels sit inside the gaps and nothing overlaps a box.
The fixture is the suite's ESNAD-shaped answers, not the real ESNAD session.

## Things the reviewer should look at

1. **F54 loses two true links, by the rule.** With users, Ping, Saviynt and
   applications in that order, Ping to applications (SSO) and privileged users
   to Saviynt connect columns that are not neighbours, so they are not drawn.
   The Ping box now has an arrow in from users and a dashed line to the
   sources, and none to the applications. Before, that relation was implied by
   an arrow from the *Saviynt* column labelled "SSO · provisioning", which was
   wrong. Options if this reads badly on Run 1: name the undrawn links in the
   caption under the picture, or put the applications column between the two
   platforms when both feed it. I did neither; the decision said grid and
   neighbours only.
2. **F72, the filter errs toward showing less.** "MFA for contractors is out of
   scope" drops "adaptive MFA" from the whole workforce box. A clause that says
   a capability stays in scope removes nothing. The bullets are a second
   structure beside `DOMAIN_CAPABILITIES`, not derived from its prose (that
   prose is seven to nine long items per domain, too much for a stack box); a
   test holds every bullet to a capability the prose lists. With nothing
   excluded the labels are byte-for-byte what they were.
3. **F10 (a) is measurement only.** I did not implement either branch of the
   rule (marker in place, or list the sections), so that there is one code
   path, not two, once the count is known. Read it after Run 1 from the log
   line `review asides stripped: N in total (…)` or from
   `generated_proposals.retrieval_trace[*].stripped_asides`. The chosen branch
   is then a few lines and is verified on Run 2.
4. **F08, `missing` with a reference is unchanged**, as decided. Run 1 reports
   the count three ways: the log line `compliance matrix by status`, the
   `compliance_breakdown` field in the endpoint JSON, and a chat line of the
   form "N of M requirement(s) with no IV precedent are answered 'Compliant,
   subject to confirmation'…". That chat line is new wording a consultant will
   see; change it if it is too blunt.
5. **F14 holds "No issues, continue"**, as decided. The negation pattern
   (`scope_filter.NEGATION_RE`) is shared by F14 and F61. "Not all met" is now
   held with the same message instead of falling to "I didn't catch a value".
6. **F61 also fixes the opposite misread**: "SaaS, not on-prem" used to read as
   on-prem because the on-prem check ran first.
7. **F24, what counts as "the client excluded".** Only an explicit exclusion
   stays dropped on the fallback. Decommissioning dropped by the
   in-place-upgrade heuristic is restored with the rest, because that is an
   inference, not something the client said.
8. **F31, the context key is still `delivered_references`.** Renaming it
   touches three files and a test for no behaviour; say if you want it.
9. **F42 tests note.** Earlier tests in `test_document_engine.py` replace
   `draft_with_openrouter` at module level and never restore it, so the new
   test installs the real chain itself. Not changed: it is how that file works.

## Not verified

- **Nothing here has run against a live model.** Run 1 (ESNAD) is the check for
  F42, F10, F08, F31, F72 and F54 on a real document.
- **CI has not run**: neither branch is pushed (the push is blocked by this
  session's permission rules and needs Imran).
- F54 was measured on the suite's fixture, with macOS fonts, not in the
  production image (DejaVu). The canvas size should match; the picture on Run 1
  is the real check.
- F14, F61 and F24 rest on a negation pattern tested on the phrasings listed in
  the tests. A phrasing outside them can still be misread; the direction of
  error is "hold at the gate", "not SaaS", "keep the section".

## What Run 1 has to bring back

| Number | Decides |
|---|---|
| Stripped asides, total and per section | F10 (a): ten or fewer, marker in place; more, list the sections. |
| Matrix rows by status and reference, above all `missing` with a reference | F08: Imran chooses the wording for those rows. |
| `[SME REVIEW]` count, against the `sme <= 20` gate | Whether F42 markers push a normal run over; revisit the gate. |
| The stack picture | Whether the undrawn SSO link needs a caption or a column order. |
| Unpriced calls in the cost log | Whether structured calls report a cost at all (Phase 0, F49). |
