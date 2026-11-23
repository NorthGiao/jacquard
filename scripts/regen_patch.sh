#!/bin/bash
# Regenerate the patch after changing writer/labview_mcp/{gen,lv}.py (tests/test_repo_hygiene.py checks upstream + patch == the current writer).
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R/writer" && { diff -u --label a/src/labview_mcp/gen.py --label b/src/labview_mcp/gen.py upstream/gen.py labview_mcp/gen.py
                    diff -u --label a/src/labview_mcp/lv.py --label b/src/labview_mcp/lv.py upstream/lv.py labview_mcp/lv.py; } > patches/0001-lv2024-fixes-and-fp-finish.diff
echo "wrote writer/patches/0001-lv2024-fixes-and-fp-finish.diff ($(grep -c '^@@' patches/0001-lv2024-fixes-and-fp-finish.diff) hunks)"
