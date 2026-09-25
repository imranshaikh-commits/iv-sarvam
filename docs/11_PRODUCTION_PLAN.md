# Shilpi: Road to Production

> Written 2026-09-25. Canonical plan for getting from "pilot, one builder" to
> "production for IV's consultants". The README's Progress Dashboard is the
> canonical *status*; this file is the canonical *plan*. `09_NEAR_TERM_SPRINTS.md`
> and `PHASES.md` are history for anything this file covers.

## What "production" means here

Shilpi is an internal tool. Production means **IV consultants use it on live
bids, and what it produces goes to clients after a human edit**, with the
controls a tool holding IV's whole proposal bank needs. It does not mean
public, multi-tenant or 24x7.

Release gate. Every box must be ticked:

| Area | Gate |
|---|---|
| Security | Repo private · TLS on the frontend (or frontend reachable only through a tunnel or an IP allow-list) · RLS on every public table · `sarvam_018` applied · OpenRouter spend cap set · keys rotated after going private |
| Quality | 3 golden RFPs pass the automated scorecard · 1 human read of one draft (Imran as product owner; Ashish 30-minute spot-check strongly recommended) · no known Sev-1 defect open |
| Reliability | Tests run on every push (CI) · nightly backups copied off the host · deploy with a one-command rollback · health check that alerts |
| Operability | Runbook (deploy, rollback, restore, rotate keys) · 1-page user guide · cost per proposal logged |

## The constraint, and how the plan handles it

**There is one tester: Imran.** Agile normally leans on a team to review and
test. Here:

- **Claude builds; Imran runs, decides and accepts.** Imran is product owner and
  the only QA, so his time goes on decisions and runs, never on checks a
  machine can do.
- **An automated proposal scorecard replaces a second tester for regressions.**
  Every run is scored against the IV original for the same deal (words, images,
  tables, heading depth, repetition, SME markers, TBC cells, leaked client
  names, invented systems, required headings). A human read is then needed
  only for judgement, not for counting.
- **Paid runs are the bottleneck, not code.** At about $0.50 and 40 minutes
  per run, each sprint has a run budget. Fixes are batched so one run tests many.
- **Golden set of 3 deals, each with a real IV proposal to compare against:**
  ESNAD (multi-vendor SaaS, Ping + Saviynt), Amlak (single-vendor SailPoint,
  greenfield), BTPN (scoped version upgrade, migration). These cover the three
  template paths. Fixtures stay local (client content, never committed).

## Working agreement (solo agile)

| Ceremony | Solo form |
|---|---|
| Sprint planning | Start of sprint: Imran confirms the sprint goal and backlog below (a 10-minute message) |
| Daily | Each test run is the stand-up: docx + scorecard in, triage out |
| Sprint review | Scorecard trend + one docx Imran would or would not send |
| Retro | Lessons appended to `docs/02_LESSONS.md` |

- **Definition of Ready:** acceptance criteria stated, and any decision only
  Imran can make is already made.
- **Definition of Done:** code merged, tests added (each with a negative
  control), full suite green, deployed to the host, verified on a real run or
  in the live system, README updated.
- **WIP limit:** one paid run in flight. Nothing new is built on an unscored run.
- **Sev-1** = a client could see something wrong or unsafe (another client's
  name, an invented commitment, a security exposure). It interrupts the sprint.

## Sprints (4 weeks; go-live target Thursday 22 October 2026)

### Sprint 0: Baseline (Fri 25 – Mon 28 Sep)

Goal: a measured starting point.

| # | Item | Acceptance | Who |
|---|---|---|---|
| 0.1 | Score the ESNAD rerun against 09-25 and IV V3.0 | Images, repetition, exec-summary words, domain scope, Gantt all measured | Claude |
| 0.2 | `scripts/score_proposal.py`: the scorecard as code | Takes a docx + an optional baseline docx and emits JSON + pass/fail against thresholds; runs on the 3 existing ESNAD runs and reproduces the manual numbers | Claude |
| 0.3 | Triage 0.1 into the Sprint 1–2 backlog | Each defect sized and marked Sev-1/2/3 | Claude, Imran accepts |

Run budget: 1 (ESNAD, already running).

### Sprint 1: Lock it down (Tue 29 Sep – Fri 2 Oct)

Goal: every security gate closed.

| # | Item | Acceptance | Who |
|---|---|---|---|
| 1.1 | Repo private | GitHub shows Private; host pulls through the deploy key (already working) | Imran (one click) |
| 1.2 | RLS on `visual_assets` | Every reader and writer (brain, scripts) uses the service-role key, which bypasses RLS, so enabling RLS with no policies closes anon access without breaking anything. Verified: `SET ROLE anon` returns 0 rows; brain image placement still works | Claude applies after Imran's yes |
| 1.3 | Apply `sarvam_018` | Advisor shows 0 warnings for SECURITY DEFINER / search_path; retrieval RPCs return results | Claude after Imran's yes |
| 1.4 | Frontend exposure | Choose one: (a) security group allow-list to IV office/VPN IPs + Caddy TLS on a DNS name, or (b) close 8080 and use an SSH tunnel. Port 8080 is not reachable from the internet over plain HTTP | Imran picks; Claude writes config |
| 1.5 | OpenRouter spend cap | Monthly limit set on the key | Imran (dashboard) |
| 1.6 | Rotate secrets | OpenRouter and Supabase service keys rotated once after 1.1; `sarvam.env` updated by Imran | Imran |
| 1.7 | Off-host backups | Nightly backup copied to S3 (bucket with versioning, 30-day lifecycle) using the EC2 instance role; restore rehearsed once into a scratch table | Claude script, Imran creates bucket/role |

Run budget: 0. Security work needs no paid runs.

### Sprint 2: Prove it generalises (Mon 5 – Fri 9 Oct)

Goal: the golden set passes; the 09-25 fixes are shown to be generic, not ESNAD-fitted.

| # | Item | Acceptance | Who |
|---|---|---|---|
| 2.1 | Amlak run (single-vendor SailPoint) | Scorecard passes vs IV Amlak; no regression from the domain restructure (single-domain path kept its facets) | Imran runs, Claude scores |
| 2.2 | BTPN run (scoped migration) | Scorecard passes vs IV BTPN; document stays compact (scale filter) | Imran runs, Claude scores |
| 2.3 | Fix batch from 2.1 and 2.2 | All Sev-1/2 closed; one confirming run per affected deal | Claude |
| 2.4 | First MSS run (any MSS RFP in the Drive `RFP/` set) | Produces a complete document; defects triaged | Imran runs |
| 2.5 | Vendor kit for the next likely vendor (SailPoint) | Analyst view + product slides + reference architecture from a SailPoint deck, placed by `asset_selection.KIT` | Imran supplies deck, Claude builds |

Run budget: 5 to 6.

### Sprint 3: Run it like a service (Mon 12 – Fri 16 Oct)

Goal: it can be operated and recovered by someone other than Claude.

| # | Item | Acceptance | Who |
|---|---|---|---|
| 3.1 | CI | GitHub Actions runs the full suite on every push (keyless tests only); a red build blocks deploy | Claude |
| 3.2 | Deploy + rollback | `deploy/deploy.sh` builds a tagged image; `deploy/rollback.sh <tag>` restores the previous one in under 2 minutes; rehearsed once | Claude, Imran rehearses |
| 3.3 | Health alerting | 5-minute check of `/health` and `/v1/keepalive` alerting by email (AWS SNS or an uptime service) | Claude config, Imran subscribes |
| 3.4 | Cost per proposal logged | Each generated proposal row records tokens and cost from OpenRouter usage; README cost figure comes from data | Claude |
| 3.5 | Per-user identity | Open WebUI forwards the user's identity to the brain; `approved_by` on diagrams and `created_by` on proposals are filled | Claude |
| 3.6 | Runbook + user guide | `docs/RUNBOOK.md` (deploy, rollback, restore, rotate, add a vendor kit); `docs/USER_GUIDE.md` (1 page: attach RFP, answer gaps, approve diagrams, what to edit before sending) | Claude, Imran reviews |

Run budget: 1 (regression run after 3.5).

### Sprint 4: Release (Mon 19 – Thu 22 Oct)

Goal: go-live.

| # | Item | Acceptance | Who |
|---|---|---|---|
| 4.1 | Full golden-set regression | 3 of 3 pass the scorecard on the release build | Imran runs |
| 4.2 | Human read | Imran reads one full draft as the client would; Ashish 30-minute spot-check of the solution and commercial sections if at all possible | Imran (+ Ashish) |
| 4.3 | Release gate review | Every box in the gate table ticked, or explicitly waived with the reason written here | Imran |
| 4.4 | Tag `v1.0.0`, onboard first colleague | Tagged image deployed; one colleague account created; user guide sent | Imran |

Run budget: 3.

## Risks

| Risk | Mitigation |
|---|---|
| Only one person has judged quality | The scorecard catches regressions; the Ashish spot-check is the cheapest real second opinion. If skipped, the release notes say so |
| Fixes are tuned to ESNAD | Sprint 2 runs two structurally different deals before anything else is built |
| Vendor kit only for Ping/Saviynt | Other vendors still get house slides + partner images; each new vendor deck is roughly a 1-hour add |
| Paid-run budget overruns | WIP limit of 1; OpenRouter cap (1.5); fixes batched per run |
| Host is a single EC2 box | Off-host backups (1.7), rollback (3.2), documented rebuild in the runbook |

## Decisions only Imran can make (needed by Sprint 1)

1. Yes/no: enable RLS on `visual_assets` with no policies (1.2) and apply `sarvam_018` (1.3).
2. Frontend option for 1.4: (a) allow-list + TLS on a DNS name, or (b) SSH tunnel only.
3. OpenRouter monthly cap amount (1.5).
4. S3 bucket and instance role for backups (1.7), or approval for Claude to script them.
5. Which colleague is first after go-live (4.4).
