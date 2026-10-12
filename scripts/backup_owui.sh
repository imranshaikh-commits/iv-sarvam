#!/usr/bin/env bash
# Back up Open WebUI's volume: accounts, chats (which carry Shilpi's workflow
# markers) and the RFP/SOW files people uploaded. None of it is in Supabase,
# so the table backup alone cannot bring the service back (audit 2026-10-12,
# finding 11).
#
#   ~/iv-sarvam/scripts/backup_owui.sh            # -> ~/sarvam-backups/owui/
#
# Restore: stop the stack, then
#   docker run --rm -v open-webui:/data -v ~/sarvam-backups/owui:/in alpine \
#     sh -c "rm -rf /data/* && tar xzf /in/owui-<STAMP>.tgz -C /data"
#
# ponytail: a tar of a live SQLite file is crash-consistent, not a snapshot.
# It runs at night when nobody is chatting; use `sqlite3 .backup` if that stops
# being true.
set -euo pipefail
OUT=${1:-$HOME/sarvam-backups/owui}
mkdir -p "$OUT"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
docker run --rm -v open-webui:/data:ro -v "$OUT":/out alpine \
  tar czf "/out/owui-$STAMP.tgz" -C /data .
ls -1t "$OUT"/owui-*.tgz | tail -n +8 | xargs -r rm -f    # keep the last 7
echo "open-webui volume -> $OUT/owui-$STAMP.tgz ($(du -h "$OUT/owui-$STAMP.tgz" | cut -f1))"
