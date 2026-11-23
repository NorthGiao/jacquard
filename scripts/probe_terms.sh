#!/bin/bash
# Read back function terminal names on the LabVIEW machine and write them into catalog/terminals_confirmed.json (only this script may write it).
# Usage: scripts/probe_terms.sh "Square Root" "Sine" ...      or   scripts/probe_terms.sh --queue   (runs what offline lint accumulated in catalog/probe_queue.json)
set -u
. "$(dirname "$0")/_env.sh"
cd "$REPO"
if [ "${1:-}" = "--queue" ]; then mapfile -t ARGS < <($PY -m jacq.catalog queue --names); else ARGS=("$@"); fi
[ ${#ARGS[@]} -eq 0 ] && { echo "no functions to probe"; exit 0; }
OUT=$(bash "$REPO/scripts/lab_run.sh" probe_terms.py 300 1 "PROBE TERMS COMPLETE" "${ARGS[@]}")
echo "$OUT" | grep -q "PROBE TERMS COMPLETE" || { echo "$OUT" | tail -15; echo "the probe didn't finish"; exit 2; }
echo "$OUT" | grep "^TERMS " | sed 's/^TERMS //' | $PY -m jacq.catalog record
