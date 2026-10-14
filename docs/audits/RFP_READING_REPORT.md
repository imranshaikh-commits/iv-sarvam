# Report: reliable RFP reading and requirement-to-section matching

Branch `rfp/v1-reading`, off `main` at `cbe0638`. Built against
`DESIGN_RFP_READING_AND_MATRIX.md`, in the order its last section gives: 1a
and 1c, then 1b and 1d, then 1e, then part 2. For review by the main session.
2026-10-14.

Thirteen commits: twelve changes, each with a test that fails first, plus one
fix to a test that could not run inside the brain image. Suite: **867 passed,
4 skipped** (830 on `main` with the F28 follow-up branch; this branch does not
include that branch, which touches a different function).

## What changed

| Step | Commit | Change | Test that fails without it |
|---|---|---|---|
| 1a | `ec57371` | Every intake question has one kind, defined once in `intake_template`: `client_fact`, `iv_decision`, `derived`. `extract_rfp` drops anything that is not a client fact, whatever its caller asks for; `run_rfp_extraction` asks for client facts only. | `test_rfp_intake.py::test_every_intake_field_has_one_kind`, `…::test_an_rfp_that_says_sow_cannot_set_proposal_type`, `…::test_the_chat_asks_the_reader_for_client_facts_only` |
| 1a | `5c822e5` | IV's decisions (`iam_vendor`, `proposal_type`, `proposal_depth`, `pricing_model`) are asked in one block after the reading, each with its valid choices. At **continue**, a missing or invalid required decision ("SOW") stops the step and shows the same block. The decision list is one definition; `rfp_intake`'s copy is gone. | `…::test_ivs_decisions_are_asked_once_with_their_choices`, `…::test_a_decision_outside_its_choices_is_not_an_answer`, `test_chat_interview_gating.py::test_continue_with_an_invalid_proposal_type_asks_for_it_with_the_choices` |
| 1a | `5ae5809` | The diagram plan is derived from the domains in scope; `required_diagram_types` and `diagram_count` are no longer read or asked. ESNAD's answers give run 0930's five on every run. | `test_chat_state.py::test_the_esnad_answers_give_the_0930_diagram_set_on_every_run`, `…::test_the_derived_plan_follows_the_domains_in_scope`, `…::test_the_interview_does_not_ask_for_derived_fields` |
| 1c | `969a651` | A reading is stored in `rfp_readings` under the file's SHA-256 and `EXTRACTOR_VERSION`. The same file uploaded again, under any name, reuses it: no model call, identical answers. A new version reads fresh. | `test_rfp_intake.py::test_the_same_file_uploaded_again_reuses_its_reading`, `…::test_a_stored_reading_comes_back_identical`, `…::test_rfp_readings_are_stored_under_hash_and_version`, `…::test_the_rfp_readings_migration_keeps_row_level_security_on` |
| 1b | `6e36ae6` | Two passes. Pass 1 per page returns verbatim quotes per client-fact field; each is checked against that page's text with the compliance matrix's quote-grounding test (now one function, `document_qa.quote_is_grounded`) plus "every number in the quote is on the page"; failures are dropped and counted. A dense page is read in 6,000-character parts, so nothing goes unread (F11 remainder). Pass 2 per field states the value from all its grounded quotes, with pages. No grounded quote: not found. Each field stores value, pages, quotes. | `…::test_facts_spread_over_pages_are_combined_with_their_pages`, `…::test_the_reader_checks_each_quote_against_the_page_itself`, `…::test_a_dense_page_is_read_in_parts_and_none_of_it_goes_unread`, `…::test_a_scanned_page_is_checked_against_its_transcription`, `…::test_if_stating_a_field_fails_its_own_quotes_stand`, `…::test_quotes_and_values_use_strict_schemas` (7 of the 9 new reading tests fail on the old reader; the other two are guards that held before too) |
| 1d | `f6c9131` | The review opens with the critical facts (populations, total users, environments, application count, deployment model, key integrations, out of scope), each with its pages; a missing one says **missing**. Values are stored with their evidence (`_rfp_evidence`). A field the consultant corrects is recorded as theirs (`_consultant_fields`) and a reading never writes over it. | `test_rfp_intake.py::test_the_review_shows_the_critical_facts_first_with_pages_and_names_the_missing`, `…::test_a_reread_never_overwrites_what_the_consultant_corrected`, `test_chat_interview_gating.py::test_the_rfp_reading_is_stored_with_its_evidence`, `…::test_a_correction_in_rfp_review_is_marked_as_the_consultants`, `…::test_internal_rfp_keys_stay_out_of_the_diagram_context` |
| 1e | `8fb4f16` | `scripts/rfp_accuracy.py`: reads each tender in a fixture folder from scratch, scores the critical facts, names those with no expected value, reads again and lists any field the two reads disagree on. On demand, on the host; never in CI. | `scripts/test_rfp_accuracy.py` (5 tests, incl. that `docs/evals/rfp/` is really ignored) |
| 2.1 | `b979bd9` | One structured call (`app.allocate_requirements`, passed as `allocate_fn`) places each extracted requirement in the subsection that must answer it, or none. The places are exactly the subsections that will be drafted (facet selection is now one function, `section_facets`). | `test_document_engine.py::test_requirements_are_allocated_before_drafting_to_what_will_be_drafted`, `…::test_without_extracted_requirements_nothing_is_allocated`, `test_export_engine.py::test_the_brain_allocates_requirements_through_its_own_structured_call`, `…::test_allocation_answers_map_to_outline_positions` |
| 2.2 | `2a63b42` | Each subsection's prompt lists its requirements and asks for `[[ID]]` at the end of the answering paragraph. The tag survives cleaning in a guard form, is taken out with its paragraph, and never reaches the text. | `…::test_each_subsection_drafts_with_its_own_requirements_in_hand`, `…::test_tags_are_recorded_with_their_paragraph_and_never_reach_the_text` |
| 2.3 | `22691cb` | The matrix reference is the tagged section and subsection. Allocated but untagged: To be confirmed, on the bid team's list. Keyword matching only for requirements never allocated. | `…::test_the_matrix_reference_comes_from_the_tag`, `…::test_without_an_allocation_the_keyword_match_still_applies` |
| 2.4 | `f7bf906` | Each tagged paragraph is embedded with its requirement; the similarity is reported per requirement. With `SHILPI_REQ_TAG_MIN_SIMILARITY` set, below it (or unmeasured) is To be confirmed. | `…::test_a_tagged_paragraph_that_is_not_about_its_requirement_is_to_be_confirmed`, `…::test_a_tagged_paragraph_about_its_requirement_keeps_its_reference`, `…::test_with_no_threshold_set_the_similarity_is_measured_and_nothing_is_rejected` |
| 2.5 | `17fac35` | Requirements placed nowhere are answered in an **Additional Requirements** subsection at the end of Our Understanding of Scope, tagged like any other. | `…::test_requirements_no_section_covers_are_answered_under_additional_requirements`, `…::test_with_every_requirement_placed_there_is_no_additional_requirements_subsection` |
| fix | `8af9478` | The migration-file test skips inside the brain image (which has no `supabase/`); the repo job still runs it. | Reproduced by running the suite from a copy of `backend/brain` alone: 1 failure before, none after. |

Existing tests changed, each because it pinned what the design replaces: the
per-page "longest value wins" merge tests (replaced by the two-pass tests),
three interview parse tests that used `required_diagram_types` as their
example field (now `ha_dr_requirements`), two that named
`rfp_intake.IV_DECISION_FIELDS`, and three call-site checks that now look in
`section_facets` and `parse_extracted_requirements`.

## Database

`sarvam_023_rfp_readings` **applied to production 2026-10-14**, as the design
allows: a new table (`sha256`, `extractor_version`, `reading` jsonb,
`created_at`), primary key on the first two, Row Level Security on with no
policy (service role only). Verified after applying: RLS on, 0 policies, 0
rows. Nothing existing changed. The design named the column `fields`; it holds
the whole reading (fields with evidence, requirements, gates, structure), so
it is `reading`.

## Things the reviewer should look at

1. **Which fields are IV's decisions.** The design's examples plus IV's
   positioning: `iam_vendor`, `proposal_type`, `proposal_depth`,
   `pricing_model`, `deal_size_bucket`, `license_included`, `differentiators`,
   the case-study and positioning fields, `partner_tier_certifications`.
   Commercial terms a tender can state (`payment_milestones`, `taxes`,
   `travel`, `validity_period`, `support_terms`) stay client facts; the old
   list called them IV decisions but still extracted them. Only the four the
   design names are asked in the block.
2. **The derived diagram rule.** Solution Architecture and Integration, then
   Identity Lifecycle (IGA), Privileged Access Management (PAM), Identity
   Governance (IGA), Authentication Journey (WIAM), Customer Journey (CIAM),
   at most five. That ordering is what reproduces run 0930 for Ping + Saviynt
   (read from the stored plan of the 2026-09-30 session); it also means a
   four-domain deal never gets the WIAM and CIAM journeys. With no scope known
   the plan stays one Solution Architecture diagram, as before. **The
   interview no longer asks for diagram types**, so BTPN and Amlak through the
   interview get the derived plan too; the plan step still edits it.
3. **Cost of reading.** Pass 2 is one call per field that has a grounded
   quote, six at a time; a full tender is likely 30-50 extra small calls on
   top of one call per page part. The stored reading means a file is paid for
   once per extractor version. Not measured on a real tender.
4. **A scanned page is checked against the model's own transcription.** The
   design allows it; it catches a quote the model did not also write into the
   transcription, not a transcription that is itself wrong. A long dense scan
   whose transcription is cut by the output cap loses its later quotes, and
   they are counted as dropped (`dropped_quotes`), not shown in chat yet.
5. **Short facts.** The grounding test rejects quotes under 12 characters, so
   the prompt asks for the whole sentence or table row. A table cell holding
   only "6" must be quoted with its row label to count.
6. **"Never overwritten by a re-read"** is enforced in `answers_to_store`, but
   today no path re-reads a tender into an existing session: each upload
   starts a new session. The mark is recorded on RFP-review corrections only,
   not on late answers at the drafting gate.
7. **Allocation needs the extracted register.** A proposal drafted from
   pasted `rfp_text` with no extracted requirements is not allocated and keeps
   keyword matching. A failed allocation call does the same and is logged.
8. **The similarity threshold is unset**, as the design says (set on the ESNAD
   run). Until then every tagged row's similarity is in the result
   (`requirement_tag_similarity`) and the log, and nothing is rejected on it.
   Allocated-but-untagged rows are already To be confirmed, so if the drafter
   tags poorly, the To-be-confirmed count rises; that is the honest failure.
9. **The `[[...]]` guard** catches any double-bracketed text up to 40
   characters in a draft, and drops tags naming no known requirement. A model
   writing other double brackets would lose them.
10. **The result now carries `compliance_markdown`** (the rendered matrix) and
    `requirement_allocation` for the bid team. Nothing in `app.py` passes the
    result through wholesale.

## Not verified

- **Nothing has run against a real model or a real tender.** The two passes,
  the allocation, the tagging and the similarity numbers are tested with fakes
  only.
- **The accuracy script has not run.** It needs the five tenders and their
  expected values in `docs/evals/rfp/` on the host; the format is at the top of
  `scripts/rfp_accuracy.py`. Commands, once the fixtures are there:

  ```bash
  docker cp docs/evals/rfp sarvam-brain:/tmp/rfp-evals
  ```

  ```bash
  docker exec -i sarvam-brain python - /tmp/rfp-evals < scripts/rfp_accuracy.py
  ```

- **CI**: see the message to the main session for the result on this report's
  commit.
- The design's step 5: one ESNAD run to measure both parts (target: every
  critical fact right, more than 90% of the 56 rows with a reference a reader
  agrees with; set the similarity threshold from it), then BTPN or Amlak
  through the interview.
