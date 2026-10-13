# Phase 3 report: reading the tender and the evidence

Branch `audit/phase-3`, stacked on `audit/phase-2` (which is on `phase-1`, on
`phase-0`; those three are pushed with CI green, none merged). For review by
the main session against `ARCHITECT_DECISIONS.md`. 2026-10-14.

Thirteen findings, eleven fix commits, plus one for the measurement script.
Suite: **820 passed, 4 skipped** (801 at the end of Phase 2). The four skips
are the real-binary tests, which run in the brain image in CI.

## What changed

| ID | Commit | Change | Test that fails without it |
|---|---|---|---|
| F11 | `3eac0f5` | Requirement text cap 600 to 2,000. The matrix carries `text_cut` when pasted RFP text was longer than extraction reads; it reaches the chat reply. | `test_rfp_intake.py::test_a_long_requirement_is_not_cut_off`, `test_export_engine.py::test_a_requirement_read_cut_short_reaches_the_chat_reply`. |
| F12 | `2b2e907` | Text or vision is decided per page. A scanned page inside a text PDF is read as an image and listed; one that cannot be read is reported as NOT READ. `read_text_layer` drops pdftotext's trailing empty page. | `test_rfp_intake.py::test_a_scanned_page_inside_a_text_pdf_is_read_visually`, `…::test_a_scanned_page_that_cannot_be_read_is_reported_not_skipped`; in CI, `test_production_stack.py::test_poppler_counts_pages_as_they_are_and_a_scanned_page_is_read_as_an_image`. |
| F16 | `90ab82f` | `.docx` and `.doc` are converted to PDF with LibreOffice (`rfp_intake.as_pdf`, off the event loop) and read by the existing path. `.txt`, `.md`, `.rtf` are refused by name. | `test_rfp_intake.py::test_a_word_tender_is_converted_and_a_text_file_is_refused_plainly`, `…::test_the_chat_reads_a_word_tender_through_the_converter`; in CI, `test_production_stack.py::test_a_word_tender_is_converted_and_its_text_is_readable`. |
| F15 | `3e24689` | An exact repeat of a requirement (same reference, same normalised text) is kept once. | `test_rfp_intake.py::test_a_requirement_repeated_on_another_page_is_listed_once`. |
| F28 F30 | `19d37d9` | When a section's facts block is too long, the longest answers are shortened (marked "…") instead of the block being cut from the end. The weak-evidence test counts the section's own facts only. One commit: same function. | `test_document_engine.py::test_a_long_answer_cannot_push_a_sections_other_facts_out_of_its_prompt`, `…::test_names_and_country_do_not_count_as_a_briefed_section`. |
| F67 | `19c18f7` | `_rest_all` pages in primary-key order. | `test_export_engine.py::test_paged_reads_ask_for_a_stable_order`. |
| F09 | `7629d17` | The classification call returns, per quote, `stance: supports or contradicts`. A covered or partial verdict resting on a contradicting quote becomes `needs-human`. | `test_multi_vendor.py::test_evidence_that_says_the_opposite_cannot_make_a_requirement_compliant`. |
| F29 F22 | `bc22f17` | `retrieval_trace` keeps `chunk_ids` and `product_chunk_ids` per section, and a `_document` entry with the ids of the approved diagrams embedded. One commit: same stored trace. | `test_export_engine.py::test_a_drafts_sources_are_recorded_per_section`, `…::test_the_stored_trace_names_the_diagrams_that_went_into_the_document`. |
| F26 | `de6ccbe` | Fan-out merges chunks by `chunk_id`, or whole text when there is no id. | `test_document_engine.py::test_evidence_sharing_an_opening_is_not_treated_as_one_chunk`. |
| F27 | `40b06b8` | The product-evidence cap is applied per vendor. | `…::test_each_proposed_vendor_keeps_its_own_product_evidence` (was Ping 12, Saviynt 0 at top_k 20). |
| F62 | `909b8f3` | `detect_vendor` matches vendor and product names as whole words, for twelve vendors. | `test_chat_interview_gating.py::test_vendor_detection_matches_vendors_not_fragments_of_words`, `…::test_vendor_preference_no_longer_follows_the_word_mapping`. |
| tool | `3238ebe` | `scripts/retrieval_attribution.py`, with an offline test. | `scripts/test_retrieval_attribution.py`. |

Three older `extract_rfp` tests fed pages a few words long with the vision
check patched off. A page that thin is now, correctly, a scan, so their pages
were made realistic. One classification test gained the new required field.

## Attribution for F26, F27 and F62: one command, on the host

Not run: it needs the database and embedding keys.

```bash
docker exec -i sarvam-brain python - < scripts/retrieval_attribution.py
```

It runs the brain's own section retrieval four times over the same queries
(the implementation template, for a Ping plus Saviynt engagement): all three
fixes undone, then adding them back one at a time. It prints, per section, how
many chunk ids each step added and removed, then the ids as JSON. Each distinct
request is sent once, nothing is drafted, reranking is off for the measurement.
It must be run on a build that contains this branch.

## Things the reviewer should look at

1. **F28 is not "section-specific fields first".** I shortened the longest
   answers instead. Order then stops mattering, a long section field can no
   longer crowd out another section field, and when everything fits the block
   is byte-for-byte what it was (so prompts and the prompt cache are unchanged
   for a normal session). If you want the reorder as well, it is one line.
2. **F26 has less effect than the finding suggests.** `retrieve_chunks` has its
   own near-duplicate pass that compares the first 400 characters of each chunk
   and drops one of any pair that is 85% alike. Two chunks with a long shared
   opening are dropped there, before the fan-out merge sees them. I left that
   pass alone (it was added for the "specialisation / specialization"
   boilerplate variants); it is the same defect at 400 characters instead of
   160, and the attribution run will show how much F26 moves on its own.
3. **F27 at the default settings was 8 to 4, not 12 to 0.** With `top_k` 8 the
   old pooled cut gave the better-matching vendor all 8 and the other the 4
   slots left. It starved a vendor outright only at a higher `top_k`. Now 6
   and 6. The product block is ordered by vendor, then similarity.
4. **F62 returns one vendor**, the one the query names first. The patterns
   include product names (PingOne, IdentityIQ, OIM, Entra). `\bping\b` still
   matches the plain word "ping". Vendor values were checked against the live
   corpus: twelve vendors, every token a substring of how the corpus writes it.
5. **F09 changes the classification prompt and its strict schema** (a new
   required field). The response cache will miss on the first run after it.
   Count the "contradicts the requirement" downgrades on Run 2.
6. **F12 will make more vision calls.** Every page under 200 characters of
   text in a text PDF is read as an image, blank separators included: one
   cheap call each. The whole document is rasterised once if any page is thin.
7. **F12, the phantom page.** With the 2026-10-12 "INCOMPLETE READ" warning in
   place, a fully read scanned tender would have reported "only the first N of
   N+1 pages" on its next run, because pdftotext ends the last page with a
   form feed too. That is from reading the code; the CI real-binary test is
   what confirms the page count.
8. **F16 trusts the file suffix**, not the content. A PDF saved as `.docx`
   fails in the converter and is reported as "could not be converted".
9. **F29 and F22, the trace shape.** `retrieval_trace` stays a list: the
   section entries, then one `{"id": "_document", "diagram_ids": [...]}`.
   Anything reading it as "one entry per section" must skip that last one.
10. **F11, one cap left.** The per-page text prompt still reads the first 6,000
    characters of a page. A denser page loses its tail with no flag.
11. **F30 will show more "weak evidence" banners** on sections that had only
    identity facts. That is the intent; watch the `sme <= 20` gate with F42.

## Not verified

- **No poppler or LibreOffice on this machine.** The real PDF and Word paths
  (F12, F16) are covered only by the two new tests in
  `test_production_stack.py`, which need this branch pushed to run in CI.
- **CI has not run on this branch.** It is not pushed.
- **Nothing has run against a live model**: F09's new field, F12's vision
  pages and the wording of the new chat lines are untested on a real run.
- **The attribution script has not run on the live corpus.**
- Run 2 (BTPN, then Amlak) is the check for this phase.

## State of the whole plan

| Phase | Findings | State |
|---|---|---|
| Already fixed | 4 | done |
| 0 | 13 | pushed, CI green, not reviewed, not merged |
| 1 | 11 | pushed, CI green, not reviewed, not merged |
| 2 | 11 | pushed, CI green, not reviewed, not merged |
| 3 | 13 | built, committed, **not pushed** |
| 4 | 1 (F46) | main session and Imran |

All 48 findings assigned to the audit workstream are written and tested.
None is on `main`, so none is deployed.
