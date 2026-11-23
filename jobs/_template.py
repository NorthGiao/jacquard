"""job template: copy to jobs/<job>.py and replace <job> with the file name (job names may only contain letters, digits, underscores; lint JOB-NAME). Quick reference: docs/spec_reference.md; copy structure patterns from jobs/gallery.py.
Workflow: write/edit this file (the hook runs lint --offline automatically) → python -m jacq.lint <job> → scripts/vi_job.sh <job> (on-site)."""
from lablib import BASE

OUT = BASE / "write2" / "jobs" / "<job>"
P_SUB = str(OUT / "sub.vi")


def ref_sub(x):            # Python reference implementation: expected values of tests are computed by it, don't compute them by hand
    return x + 1


SPEC_SUB = {
    "controls": [{"name": "x", "type": "i32"}],           # subVI: don't use Chinese labels (the caller's wires get dropped)
    "indicators": [{"name": "y", "type": "i32"}],
    "functions": [{"name": "a", "function": "Increment"}],
    "wires": [["x", "a.x"], ["a.x+1", "y"]],
}

SPEC_MAIN = {
    "controls": [{"name": "x", "type": "i32", "label": "输入", "position": [20, 0]}],   # top-level VIs may use Chinese labels
    "indicators": [{"name": "y", "type": "i32", "label": "输出", "position": [20, 60]}],
    "subvis": [{"name": "s", "path": P_SUB}],
    "wires": [["x", "s.x"], ["s.y", "y"]],
}

VIS = [
    {"name": "sub", "desc": "y = x + 1", "spec": SPEC_SUB,
     "tests": [{"name": f"x={x}", "inputs": {"x": x}, "expect": {"y": ref_sub(x)}} for x in (0, 41, -5)]},
    {"name": "main", "desc": "calls sub", "spec": SPEC_MAIN,
     "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]},
]
