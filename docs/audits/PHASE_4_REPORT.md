# Phase 4 report: release hardening

Branch `audit/phase-4`, off `main` at `f32827b`. For review by the main
session. 2026-10-23.

Six items, six commits, a test with every code change. Suite: **909 passed,
4 skipped**. Nothing here has run on the host yet: both rehearsals are
commands for Imran, and the deploy and reader changes are tested with fakes
only.

## What changed

| Item | Commit | Change | Test that fails without it |
|---|---|---|---|
| 1. F46 restore rehearsal | `a238754` | `scripts/restore_rehearsal.sh`: every migration in file order (psql through `postgres:17-alpine`), every table in the backup parents first, the Storage mirror, the Open WebUI volume into a throwaway container on 127.0.0.1:18080, then counts backup next to restored (tables, buckets, Open WebUI users/chats/files). Target only from `SCRATCH_SUPABASE_URL`, `SCRATCH_SUPABASE_KEY`, `SCRATCH_DB_URL`; refuses any target naming `jthrjmiulefmyrqtwsnz`; never reads `sarvam.env`. `backup_tables.py` gains `--tables all`, `--restore-storage`, `--compare`. Runbook section with Imran's commands; keys are entered with `read -s`, so they stay out of shell history. | `scripts/test_restore_rehearsal.py` (refuses production by URL and by DB string with nothing run; no env-file use; migrations, tables, storage, Open WebUI, counts in that order, all against the scratch URL), `scripts/test_backup_tables.py` (3 new) |
| 2. Rollback rehearsal | `628ba31` | Runbook section, commands only. The known-bad build is branch `rehearsal/known-bad` (`51c6ea0`, **never merge**): it passes the in-image tests (the test run overrides the start command) but listens on 8001, so `deploy.sh`'s health check fails and it must roll back by itself. Then `rollback.sh` to a named tag, `/health` checked, and back. No code change: nothing has run, so no gap is shown yet. | none (no code) |
| 3. Uptime monitor | `ce27a57` | Brain `GET /healthz`: `ok` (200) or `unavailable` (503), plain text, open without the service key. **Caddy unchanged.** | `test_chat_interview_gating.py::test_the_public_health_route_says_only_ok` |
| 4. Disk | `f4dcb70` | `deploy.sh` checks free space on Docker's root before pulling and stops with the amount free and what to clear if under 3 GB (`SHILPI_DEPLOY_MIN_FREE_GB`). After a healthy deploy it keeps the running image, `previous` and the two newest tags, removes other tags, and runs `docker builder prune -af`. A failed or rolled-back deploy prunes nothing. | `scripts/test_deploy_gate.py::test_a_deploy_stops_before_building_when_the_disk_is_nearly_full`, `…::test_a_successful_deploy_keeps_running_previous_and_two_newest_and_prunes_build_cache` (`…::test_a_failed_deploy_prunes_nothing` held before too) |
| 5. Reading follow-ups | `4c23916` | A failed page or page-part call is tried once more before the page is marked not read. The accuracy harness scores both reads ("read 1: x/7, read 2: y/7") and lists the second read's wrong facts. | `test_rfp_intake.py::test_a_page_call_that_fails_once_is_read_on_the_retry`, `…::test_a_page_that_fails_twice_is_not_read`, `scripts/test_rfp_accuracy.py::test_both_reads_are_scored` |
| 6. Diagram plan editor | `075b835` | "replace X with Y" is a drop and an add, Y in X's place, all or nothing (a drop alone would lose a diagram); an unknown Y goes to the model like a plain add. Each derived diagram has its own description: the two journeys **and** the three flows (Lifecycle, PAM, Governance) all shared their engine type's line. | `test_chat_state.py::test_replace_x_with_y_is_a_drop_and_an_add`, `…::test_replace_falls_back_to_the_model_for_an_unknown_new_diagram`, `…::test_every_derived_diagram_has_its_own_description` |

One existing test changed: the dense-page test counted calls exactly; the
failing part is now tried twice (item 5).

## Item 3: the health route, as found

- **Caddy exposes `/healthz` publicly** (`deploy/Caddyfile`): `rewrite * /v1/keepalive`
  to the brain. Only that path; nothing else of the brain is reachable.
- It answers `{"status":"ok","database":"reachable"}` (200) or `degraded`/
  `unreachable` (503). **No build tag**, but the database state is internal.
- `RUNBOOK.md` says "an external uptime monitor polls `/healthz` every 5
  minutes". I cannot see whether one exists; Imran to confirm.

**Proposal, smallest first:**
1. **External free monitor (owner action, Imran).** UptimeRobot or Better
   Stack free tier, HTTPS check of `https://13-206-20-25.sslip.io/healthz`
   every 5 minutes, alert by email. It also catches a whole-host outage,
   which no cron on the host can.
2. Then switch Caddy to the new brain route, which says only `ok`: in
   `deploy/Caddyfile` delete the line `rewrite * /v1/keepalive` (keep
   `reverse_proxy sarvam-brain:8000`), then
   `cd ~/iv-sarvam/deploy && docker compose up -d caddy`. Not done, as asked.
3. A host cron that alerts is the bigger option: the host has no mail
   transport, so it needs SES or SNS set up first, and it is blind when the
   host itself is down.

## Things the reviewer should look at

1. **`docker builder prune -af` empties the whole build cache.** The next
   deploy rebuilds every layer, including the apt layer with LibreOffice and
   D2, so it will take several minutes instead of about one. If that is too
   slow, `--keep-storage 1GB` keeps the base layers. As asked, it prunes all.
2. **Known gaps from reading, not yet shown by a rehearsal** (no change
   made, per item 2): after `deploy.sh` rolls back, `/health` reports the
   build as `previous`, not the commit; and `rollback.sh` waits for
   `/v1/keepalive`, not for `/health` to report the requested tag. The
   runbook checks `/health` after each step, so the rehearsal will show
   whether either matters.
3. **Restore rehearsal unknowns.** The first run is the test. Most likely
   failures: a migration that assumed production's history (production
   applied `sarvam_001..004` under other names than `001_init.sql`); the
   weekly Storage mirror being up to a week behind the nightly table backup
   (counts will differ by what was added since); disk space for the
   restored Open WebUI volume (the runbook checks `df` first); the Supabase
   free tier allowing two projects.
4. **`rehearsal/known-bad`** is pushed so the host can check it out. It
   must never be merged; delete it after the rehearsal
   (`git push origin --delete rehearsal/known-bad`).
5. **Replace** applies only when exactly one diagram is dropped and one
   added; "replace integration and security with …" changes nothing and
   re-prompts.

## Not verified

- Neither rehearsal has run. Imran's commands are in `docs/RUNBOOK.md`
  ("Restore rehearsal", "Rollback rehearsal"); their outputs belong in this
  report.
- `deploy.sh`'s disk check and pruning are tested with fake `docker` and
  `df`, not on the host.
- The page retry and the both-reads scoring have not run against a model.
