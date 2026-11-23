#!/bin/bash
# Do one job in one round: local lint (no LabVIEW run with any E) → sync the writer if it changed (and rebuild everything) → lab/vi_job.py on the LabVIEW machine → pull back → wire check + summary → record in jobs/verified.json.
# Usage: scripts/vi_job.sh <job> [--force] [--only name1,name2]
set -u
. "$(dirname "$0")/_env.sh"
ARG="$1"   # passed as-is to the lint below: it accepts job names and jobs/<job>.py, and rejects .py paths elsewhere
case "$1" in *.py) JOB="$(basename "$1" .py)" ;; *) JOB="$1" ;; esac; shift   # same as the file-name half of jacq.lint.job_name; the LabVIEW machine only accepts job names
T0=$(date +%s)
JACQ_OFFLINE=0 $PY -m jacq.lint "$ARG" || { echo "==== lint has errors, not run on the LabVIEW machine (fix each E above according to its \"Fix\", then come back)"; exit 1; }
# command-line arguments are checked locally before syncing the writer (which writes to the LabVIEW machine over SSH): the same parsing as lab/vi_job.py, and --only names are also checked against this job's VIS (jacq/lint.py check_job_args)
$PY -c 'import sys; from jacq import lint; sys.exit(lint.check_job_args(sys.argv[1], sys.argv[2:]))' "$ARG" "$@" || { echo "==== wrong arguments, not run on the LabVIEW machine (and the writer wasn't synced)"; exit 1; }
S=$(bash "$REPO/scripts/sync_writer.sh") || { echo "$S"; exit 1; }
EXTRA=(); [ -n "$S" ] && { echo "$S: the writer changed, rebuilding everything"; EXTRA=(--force); }
OUT=$(bash "$REPO/scripts/lab_run.sh" vi_job.py 1500 1 "JOB COMPLETE" "$JOB" "$@" "${EXTRA[@]}")
echo "$OUT" | grep -vE "^\s*$|^---- |^  pid=|LabVIEW now running|^==== round"
if ! echo "$OUT" | grep -q "JOB COMPLETE"; then
  if grep -q "^BUILD_ONLY = True" "$REPO/jobs/$JOB.py"; then
    # probe batches write the report after each VI is built (complete=false): if stuck on some VI, the earlier outcomes are still pulled back for comparison
    echo "==== the LabVIEW machine didn't finish this round (stuck on some VI); BUILD_ONLY: pulling back the part already built for comparison"
    P=$(bash "$REPO/scripts/pull.sh" "write2/jobs/$JOB"); PRC=$?; echo "$P" | tail -1
    [ $PRC -eq 0 ] && $PY -m jacq.job_check "$JOB"
    exit 2
  fi
  # vi_job.py writes the report to disk after each VI (complete=false): pull it back so job_check can say how far it got; it won't record verified based on it.
  # only run job_check on a complete=false report: if the report pulled back is complete=true, it's from the previous round (this round crashed before writing a report) and can't be used for judging
  echo "==== the LabVIEW machine didn't finish this round (timeout/crash/LabVIEW hung). Above is the tail of the output; pulling back the completed part:"
  P=$(bash "$REPO/scripts/pull.sh" "write2/jobs/$JOB"); PRC=$?; echo "$P" | tail -1
  RPT="$LAB_CORPUS/write2/jobs/$JOB/job_report.json"
  if [ $PRC -eq 0 ] && $PY -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("complete") is False else 1)' "$RPT" 2>/dev/null; then
    $PY -m jacq.job_check "$JOB"
  else
    echo "==== this round left no report of its own (stopped before writing a report; any pulled back is from the previous round): not checking. See the tail of the output above"
  fi
  exit 2
fi
P=$(bash "$REPO/scripts/pull.sh" "write2/jobs/$JOB"); PRC=$?; echo "$P" | tail -1
[ $PRC -eq 0 ] || { echo "==== pull-back failed or file sizes mismatch (see the previous line): the local results can't be trusted, no check, not recording verified. Rerun this command after SSH recovers"; exit 2; }
$PY -m jacq.job_check "$JOB"; RC=$?
echo "==== elapsed $(( $(date +%s) - T0 )) s"
exit $RC
