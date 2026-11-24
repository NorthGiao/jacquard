#!/bin/bash
# Usage: pull.sh <subdirectory under REMOTE_BASE, e.g. write2/jobs/mcpi>
# Pull back into private/lab_corpus/<subdirectory> (the user's VIs and dumps only go in private/, never into the repo). tar stream + per-file size comparison.
set -u
. "$(dirname "$0")/_env.sh"
DST="$LAB_CORPUS"; mkdir -p "$DST"
"${SSH[@]}" "tar -cf - --exclude=production --exclude=production_fake --exclude=.production.lock --exclude='*.part' -C $REMOTE_BASE_FWD '$1'" | tar -xf - -C "$DST" || { echo "pull-back failed $1"; exit 1; }
"${SSH[@]}" "[Console]::OutputEncoding=[Text.Encoding]::UTF8; \$b='$REMOTE_BASE\\'; Get-ChildItem -LiteralPath (\$b+'$1') -Recurse -File | % { '{0}|{1}' -f \$_.Length,\$_.FullName.Substring(\$b.Length).Replace('\\','/') }" > "$DST/.sizes_remote" 2>/dev/null
[ -s "$DST/.sizes_remote" ] || { echo "after pulling back, can't read the remote file list (SSH dropped?): can't check sizes $1"; rm -f "$DST/.sizes_remote"; exit 1; }
bad=0; while IFS='|' read -r n p; do p="${p%$'\r'}"; case "$p" in */production/*|*/production_fake/*|*/.production.lock|*.part) continue;; esac; l=$(stat -c %s "$DST/$p" 2>/dev/null || echo -1); [ "$l" = "$n" ] || { echo "size mismatch: $p remote $n local $l"; bad=$((bad+1)); }; done < "$DST/.sizes_remote"
echo "pulled back $DST/$1 ($(wc -l < "$DST/.sizes_remote") files, $bad mismatches)"; rm -f "$DST/.sizes_remote"; [ $bad -eq 0 ]
