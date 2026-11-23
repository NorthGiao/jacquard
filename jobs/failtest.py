"""job: deliberately failing cases, to check whether vi_job's error reports are usable; plus a call test of a subVI with Chinese labels."""
from lablib import BASE

OUT = BASE / "write2" / "jobs" / "failtest"
P_ZH = str(OUT / "zh_sub.vi")

BROKEN = {  # 1-D indicator wired to an array source inside the loop → 2-D, broken on-site; type propagation infers the source is 1-D → E DIMS
    "controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32[]"}],
    "constants": [{"name": "one", "type": "i32", "value": 1}],
    "structures": [{"name": "L", "type": "for_loop", "constants": [{"name": "J", "type": "i32[]", "value": [0, 1, 2]}],
                    "shift_registers": ["s"], "functions": [{"name": "a", "function": "Add"}]}],
    "wires": [["one", "L.N"], ["x", "s.left"], ["s.left", "s.right"], ["J", "a.x"], ["s.left", "a.y"], ["a.x+y", "y"]]}

WRONG = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
         "functions": [{"name": "a", "function": "Increment"}], "wires": [["x", "a.x"], ["a.x+1", "y"]]}

ZH_SUB = {"controls": [{"name": "x", "type": "i32", "label": "输入"}], "indicators": [{"name": "y", "type": "i32", "label": "输出"}],
          "functions": [{"name": "a", "function": "Increment"}], "wires": [["x", "a.x"], ["a.x+1", "y"]]}
ZH_CALLER = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
             "subvis": [{"name": "s", "path": P_ZH}], "wires": [["x", "s.输入"], ["s.输出", "y"]]}

LINT_EXPECT = {"broken": ["DIMS"]}   # expected E (downgraded to I, so it can go to the LabVIEW machine to rehearse vi_job's broken error report)

VIS = [
    {"name": "broken", "spec": BROKEN},
    {"name": "wrong_expect", "spec": WRONG, "tests": [{"name": "deliberately wrong", "inputs": {"x": 1}, "expect": {"y": 3}}]},
    {"name": "zh_sub", "spec": ZH_SUB, "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]},
    # zh_caller: subVI terminal names are non-ASCII, wires get silently dropped -- now both the writer and lint reject it outright (see spec_lint_selftest)
]
