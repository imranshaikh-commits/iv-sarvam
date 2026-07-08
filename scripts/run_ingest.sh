#!/usr/bin/env bash
# Sarvam ingest launcher
# Usage: ./run_ingest.sh
# Reads env from ./sarvam.env (in same dir as this script)

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

if [[ ! -f sarvam.env ]]; then
  echo "ERROR: sarvam.env not found in $SCRIPT_DIR"
  echo "Create it with:"
  echo "  OPENROUTER_API_KEY=sk-or-..."
  echo "  SUPABASE_URL=https://jthrjmiulefmyrqtwsnz.supabase.co"
  echo "  SUPABASE_KEY=eyJ..."
  echo "  ORG_ID=5ec29afe-13ff-4657-a4cd-9a078226cdc2"
  exit 1
fi

# shellcheck disable=SC1091
set -a
source ./sarvam.env
set +a

: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY missing}"
: "${SUPABASE_URL:?SUPABASE_URL missing}"
: "${SUPABASE_KEY:?SUPABASE_KEY missing}"
: "${ORG_ID:?ORG_ID missing}"

INPUT_DIR="${INPUT_DIR:-$HOME/proposals}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/sarvam_out}"
mkdir -p "$OUTPUT_DIR"

echo "════════════════════════════════════════════════════════════════"
echo "Sarvam Ingest Batch — starting $(date)"
echo "  Input:  $INPUT_DIR"
echo "  Output: $OUTPUT_DIR"
echo "  Files:  $(ls -1 "$INPUT_DIR"/*.docx "$INPUT_DIR"/*.pdf 2>/dev/null | wc -l)"
echo "════════════════════════════════════════════════════════════════"

python3 "$SCRIPT_DIR/ingest_v2.py" \
  --input "$INPUT_DIR" \
  --output "$OUTPUT_DIR" \
  2>&1 | tee -a "$OUTPUT_DIR/ingest_$(date +%Y%m%d_%H%M%S).log"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "Batch complete — $(date)"
echo "See summary: $OUTPUT_DIR/run_summary.json"
echo "════════════════════════════════════════════════════════════════"
