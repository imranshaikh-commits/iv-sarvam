# Deep audit: handoff to the audit workstream

Written 2026-10-12 from the main working session, for the session that picks
up the deep audit. Read this first, then the report.

## What you are being asked to do

Imran (product owner, sole tester) asked for the deep audit to be worked in
its own fork, in this order. **Do not start fixing before step 4.**

1. **Understand the whole report**: `docs/audits/2026-10-12_deep_audit.md`
   (1,600 lines, 72 findings F01-F72: 1 conditional P0, 34 P1, 34 P2, 3 P3).
   IV's senior solutions architect tested it by hand and says it holds up.
2. **Reconcile every finding against CURRENT `main`**, not against the audited
   snapshot. The report audited `3a50830`. Since then 13 commits fixed the
   primary audit's 12 findings (table below), so many of the 22 "baseline"
   entries (section 4B) are fully or partly fixed. For each of the 72: fixed /
   partly / open / not applicable, with the file and line or the test that
   proves it. Verify in the code; do not trust this table or commit messages.
3. **Brainstorm with Imran**: what matters for IV's actual use (one
   organisation, a handful of consultants, every document human-edited before
   it goes out), what to waive, what order.
4. **Agree phases and sprints**, then execute them.

## State of the code

- `main` is at `3a8c175`; production runs that build (`sarvam-brain:3a8c175`).
- Tests: `python -m pytest -q backend/brain/tests scripts` (749 pass, 2 skip
  locally; needs no keys; `tests/conftest.py` blocks the network). Local venv:
  `~/Desktop/Shilpi/.venv`.
- CI (GitHub Actions) runs three jobs on every push: the suite, the brain
  image with its real renderer stack, and the pinned Open WebUI image build.
- Canonical plan: `docs/11_PRODUCTION_PLAN.md`. Operations: `docs/RUNBOOK.md`.
  Agent rules: `CLAUDE.md` (refreshed 2026-10-12).

### Primary audit (12 findings) -> commits already on main

| Primary # | Deep-audit ids it maps to (check section 9) | Commit | Left open on purpose |
|---|---|---|---|
| 1 compliance overstated | F08 | `44f019f` | keyword references are still only suggestions |
| 2 another user's upload | F04 | `8abc695` | verified against a mock of Open WebUI's `file` table, not a live multi-user run |
| 3 deploy gate | F45 | `92ccae3`, `5a3ec31` | none |
| 4 approval not enforced | F17 | `9e2868e` | approvals are not invalidated by later answer edits |
| 5 approve-all-versions | F18 | `0d2d130` | none |
| 6 no authn/authz | F03, F23 | `9e2868e`, `061d562` | service key is opt-in (`SHILPI_API_KEY`, now set in production); rows with no owner stay readable; emails are labels, not verified identity |
| 7 silent truncation | F11 | `44f019f` | pages past 60 are announced, still not read |
| 8 logo SSRF | F05 | `d37d0a5` | DNS rebinding between check and connect; IMDSv2 + hop limit 1 now set on the instance |
| 9 lost concurrent answers | F20 | `a9cfad0` | none for disjoint keys; no revision check for conflicting edits |
| 10 no job identity | F38 | `590acd7` | in-process dedupe only; no resume, no job table |
| 11 backups | F46 | `cb6ba4d` | fresh-project restore never rehearsed |
| 12 builds/tests/docs | F47, F48, F50 | `12123ee`, `3a8c175` | large modules untouched; some tests still read source text |

The id mapping in the second column is the main session's reading of the
headings, not a checked correspondence. Section 9 of the report is the
authority.

Migrations applied to production on 2026-10-12: `sarvam_019` (llm_usage,
created_by_email, approved_by_email) and `sarvam_020` (merge_intake_answers).

## Production facts already checked (2026-10-12)

- **F01 (legacy SECURITY DEFINER retrieval): not present in production.** A
  catalog query of the live database shows two SECURITY DEFINER functions in
  `public`, `handle_new_user` and `is_org_member`, neither executable by
  `anon`. `match_proposal_chunks` and `match_partner_product_chunks` are
  SECURITY INVOKER (so RLS applies to the caller) with a pinned search_path.
  The defect in the checked-in migration chain is still real: a database
  rebuilt from the files would have the old function. Fix the chain.
- **F02 (new auth users auto-join the organisation): not verified.**
  `auth.users` is empty. Whether signups are enabled is a Supabase dashboard
  setting only Imran can read. Ask him early; the immediate mitigation is
  switching signups off.
- Every public table has RLS on; `anon` reads 0 rows from each.
- The server's instance role can only write to the backup bucket.

## Working agreement (unchanged from the main session)

- Claude writes code, tests, commits and pushes. **Imran runs every command on
  the EC2 host**; give them one command per code block. He deploys with
  `~/iv-sarvam/deploy/deploy.sh`.
- **Never read or edit `scripts/sarvam.env`. Never disable Row Level
  Security.** No client content or secrets in the repo; check a new ignore
  rule with `git check-ignore -v`.
- Database: read-only queries are fine. Additive migrations (new nullable
  column, new function) were applied on Imran's delegation and reported;
  anything that changes or removes existing data or policies needs his
  explicit yes first.
- Each fix is its own commit with a behavioural test that fails before it.
  Smallest change that is correct; no speculative abstractions. Measure before
  claiming a fix works.
- A paid proposal run costs about $0.50 and 40 minutes of Imran's time; batch
  fixes so one run tests many.
- Plain, short replies. Say what was verified and what was not.

## Still open from the main session (not audit work)

- Imran has not yet confirmed a chat turn and an RFP upload on the current
  build (service key and upload binding are live).
- No end-to-end proposal has been generated since 2026-09-30; everything since
  is unit-tested only. ESNAD, BTPN, Amlak and the first MSS run are pending
  (answer files: `~/Desktop/Shilpi/evals/`; scorer configs:
  `docs/evals/*.json`, gitignored).
- UptimeRobot monitor on `/healthz`, and one `rollback.sh` rehearsal.
- SailPoint vendor kit needs a deck from Imran.
