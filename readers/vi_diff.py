"""Compare the on-site scan dumps of two versions of the same VI (paired by LabVIEW object UID, read-only, local offline).

Usage: python readers/vi_diff.py <old dump.json> <new dump.json> [--json]
Both dumps must come from on-site (dumps/*.json pulled back by a job, or records from lab_dump_corpus); offline outlines have no UID wiring and can't compare wires.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from labview_mcp.diff import diff  # noqa: E402
from vi_trace import load  # noqa: E402

TITLES = {"added": "added nodes", "removed": "removed nodes", "changed": "changed", "wires_added": "added wires", "wires_removed": "removed wires"}


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in a
    a = [x for x in a if x != "--json"]
    unknown = [x for x in a if x.startswith("--")]
    if unknown:
        raise SystemExit(f"unknown options {unknown} (see usage)")
    if len(a) != 2:
        raise SystemExit(__doc__)
    d = diff(load(a[0]), load(a[1]))
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    body = {k: v for k, v in d.items() if k != "summary" and v}
    if not body:
        print("no difference between the block diagrams of the two versions (nodes, constant values, types, enclosing structures, wires all identical)")
        return
    for k, v in body.items():
        if v:
            print(f"[{TITLES.get(k, k)}] {len(v)}")
            print("\n".join("  " + x for x in v))


if __name__ == "__main__":
    main()
