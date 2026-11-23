#!/bin/bash
# Usage: lab_run.sh <lab script name> <per-round timeout in seconds> <max rounds> <completion marker> [script arguments...]
# Push lab/*.py, catalog/probe_list.json (→probes.json), jobs/ to <REMOTE_BASE>\code\ on the LabVIEW machine and run them with the Python there under a timeout;
# at the end of each round only stop the LabVIEW "started in this round, in session 0" (the LabVIEW on the user's desktop is never touched).
# If the output has no completion marker, run another round (the scripts resume on their own, and hung entries are recorded as hung and skipped).
set -u
. "$(dirname "$0")/_env.sh"
NAME="$1"; TMO="$2"; ROUNDS="$3"; MARK="$4"; shift 4
REMOTE="$REMOTE_BASE\\code"
"${SSH[@]}" "New-Item -ItemType Directory -Force '$REMOTE' | Out-Null" || exit 1
# always a tar stream (scp truncates large files at ~200 KiB, measured on-site)
STAGE=$(mktemp -d "$REPO/private/.stage.XXXX")
cp "$REPO"/lab/*.py "$STAGE/" && cp "$REPO/catalog/probe_list.json" "$STAGE/probes.json" && cp -r "$REPO/jobs" "$STAGE/jobs"
tar -cf - -C "$STAGE" --exclude=__pycache__ . | "${SSH[@]}" "tar -xf - -C $REMOTE_BASE_FWD/code" || { rm -rf "$STAGE"; echo "failed to push code"; exit 1; }
rm -rf "$STAGE"
ARGLIST=""; for a in "$@"; do ARGLIST="$ARGLIST,'\"$a\"'"; done  # quote each argument so paths with spaces aren't split
for r in $(seq 1 "$ROUNDS"); do
  echo "==== round $r ($NAME, timeout ${TMO}s)"
  OUT=$("${SSH[@]}" "[Console]::OutputEncoding=[Text.Encoding]::UTF8; \$env:PYTHONIOENCODING='utf-8'; \$env:LVAGENT_SIMDEV='${LVAGENT_SIMDEV:-SimDev}'; \$env:PYTHONPATH='$REMOTE'; \$py='$LAB_PY'; \$pre=@(Get-Process -Name LabVIEW -ErrorAction SilentlyContinue | % { \$_.Id }); \$o='$REMOTE\\$NAME.out.txt'; \$e='$REMOTE\\$NAME.err.txt'; \$p = Start-Process \$py -WorkingDirectory '$REMOTE' -ArgumentList @('$REMOTE\\$NAME'$ARGLIST) -PassThru -NoNewWindow -RedirectStandardOutput \$o -RedirectStandardError \$e; if (-not \$p.WaitForExit($TMO*1000)) { 'TIMEOUT: stopping python '+\$p.Id; Stop-Process -Id \$p.Id -Force }; Get-Content \$o -Encoding UTF8; '---- stderr'; Get-Content \$e -Encoding UTF8 | Select-Object -Last 30; Start-Sleep 8; Get-Process -Name LabVIEW -ErrorAction SilentlyContinue | ? { \$_.SessionId -eq 0 -and \$pre -notcontains \$_.Id } | % { 'stopping session-0 LabVIEW started this round, pid '+\$_.Id; Stop-Process -Id \$_.Id -Force }; '---- LabVIEW now running:'; Get-Process -Name LabVIEW -ErrorAction SilentlyContinue | % { '  pid='+\$_.Id+' session='+\$_.SessionId }")
  echo "$OUT"
  echo "$OUT" | grep -q "$MARK" && { echo "==== done ($MARK)"; exit 0; }
done
echo "==== still not done after $ROUNDS rounds"; exit 3
