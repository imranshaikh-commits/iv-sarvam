# Phase 0 report: the system can be rebuilt and tells no secrets

Branch `audit/phase-0`, off `main` at `74ae8a3`. For review by the main
session against `ARCHITECT_DECISIONS.md` before merge. 2026-10-12.

Thirteen findings, twelve commits (F01 and F60 share `sarvam_021`, as decided).
Suite: **757 passed, 2 skipped** (was 749), run with
`python -m pytest -q backend/brain/tests scripts`.

## What changed

| ID | Commit | Change | Test that fails without it |
|---|---|---|---|
| F71 | `f089754` | `render_gantt` returns `None` when the largest week is under 1. | `test_diagram_engine.py::test_a_plan_whose_weeks_are_all_zero_gets_no_chart_and_still_assembles` (was `ZeroDivisionError`). |
| F41 | `2bac03f` | `generate_proposal` raises `ValueError` for a section id or title that matches nothing, naming the unknown and the valid ids. The endpoint already returns `ValueError` as a 400. | `test_document_engine.py::test_an_unknown_section_id_is_refused_before_anything_is_drafted` (was "DID NOT RAISE"; also asserts nothing was drafted). |
| F49 | `dc2e57f` | The ledger gains `unpriced_calls`; `cost_usd` is the sum over priced calls and `null` until one reports. The cost log line goes through `document_engine.cost_text`. | `test_export_engine.py::test_a_call_with_no_cost_is_recorded_as_unpriced_never_as_zero`, through the endpoint (was `0.0 is None`). |
| F06 | `2612ce3` | The vault log line records the chunk count and similarities only. `_why()` untouched. | `test_chat_interview_gating.py::test_vault_chat_logs_no_question_text_and_no_client_names`: canary words in the question, client name and heading must not appear in the captured log. |
| F52 | `9a1e11c` | No fallback once a `data:` line was streamed; the reply ends with an "interrupted" note and `[DONE]`. A non-JSON provider error body is reported as text. | `…::test_a_stream_that_breaks_part_way_is_not_followed_by_a_second_answer` and `…::test_a_provider_error_that_is_not_json_is_reported_not_crashed_on`. `…::test_a_stream_that_fails_before_any_text_still_falls_back` passed before and after: it pins the behaviour that had to survive. |
| F70 | `7642506` | `deploy.sh` also requires `"rfp_uploads_mounted": true` from `/health`; when false it rolls back at once and says why. Runbook updated. | `scripts/test_deploy_gate.py::test_a_build_that_cannot_see_the_uploads_is_rolled_back` (was exit 0). |
| F50 | `667f25e` | `COPY *.py ./`. `CLAUDE.md`, `05_SESSION_KICKOFF.md` and `01_OPERATING_MANUAL.md` no longer tell people to maintain a list. | None. See below. |
| F48 | `a97b107` | The D2 tarball is checked with `sha256sum -c` against per-architecture sums. | None locally. See below. |
| F51 | `986ee28` | The key id is gone from `docs/AWS_SETUP_COMPLETE.md`. | None (a document). |
| F43 | `e106821` | `sarvam_015`: `embedding vector(1536)`, with a dated comment. | None locally. See below. |
| F01 F60 | `9eb1530` | `sarvam_021`: drops the vector-signature SECURITY DEFINER function and the six-argument overload from `sarvam_008`; creates the four missing private buckets. | None locally. See below. |
| F02 | `4467dc9` | `sarvam_022`: `handle_new_user` keeps the profile insert and drops the membership insert. **Not applied.** | None locally. See below. |

## Where there is no failing test, and what stands in for one

- **F50:** with a glob there is no list left to get wrong. The `brain-image` CI
  job builds the image and imports the app inside it.
- **F48:** no Docker daemon on this machine, so the image was not built here.
  The `brain-image` CI job verifies the amd64 sum on push. The arm64 sum is
  first exercised by a build on an arm64 host; a mismatch stops `deploy.sh` at
  the build, before the swap. Both sums are the digests GitHub publishes for
  the `v0.9.0` release assets (read from the release metadata; I did not
  download and hash the files myself).
- **F43, F01, F60:** there is no Postgres in the suite (F47 is parked). The
  statements of `sarvam_021`, plus a `vector(1536)` column with an HNSW index,
  were run on the production engine inside a transaction that was rolled back:
  no error. The catalog afterwards was unchanged: one `match_proposal_chunks`
  overload, six buckets, none public.
- **F02:** the new body is the existing one with two lines removed. It was not
  run anywhere.

## Things the reviewer should look at

1. **F49, a choice inside the decision.** The decision says a missing cost is
   "recorded as unknown (null), never as 0". When some calls are priced and
   some are not, I kept the known sum and added `unpriced_calls` instead of
   nulling the whole total: the sum is then a stated lower bound and no data is
   thrown away. `cost_usd` is `null` only when no call was priced. If you want
   the total nulled whenever anything is unpriced, it is a one-line change.
   Also: structured calls take their usage from the OpenAI SDK object. If that
   object does not carry OpenRouter's `cost`, Run 1 will show those calls as
   unpriced. That would be the truth coming out, not a regression.
2. **F01, one drop more than asked.** `sarvam_008` creates a six-argument
   overload that `010` to `012` never drop (they drop and recreate the
   eight-argument one). It is SECURITY INVOKER, so not an exposure, but
   production has one overload, so `sarvam_021` drops it too.
3. **F52, what counts as "started".** A line beginning `data:`. OpenRouter's
   `: OPENROUTER PROCESSING` keep-alives do not count, so a failure after only
   keep-alives still falls back.
4. **F70 rolls back immediately** on a missing mount instead of waiting 90
   seconds: the mount cannot appear by waiting.
5. **F41 also refuses a mixed list** (one valid id, one unknown).
6. **`.dockerignore`:** there is none in `backend/brain/`, so `COPY *.py` takes
   exactly the thirteen modules there today.

## Not verified

- **CI.** The branch was NOT pushed from this session (the push was blocked
  by the session's permission rules), so CI has not run on it. It must be
  pushed and all three jobs read before merge.
- No image was built locally (F48, F50).
- `sarvam_021` and `sarvam_022` are not applied to production.
- Nothing in this phase was exercised against a live model or on the host.
- Whether the old IAM access key is deleted.

## Waiting on Imran

| Item | Why |
|---|---|
| Supabase: "Allow new users to sign up" off | F02, owner action. |
| Yes or no to applying `sarvam_022` | It replaces a production function. |
| IAM: confirm the old access key is deleted | F51. |
| OpenRouter: set a credit limit | F49; budget enforcement in code is parked. |

`sarvam_021` is a no-op in production and may be applied without his yes; I
left it unapplied so the reviewer sees it first.

## Seen on the way, not changed

- `document_engine.generate_proposal` logs the first 100 characters of the
  model's reasoning about engagement scale (`"engagement scale: %s (%s)"`),
  which can quote discovery answers. Outside F06 as decided (vault log only).
