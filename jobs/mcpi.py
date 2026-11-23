"""job: Monte Carlo estimation of π (a sample to validate the new workflow). Each batch has n points (x,y)∈[0,1)², the count falling inside the unit circle is accumulated,
the estimate after batch k = 4·cumulative hits/cumulative points; the curve shows the estimate converging over batches."""
from lablib import BASE

OUT = BASE / "write2" / "jobs" / "mcpi"
UWN = r"C:\Program Files\National Instruments\LabVIEW 2024\vi.lib\Analysis\1siggen.llb\Uniform White Noise.vi"
P_RANDU, P_HITS, P_MAIN = (str(OUT / n) for n in ("randu.vi", "hits.vi", "mc_pi.vi"))

SPEC_RANDU = {
    "controls": [{"name": "n", "type": "i32", "default": 8}], "indicators": [{"name": "u", "type": "dbl[]"}],
    "constants": [{"name": "amp", "type": "dbl", "value": 0.5}, {"name": "half", "type": "dbl", "value": 0.5}],
    "subvis": [{"name": "nz", "path": UWN}], "functions": [{"name": "add", "function": "Add"}],
    "wires": [["n", "nz.samples"], ["amp", "nz.amplitude"], ["nz.uniform white noise", "add.x"], ["half", "add.y"], ["add.x+y", "u"]]}

SPEC_HITS = {  # number of points inside the unit circle: sum(x²+y² <= 1)
    "controls": [{"name": "x", "type": "dbl[]"}, {"name": "y", "type": "dbl[]"}],
    "indicators": [{"name": "hits", "type": "i32"}],
    "constants": [{"name": "one", "type": "dbl", "value": 1.0}],
    "functions": [{"name": "xx", "function": "Multiply"}, {"name": "yy", "function": "Multiply"}, {"name": "r2", "function": "Add"},
                  {"name": "inside", "function": "Less Or Equal?"}, {"name": "b2n", "function": "Boolean To (0,1)"},
                  {"name": "cnt", "function": "Add Array Elements"}],
    "wires": [["x", "xx.x"], ["x", "xx.y"], ["y", "yy.x"], ["y", "yy.y"], ["xx.x*y", "r2.x"], ["yy.x*y", "r2.y"],
              ["r2.x+y", "inside.x"], ["one", "inside.y"], ["inside.x <= y?", "b2n.Boolean"], ["b2n.0, 1", "cnt.numeric array"],
              ["cnt.sum", "hits"]]}

SPEC_MAIN = {
    "controls": [{"name": "n", "type": "i32", "default": 1000, "label": "每批点数", "position": [0, 0]},
                 {"name": "K", "type": "i32", "default": 200, "label": "批数", "position": [0, 60]}],
    "indicators": [{"name": "pi_est", "type": "dbl", "label": "π 估计", "position": [0, 130]},
                   {"name": "err", "type": "dbl", "label": "误差 |估计-π|", "position": [0, 190]},
                   {"name": "curve", "type": "dbl[]", "style": "Waveform Graph", "label": "π 估计 vs 批次", "position": [150, 0]}],
    "constants": [{"name": "z0", "type": "i32", "value": 0}, {"name": "pi", "type": "dbl", "value": 3.141592653589793}],
    "functions": [{"name": "d", "function": "Subtract"}, {"name": "ad", "function": "Absolute Value"}],
    "structures": [{"name": "B", "type": "for_loop", "shift_registers": ["acc", "est"],
                    "constants": [{"name": "four", "type": "dbl", "value": 4.0}],
                    "subvis": [{"name": "rx", "path": P_RANDU}, {"name": "ry", "path": P_RANDU}, {"name": "h", "path": P_HITS}],
                    "functions": [{"name": "tot", "function": "Add"}, {"name": "k1", "function": "Increment"},
                                  {"name": "npts", "function": "Multiply"}, {"name": "q", "function": "Divide"},
                                  {"name": "x4", "function": "Multiply"}]}],
    "wires": [["K", "B.N"], ["z0", "acc.left"], ["pi", "est.left"],
              ["n", "rx.n"], ["n", "ry.n"], ["rx.u", "h.x"], ["ry.u", "h.y"],
              ["acc.left", "tot.x"], ["h.hits", "tot.y"], ["tot.x+y", "acc.right"],
              ["B.i", "k1.x"], ["k1.x+1", "npts.x"], ["n", "npts.y"],
              ["tot.x+y", "q.x"], ["npts.x*y", "q.y"], ["q.x/y", "x4.x"], ["four", "x4.y"],
              ["x4.x*y", "est.right"], ["x4.x*y", "curve"],
              ["est.right", "pi_est"], ["est.right", "d.x"], ["pi", "d.y"], ["d.x-y", "ad.x"], ["ad.abs(x)", "err"]]}


def check_main(v):
    c = v["curve"]
    return {"pi_est": v["pi_est"], "err": v["err"], "len": len(c), "last_eq": abs(c[-1] - v["pi_est"]) < 1e-12,
            "pass": len(c) == 200 and abs(v["pi_est"] - 3.14159265) < 0.05 and abs(c[-1] - v["pi_est"]) < 1e-12}


VIS = [
    {"name": "randu", "spec": SPEC_RANDU, "desc": "n 个 [0,1) 均匀随机数"},
    {"name": "hits", "spec": SPEC_HITS, "desc": "落在单位圆内的点数",
     "tests": [{"name": "four points", "inputs": {"x": [0.0, 0.6, 0.8, 0.9], "y": [0.0, 0.8, 0.7, 0.9]}, "expect": {"hits": 2}},
               {"name": "boundary", "inputs": {"x": [1.0, 0.0], "y": [0.0, 1.0]}, "expect": {"hits": 2}},
               {"name": "empty", "inputs": {"x": [], "y": []}, "expect": {"hits": 0}}]},
    {"name": "mc_pi", "spec": SPEC_MAIN, "desc": "蒙特卡洛估计 π：曲线显示估计值随批次收敛",
     "runs": {"inputs": {"n": 1000, "K": 200}, "outputs": ["pi_est", "err", "curve"], "repeat": 2, "check": "check_main"},
     "runsnap": {"inputs": {"n": 1000, "K": 200}}},
]
