#!/usr/bin/env bash
# Deploy the brain at the current origin/main, tagged by commit.
#
#   ~/iv-sarvam/deploy/deploy.sh
#
# 1. pull, 2. build sarvam-brain:<sha>, 3. run the test suite INSIDE that image
# (a red build never ships), 4. swap the container, 5. wait for /v1/keepalive
# and a /health that reports this build with the uploads folder mounted,
# rolling back to the previous tag by itself if the new one never gets there.
# Undo a good-but-wrong deploy with deploy/rollback.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
TAG=$(git rev-parse --short HEAD)
HIST=${SHILPI_DEPLOY_HISTORY:-deploy/.deploy_history}   # one tag per line, newest last (gitignored)
# Whatever is running now, however it was built, stays available as :previous.
PREV=""
if CUR_ID=$(docker inspect sarvam-brain --format '{{.Image}}' 2>/dev/null); then
  docker tag "$CUR_ID" sarvam-brain:previous && PREV=previous
fi

# Not quiet: a silent build looked hung and was interrupted (2026-10-12).
echo "building sarvam-brain:$TAG (about a minute)"
docker build -t "sarvam-brain:$TAG" backend/brain
echo "built sarvam-brain:$TAG; running the test suite inside it (about a minute, no output until it ends)"
# No pipe after pytest: `pytest | tail -1` exits with tail's status, so a failing
# suite deployed anyway (audit 2026-10-12, finding 3). Output goes to a log; the
# exit status is pytest's own, and set -e stops the script here.
# SHILPI_REQUIRE_STACK=1: in this image a missing d2/soffice/pdftoppm FAILS the
# run instead of skipping (tests/test_production_stack.py).
if ! docker run --rm -e SHILPI_REQUIRE_STACK=1 -v "$PWD/backend/brain/tests:/app/tests:ro" --entrypoint sh "sarvam-brain:$TAG" \
     -c "pip install -q pytest >/dev/null 2>&1 && python -m pytest -q -p no:cacheprovider tests" \
     > /tmp/shilpi-deploy-tests.log 2>&1; then
  tail -30 /tmp/shilpi-deploy-tests.log
  echo "tests FAILED in sarvam-brain:$TAG; nothing was deployed"
  exit 1
fi
tail -1 /tmp/shilpi-deploy-tests.log

BRAIN_TAG=$TAG docker compose -f deploy/docker-compose.yml up -d --no-build sarvam-brain
WHY="did not become healthy in 90 s"
for _ in $(seq 1 45); do
  # Database reachable AND the container answering is this build, not a stale one.
  if curl -fsS -m 5 http://127.0.0.1:8000/v1/keepalive >/dev/null 2>&1 \
     && HEALTH=$(curl -fsS -m 5 http://127.0.0.1:8000/health) \
     && echo "$HEALTH" | grep -q "\"build\": *\"$TAG\""; then
    # The brain reads RFP attachments from Open WebUI's upload folder. Without
    # that mount the build is up and RFP upload is dead; waiting will not fix it.
    if ! echo "$HEALTH" | grep -q '"rfp_uploads_mounted": *true'; then
      WHY="is up but cannot see Open WebUI's uploads (rfp_uploads_mounted is not true), so RFP upload would not work"
      break
    fi
    echo "$TAG" >> "$HIST"
    echo "deployed sarvam-brain:$TAG (previous: ${PREV:-none})"
    # Keep the last five tagged images for rollback.
    docker images sarvam-brain --format '{{.Tag}}' | grep -vE '^(latest|previous)$' | tail -n +6 \
      | xargs -r -I{} docker rmi -f "sarvam-brain:{}" >/dev/null 2>&1 || true
    exit 0
  fi
  sleep 2
done
echo "sarvam-brain:$TAG $WHY"
if [ -n "$PREV" ]; then
  echo "rolling back to $PREV"
  BRAIN_TAG=$PREV docker compose -f deploy/docker-compose.yml up -d --no-build sarvam-brain
fi
exit 1
