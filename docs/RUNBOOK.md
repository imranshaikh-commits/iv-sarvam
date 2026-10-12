# Shilpi Runbook

For whoever operates Shilpi. Every command runs on the EC2 host
(`ssh -i sarvam-server-key.pem ubuntu@13.206.20.25`) unless it says otherwise.
The repo lives at `~/iv-sarvam`; secrets live in `~/iv-sarvam/scripts/sarvam.env`
(never committed, never pasted into chat or tickets).

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Caddy (HTTPS) | container `sarvam-caddy`, ports 80/443 | certificate for `SHILPI_DOMAIN` (default `13-206-20-25.sslip.io`) renews itself |
| Open WebUI (chat) | container `sarvam-webui`, loopback 8080 | accounts, chats; volume `open-webui` |
| Brain (drafting) | container `sarvam-brain`, loopback 8000 | image `sarvam-brain:<commit>` |
| Database + files | Supabase project `jthrjmiulefmyrqtwsnz` | free tier: pauses after 7 idle days, hence the keepalive |
| Models | OpenRouter | key `OPENROUTER_API_KEY` in `sarvam.env` |
| Backups | `~/sarvam-backups` and S3 `iv-sarvam-backups-430689518268` | 30-day retention in S3 |

Scheduled jobs (`crontab -l`, all UTC): 00:54 keepalive, 02:15 table backup,
02:20 Open WebUI volume, Sunday 02:45 backup including proposal chunks and
the Storage buckets, 03:15 sync of everything to S3.

## What a full recovery needs

| Piece | Holds | Backed up by |
|---|---|---|
| Supabase tables | sessions, proposals, diagrams, corpus, approvals | `scripts/backup_tables.py` nightly |
| Supabase Storage | rendered diagrams, generated DOCX/PDF, image library, vendor kit | `backup_tables.py --with-storage` weekly (`~/sarvam-backups/storage/`) |
| Open WebUI volume | accounts, chats, uploaded RFPs | `scripts/backup_owui.sh` nightly (`~/sarvam-backups/owui/`, last 7) |
| `scripts/sarvam.env` | the keys | **nothing: keep a copy in the password manager** |
| Code and migrations | this repo | GitHub |

A restore into a brand-new Supabase project has not been rehearsed end to
end. Do that drill before relying on these backups for a real outage.

## Health

- Public check: `curl https://13-206-20-25.sslip.io/healthz` returns
  `{"status":"ok","database":"reachable"}` (HTTP 200), 503 when the database is
  unreachable, 502 when the brain is down.
- Full detail (models, flags), host only: `curl -s http://127.0.0.1:8000/health`
- Containers: `docker ps` (all three `Up`); logs: `docker logs --tail 100 sarvam-brain`
- An external uptime monitor polls `/healthz` every 5 minutes and emails on failure.

## Deploy

```
~/iv-sarvam/deploy/deploy.sh
```

This pulls `main`, builds `sarvam-brain:<commit>`, runs the test suite inside
that image (a failing build stops here), swaps the container and waits until
the brain answers with the database reachable and Open WebUI's upload folder
mounted (`rfp_uploads_mounted` in `/health`). If the new build never gets
there it puts the previous one back by itself and says why. It deploys the
brain only.

- Open WebUI or Caddy changes (`deploy/Dockerfile.webui`, `deploy/Caddyfile`,
  their settings in `deploy/docker-compose.yml`):
  `cd ~/iv-sarvam/deploy && docker compose up -d --build open-webui caddy`
- A database migration (`supabase/migrations/sarvam_NNN_*.sql`) is applied in
  the Supabase SQL editor **before** deploying code that needs it.

## Roll back

```
~/iv-sarvam/deploy/rollback.sh            # the previous deploy
~/iv-sarvam/deploy/rollback.sh 938fcbc    # a specific commit
docker images sarvam-brain                # what is available (last 5 kept)
```

No rebuild, so it takes seconds. Roll back first, investigate second.

## Restore data

Backups are gzipped JSON lines per table, one folder per night.

1. Pick a night. Local: `ls ~/sarvam-backups`. From S3 (older than the host copy):
   `/snap/bin/aws s3 cp s3://iv-sarvam-backups-430689518268/tables/<STAMP>/ ~/sarvam-backups/<STAMP>/ --recursive`
2. Restore the tables you need:
   `cd ~/iv-sarvam && python3 scripts/backup_tables.py --env-file scripts/sarvam.env --restore ~/sarvam-backups/<STAMP> --tables generated_proposals`

Restore upserts by primary key and never deletes: rows in the backup are written
back, rows created since are left alone. Tables are written parents first,
whatever order you list them in. Storage files are under
`~/sarvam-backups/storage/<bucket>/`; upload them back through the Supabase
dashboard or the Storage API. The Open WebUI volume restores with the command
at the top of `scripts/backup_owui.sh`.

If Supabase has **paused** the project (idle 7 days): open the Supabase
dashboard, restore the project, then `curl -s http://127.0.0.1:8000/v1/keepalive`.

## Access control

- **Service key.** With `SHILPI_API_KEY` set in `sarvam.env`, every brain
  route except `/health` and `/v1/keepalive` needs it as a Bearer token. Open
  WebUI sends the API key of its connection: Admin Settings → Connections →
  the Shilpi connection → API Key. Set both to the same value, then recreate
  the brain. Unset, the brain logs a warning and accepts any caller on its
  network.
- **Whose data.** Open WebUI forwards the signed-in user
  (`ENABLE_FORWARD_USER_INFO_HEADERS`). A session opened by one user is not
  readable by another, and an uploaded RFP is read only by its uploader.
- **"I can't confirm that the attached document is yours."** The brain could
  not identify the requester or the uploader. Check, in order:
  `docker exec sarvam-webui env | grep FORWARD_USER` (must be `true`), then
  `docker exec sarvam-brain python3 -c "import sqlite3;print(sqlite3.connect('file:/owui-data/webui.db?mode=ro',uri=True).execute('select count(*) from file').fetchone())"`.
  `SHILPI_ALLOW_UNBOUND_UPLOADS=1` restores "newest recent upload" and is only
  for an install with a single user.
- **Drafting refused.** "Architecture is not approved yet" (approve or
  regenerate the named diagrams), "already being generated" (wait), or "N
  proposals are being drafted" (limit `SHILPI_MAX_CONCURRENT_DRAFTS`, default 2).

## Rotate a key

1. Create the new key (OpenRouter dashboard, or Supabase → Project Settings → API).
2. Edit `~/iv-sarvam/scripts/sarvam.env` on the host and replace the value.
3. `cd ~/iv-sarvam/deploy && docker compose up -d --force-recreate sarvam-brain`
4. `curl -s http://127.0.0.1:8000/v1/keepalive` returns `ok`; run one chat turn.
5. Delete the old key.

The backup job reads the same `sarvam.env`, so nothing else needs editing.

## Add a vendor kit (images for a new vendor)

1. Export the slides as PNG, at least 1,200 px wide (anything smaller prints
   blurry and is dropped by the sharpness gate).
2. Add one line per image to `KIT` in `backend/brain/asset_selection.py`
   (section, heading, vendor, domains, `near`), with a test, and deploy.
3. Upload the images:
   `docker cp ~/asset_kit sarvam-brain:/tmp/asset_kit && docker exec -i sarvam-brain python3 - /tmp/asset_kit < ~/iv-sarvam/scripts/upload_asset_kit.py`

## Where to look when

| Symptom | First check |
|---|---|
| Site does not load | `docker ps`; `docker logs --tail 50 sarvam-caddy` (certificate) |
| Chat answers but drafting fails | `docker logs --tail 200 sarvam-brain`; `/healthz` for the database |
| Every call fails with 402 | OpenRouter credit or key limit |
| Stuck after uploading an RFP | brain logs for `RFP page`; a page call is bounded at 150 s |
| Images missing or blurry | brain logs for `image dropped`; kit uploaded? |
| What did a proposal cost | `generated_proposals.llm_usage`, or `docker logs sarvam-brain 2>&1 \| grep "proposal cost"` |
| Who made a proposal | `generated_proposals.created_by_email`; diagrams: `approved_by_email` |
