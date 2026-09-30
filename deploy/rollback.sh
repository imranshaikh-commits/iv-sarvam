#!/usr/bin/env bash
# Put a previous brain image back. No rebuild, so it takes seconds.
#
#   ~/iv-sarvam/deploy/rollback.sh          # the deploy before the current one
#   ~/iv-sarvam/deploy/rollback.sh 938fcbc  # a specific tag
#   docker images sarvam-brain              # tags available
set -euo pipefail
cd "$(dirname "$0")/.."
HIST=deploy/.deploy_history
CUR=$(docker inspect sarvam-brain --format '{{.Config.Image}}' | sed 's/.*://')
TAG=${1:-$(grep -vx "$CUR" "$HIST" 2>/dev/null | tail -1 || true)}
[ -n "$TAG" ] || { echo "no earlier deploy recorded; pass a tag (docker images sarvam-brain)"; exit 1; }
docker image inspect "sarvam-brain:$TAG" >/dev/null
BRAIN_TAG=$TAG docker compose -f deploy/docker-compose.yml up -d --no-build sarvam-brain
for _ in $(seq 1 45); do
  curl -fsS -m 5 http://127.0.0.1:8000/v1/keepalive >/dev/null 2>&1 && { echo "rolled back $CUR -> $TAG"; exit 0; }
  sleep 2
done
echo "sarvam-brain:$TAG is not healthy either; check: docker logs sarvam-brain"; exit 1
