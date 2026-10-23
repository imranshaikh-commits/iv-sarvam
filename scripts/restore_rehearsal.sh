#!/usr/bin/env bash
# Restore rehearsal (deep audit F46): rebuild Shilpi's data from the backups
# into a SCRATCH Supabase project and a throwaway Open WebUI container, then
# print what came back next to what was backed up.
#
#   SCRATCH_SUPABASE_URL=https://<ref>.supabase.co \
#   SCRATCH_SUPABASE_KEY=<scratch service_role key> \
#   SCRATCH_DB_URL='postgresql://postgres.<ref>:<password>@<pooler host>:5432/postgres' \
#   scripts/restore_rehearsal.sh ~/sarvam-backups/<STAMP> [~/sarvam-backups/owui/owui-<STAMP>.tgz]
#
# The target comes from those three variables only: this script never reads the
# production env file, and it refuses any target naming the production project.
# Use the Session pooler connection string for SCRATCH_DB_URL (the direct one is
# IPv6 only and the EC2 host has no IPv6 route).
#
# Steps: 1. every migration in supabase/migrations, in file order; 2. every
# table in the backup, parents first (backup_tables.py's foreign-key order);
# 3. the Storage mirror; 4. the Open WebUI volume into a throwaway container;
# 5. counts, backup next to restored. Nothing production is written.
set -euo pipefail
cd "$(dirname "$0")/.."
PROD_REF=jthrjmiulefmyrqtwsnz

: "${SCRATCH_SUPABASE_URL:?set SCRATCH_SUPABASE_URL to the scratch project URL}"
: "${SCRATCH_SUPABASE_KEY:?set SCRATCH_SUPABASE_KEY to the scratch project service_role key}"
: "${SCRATCH_DB_URL:?set SCRATCH_DB_URL to the scratch project Session pooler connection string}"
for target in "$SCRATCH_SUPABASE_URL" "$SCRATCH_DB_URL"; do
  case "$target" in
    *"$PROD_REF"*) echo "REFUSED: the target names the production project ($PROD_REF). Use a scratch project."; exit 2 ;;
  esac
done

BACKUP=${1:?usage: restore_rehearsal.sh <backup dir> [open webui archive]}
STORAGE=${STORAGE_DIR:-$HOME/sarvam-backups/storage}
OWUI_ARCHIVE=${2:-$(ls -1t "$HOME"/sarvam-backups/owui/owui-*.tgz 2>/dev/null | head -1)}
[ -d "$BACKUP" ] || { echo "no backup folder at $BACKUP"; exit 1; }
[ -f "$OWUI_ARCHIVE" ] || { echo "no Open WebUI archive found (pass one as the second argument)"; exit 1; }

# Every backup_tables.py call gets the scratch project, explicitly, and nothing else.
scratch() { SUPABASE_URL="$SCRATCH_SUPABASE_URL" SUPABASE_KEY="$SCRATCH_SUPABASE_KEY" python3 scripts/backup_tables.py "$@"; }

echo "== 1/5 migrations into the scratch project"
for f in $(ls supabase/migrations/*.sql | sort); do
  echo "   $(basename "$f")"
  docker run --rm -i postgres:17-alpine psql "$SCRATCH_DB_URL" -q -v ON_ERROR_STOP=1 \
    --set=migration="$(basename "$f")" < "$f"
done

echo "== 2/5 tables, parents first"
scratch --restore "$BACKUP" --tables all

echo "== 3/5 storage"
scratch --restore-storage "$STORAGE"

echo "== 4/5 Open WebUI volume into a throwaway container"
WEBUI_IMAGE=$(docker inspect sarvam-webui --format '{{.Config.Image}}')
VOL=owui-rehearsal-vol
docker volume create "$VOL" >/dev/null
cleanup() { docker rm -f owui-rehearsal >/dev/null 2>&1 || true; docker volume rm "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --rm -v "$VOL":/data -v "$(dirname "$OWUI_ARCHIVE")":/in:ro alpine \
  sh -c "tar xzf /in/$(basename "$OWUI_ARCHIVE") -C /data"
docker run -d --name owui-rehearsal -v "$VOL":/app/backend/data -p 127.0.0.1:18080:8080 "$WEBUI_IMAGE" >/dev/null
UP=no
for _ in $(seq 1 60); do
  if curl -fsS -m 5 http://127.0.0.1:18080/health >/dev/null 2>&1; then UP=yes; break; fi
  sleep 2
done
echo "   restored Open WebUI answers /health: $UP"
COUNT='import sqlite3;c=sqlite3.connect("file:/d/webui.db?mode=ro",uri=True);q=lambda t:c.execute(f"select count(*) from \"{t}\"").fetchone()[0];print(f"users={q(\"user\")} chats={q(\"chat\")} files={q(\"file\")}")'
SRC=$(docker run --rm -v open-webui:/d:ro --entrypoint python3 "$WEBUI_IMAGE" -c "$COUNT")
GOT=$(docker run --rm -v "$VOL":/d:ro --entrypoint python3 "$WEBUI_IMAGE" -c "$COUNT")
echo "   open webui  live: $SRC"
echo "   open webui  restored: $GOT"

echo "== 5/5 counts, backup next to restored"
scratch --compare "$BACKUP" --storage-dir "$STORAGE"
echo "rehearsal finished; the throwaway container and volume are removed on exit"
