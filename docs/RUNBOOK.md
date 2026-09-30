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

Scheduled jobs (`crontab -l`): 00:54 keepalive, 02:15 table backup, Sunday
02:45 backup including proposal chunks, 03:15 sync to S3 (all UTC).

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
the brain answers with the database reachable. If the new build never becomes
healthy it puts the previous one back by itself. It deploys the brain only.

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
back, rows created since are left alone. Storage buckets (rendered diagrams,
generated DOCX) are not in these backups.

If Supabase has **paused** the project (idle 7 days): open the Supabase
dashboard, restore the project, then `curl -s http://127.0.0.1:8000/v1/keepalive`.

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
