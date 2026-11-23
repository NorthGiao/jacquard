"""Probe for adding new nodes to the catalog. For each function in probes.json:
  function_terminals → build a VI: each input terminal wired to a dbl control, each output terminal wired to a dbl indicator,
  control labels = "in:<terminal name>" / "out:<terminal name>" (wires break on type mismatch, but objects and uids remain, and scan can still read them)
  → save → vi_dump(scan, dfir) → result JSON. The VI files are kept and brought back to the local machine for offline pairing.
Then do the same save + dump for STRUCT_PROBES (structure samples).
Probe VIs are never run (things like One Button Dialog won't pop up even when built).
Usage: python lab_probe.py [funcs|structs|all] [--only substring]
"""
import json, re, sys
from pathlib import Path
from lablib import BASE, LV, Resume, timed, dump_json, say, main_guard

OUT = BASE / "probes"
HERE = Path(__file__).resolve().parent


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "x"


def func_spec(name, size, terms):
    ins = terms.get("inputs")
    outs = terms.get("outputs")
    if ins is None and outs is None:          # direction unknown: wire everything as inputs
        ins, outs = terms.get("terminals", []), []
    pos = {"Build Array": (lambda n: [f"element[{k}]" for k in range(1, n + 1)], lambda n: ["appended array"]),
           "Bundle": (lambda n: [f"element[{k}]" for k in range(1, n + 1)], lambda n: ["output cluster"]),
           "Unbundle": (lambda n: ["cluster"], lambda n: [f"element[{k}]" for k in range(1, n + 1)])}
    if name in pos and size:                  # growable terminals are named by position (function_terminals gives repeated/empty names)
        ins, outs = pos[name][0](size), pos[name][1](size)
    fn = {"name": "f", "function": name}
    if size:
        fn["size"] = size
    ctrls = [{"name": f"i{k}", "type": "dbl", "label": f"in:{t}", "connector": False} for k, t in enumerate(ins)]
    inds = [{"name": f"o{k}", "type": "dbl", "label": f"out:{t}", "connector": False} for k, t in enumerate(outs)]
    wires = [[f"i{k}", f"f.{t}"] for k, t in enumerate(ins)] + [[f"f.{t}", f"o{k}"] for k, t in enumerate(outs)]
    return {"controls": ctrls, "indicators": inds, "functions": [fn], "wires": wires}


def struct_probes():
    """Structure samples: all simple, readable specs; only save + dump, not run."""
    d = lambda n, t="dbl", **kw: {"name": n, "type": t, **kw}
    return {
        "for_index_tunnels": {"controls": [d("a", "dbl[]")], "indicators": [d("b", "dbl[]")],
            "constants": [{"name": "k", "type": "dbl", "value": 2}],
            "structures": [{"name": "L", "type": "for_loop", "functions": [{"name": "m", "function": "Multiply"}]}],
            "wires": [["a", "m.x"], ["k", "m.y"], ["m.x*y", "b"]]},
        "while_sr_stop": {"controls": [d("n", "i32")], "indicators": [d("cnt", "i32")],
            "structures": [{"name": "W", "type": "while_loop", "shift_registers": ["s"],
                            "functions": [{"name": "ge", "function": "Greater Or Equal?"},
                                          {"name": "inc", "function": "Increment"}]}],
            "constants": [{"name": "z", "type": "i32", "value": 0}],
            "wires": [["z", "s.left"], ["s.left", "inc.x"], ["inc.x+1", "s.right"], ["inc.x+1", "ge.x"],
                      ["n", "ge.y"], ["ge.x >= y?", "W.stop"], ["s.right", "cnt"]]},
        "case_bool": {"controls": [d("sel", "bool"), d("x")], "indicators": [d("y")],
            "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
                {"value": True, "functions": [{"name": "neg", "function": "Negate"}]},
                {"value": False, "pass": [["x", "t"]]}]}],
            "wires": [["sel", "C.selector"], ["x", "neg.x"], ["neg.-x", "t"], ["t", "y"]]},
        "case_enum": {"controls": [{"name": "st", "type": {"enum": ["Init", "Run", "Stop"]}}], "indicators": [d("v", "i32")],
            "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
                {"value": "Init", "constants": [{"name": "c0", "type": "i32", "value": 0}]},
                {"value": "Run", "constants": [{"name": "c1", "type": "i32", "value": 1}]},
                {"value": "Stop", "default": True, "constants": [{"name": "c2", "type": "i32", "value": 2}]}]}],
            "wires": [["st", "C.selector"], ["c0", "t"], ["c1", "t"], ["c2", "t"], ["t", "v"]]},
        "cluster_bundle": {"controls": [d("a"), d("b")],
            "indicators": [{"name": "cl", "type": {"cluster": [d("a"), d("b")]}}],
            "functions": [{"name": "bd", "function": "Bundle", "size": 2}],
            "wires": [["a", "bd.element[1]"], ["b", "bd.element[2]"], ["bd.output cluster", "cl"]]},
        "event_stop": {"controls": [d("stop", "bool", style="Stop Button")], "indicators": [],
            "structures": [{"name": "W", "type": "while_loop", "structures": [
                {"name": "E", "type": "event", "events": [{"control": "stop"}, {"event": "Timeout", "timeout": 100}]}]}],
            "wires": [["stop", "W.stop"]]},
        "queue_pc": {"controls": [d("x")], "indicators": [d("y")],
            "constants": [{"name": "typ", "type": "dbl", "value": 0}],
            "functions": [{"name": "oq", "function": "Obtain Queue"}, {"name": "eq", "function": "Enqueue Element"},
                          {"name": "dq", "function": "Dequeue Element"}, {"name": "rq", "function": "Release Queue"}],
            "wires": [["typ", "oq.element data type"], ["oq.queue out", "eq.queue"], ["x", "eq.element"],
                      ["eq.queue out", "dq.queue"], ["dq.element", "y"], ["dq.queue out", "rq.queue"]]},
        "property_node": {"controls": [d("x")], "indicators": [d("vis", "bool")],
            "property_nodes": [{"name": "p", "control": "x", "properties": ["Visible"]}],
            "wires": [["p.Visible", "vis"]]},
    }


def run_one(lv, rs, key, make_spec):
    vi = OUT / f"{key}.vi"
    res = OUT / f"{key}.json"
    if rs.skip(key, res):
        return
    rs.begin(key)
    r = {"key": key}
    spec = make_spec(r)
    if spec is not None:
        r["spec"] = spec
        r["create"] = timed(lv.create, vi, spec, f"probe {key}")
        if r["create"]["ok"] or vi.exists():   # VIs with broken wires are saved too
            r["dump_scan"] = timed(lv.dump, vi, "scan")
            r["dump_dfir"] = timed(lv.dump, vi, "dfir")
    rs.finish(res, r, r.get("dump_scan", {}).get("ok", False))
    c = r.get("create", {})
    say(f"{key}: create={'OK' if c.get('ok') else c.get('error', 'skip')[:120]}"
        f" state={(c.get('result') or {}).get('exec_state')} scan={'OK' if r.get('dump_scan', {}).get('ok') else '-'}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    lv, rs = LV(), Resume(OUT)
    if mode in ("funcs", "all"):
        probes = json.loads((HERE / "probes.json").read_text(encoding="utf-8"))
        for i, ent in enumerate(probes):
            name, size = (ent, None) if isinstance(ent, str) else (ent["name"], ent.get("size"))
            if only and only.lower() not in name.lower():
                continue
            key = f"f{i:03d}_{slug(name)}" + (f"_s{size}" if size else "")

            def mk(r, name=name, size=size):
                t = timed(lv.terminals, name, size)
                r["terminals"] = t
                if not t["ok"]:
                    r["search"] = timed(lv.functions, name)
                    return None
                return func_spec(name, size, t["result"])
            run_one(lv, rs, key, mk)
    if mode in ("structs", "all"):
        for k, spec in struct_probes().items():
            if only and only.lower() not in k:
                continue
            run_one(lv, rs, f"s_{k}", lambda r, spec=spec: spec)
    say(f"hung: {rs.hung}")
    say("PROBES COMPLETE")


if __name__ == "__main__":
    main_guard(main)
