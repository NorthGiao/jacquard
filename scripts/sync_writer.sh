#!/bin/bash
# Push the files in writer/labview_mcp/*.py that differ from those on the LabVIEW machine. Printing SYNCED means there were changes (the caller then rebuilds everything with --force).
set -u
. "$(dirname "$0")/_env.sh"
REM=$("${SSH[@]}" "Get-ChildItem '$REMOTE_SHLIM\\*.py' | % { \$_.Name + '|' + (Get-FileHash \$_.FullName).Hash }" | tr -d '\r') || { echo "can't read the writer hashes on the LabVIEW machine"; exit 1; }
CHG=()
for p in "$WRITER"/*.py; do
  f=$(basename "$p"); L=$(sha256sum "$p" | cut -c1-64)
  R=$(echo "$REM" | awk -F'|' -v f="$f" '$1==f{print tolower($2)}')
  [ "$L" != "$R" ] && CHG+=("$f")
done
[ ${#CHG[@]} -eq 0 ] && exit 0
tar -cf - -C "$WRITER" "${CHG[@]}" | "${SSH[@]}" "tar -xf - -C $REMOTE_SHLIM_FWD" || { echo "failed to sync the writer"; exit 1; }
echo "SYNCED ${CHG[*]}"
