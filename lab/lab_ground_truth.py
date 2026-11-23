"""Only read the "ground truth" of copies of existing VIs (these VIs are never run or saved):
- each front-panel control: UID, label, class, flattened type string + default data (for a VI never run, the current value is the default);
- each block-diagram constant: UID, flattened type + data (fills in the 36 that scan misses, also used to check the offline decoding);
- each Build Array: ConcatInputs (concatenate-inputs mode);
- broken VIs: MissingNames / MissingPaths of each node.
Properties are read with two reader VIs the agent created in the sandbox (gt_concat.vi / gt_missing.vi); only those are run.
Results in BASE\\gt\\<key>.json; resumable; hung ones recorded as hung. Usage: lab_ground_truth.py [--only substring] [--limit N]"""
import json, struct, sys
from pathlib import Path
from lablib import BASE, LV, timed, dump_json, say, main_guard
OUT = BASE / "gt"
H_CONCAT, H_MISS = BASE / "write2" / "gt_concat.vi", BASE / "write2" / "gt_missing.vi"

def make_helpers(lv):
    specs = {
        H_CONCAT: {"controls": [{"name": "r", "type": "ref:BuildArray"}], "indicators": [{"name": "c", "type": "bool"}, {"name": "e", "type": "error"}],
                   "property_nodes": [{"name": "p", "class": "BuildArray", "properties": ["ConcatInputs"]}],
                   "wires": [["r", "p.reference"], ["p.ConcatInputs", "c"], ["p.error out", "e"]]},
        H_MISS: {"controls": [{"name": "r", "type": "ref:GObject"}],
                 "indicators": [{"name": "names", "type": "string[]"}, {"name": "paths", "type": "path[]"}, {"name": "e", "type": "error"}],
                 "property_nodes": [{"name": "p", "class": "GObject", "properties": ["MissingNames", "MissingPaths"]}],
                 "wires": [["r", "p.reference"], ["p.MissingNames", "names"], ["p.MissingPaths", "paths"], ["p.error out", "e"]]},
    }
    for p, sp in specs.items():
        if not p.exists():
            c = timed(lv.create, p, sp, p.stem)
            say("helper", p.name, c["ok"], (c.get("result") or {}).get("exec_state"), c.get("error", "")[:300])

def lab_vis():
    out = []
    for f in sorted((BASE / "lab_dump").glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(r, dict) and (r.get("dump_scan") or {}).get("ok") and not r.get("dup_of"):
            st = ((r.get("info") or {}).get("result") or {}).get("exec_state", "")
            out.append((r["key"], r["path"], "bad" in str(st)))
    return out

def read_vi(L, path, broken):
    """Runs in the COM thread. L = shlim-t's LabVIEW object."""
    import labview_mcp.lv as M
    tgt = L._vi(path)
    res = {"fp": [], "const": [], "concat": [], "missing": [], "errors": []}
    def trav(target, cls):
        h = L._helper(M.UIDS_VI)
        h.SetControlValue("VI Refnum", tgt)
        h.SetControlValue("Traverse Target", target)
        h.SetControlValue("Class Name", cls)
        h.Run(False)
        return list(zip(h.GetControlValue("UIDs") or (), h.GetControlValue("Classes") or (),
                        h.GetControlValue("References") or (), h.GetControlValue("Names") or ()))
    def flat(h, ref):
        h.SetControlValue("reference", ref)
        h.Run(False)
        if h.GetControlValue("error out")[0]:
            return None, None
        ts = b"".join(struct.pack(">h", x) for x in h.GetControlValue("type string") or ())
        ds = bytes(h.GetControlValue("data bytes") or b"")
        return ts.hex(), (ds.hex() if len(ds) <= 262144 else f"<{len(ds)} bytes>")
    ft, ct = L._helper(M.CTLTYPE_VI), L._helper(M.CONSTTYPE_VI)
    for u, c, r, nm in trav(0, "Control"):
        t, d = flat(ft, r)
        res["fp"].append({"uid": u, "class": str(c), "label": str(nm), "flat": t, "data": d})
    for u, c, r, nm in trav(1, "Constant"):
        t, d = flat(ct, r)
        res["const"].append({"uid": u, "class": str(c), "label": str(nm), "flat": t, "data": d})
    ba = trav(1, "BuildArray")
    if ba:
        h = L._vi(str(H_CONCAT))
        for u, c, r, nm in ba:
            h.SetControlValue("r", r)
            h.Run(False)
            err = h.GetControlValue("e")
            res["concat"].append({"uid": u, "concat": bool(h.GetControlValue("c")), "err": int(err[1]) if err[0] else 0})
    if broken:
        h = L._vi(str(H_MISS))
        for u, c, r, nm in trav(1, "Node"):
            h.SetControlValue("r", r)
            h.Run(False)
            names = list(h.GetControlValue("names") or ())
            if names:
                res["missing"].append({"uid": u, "class": str(c), "names": [str(x) for x in names],
                                       "paths": [str(x) for x in h.GetControlValue("paths") or ()]})
    return res

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lv = LV()
    make_helpers(lv)
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10 ** 6
    hung_p, cur_p = OUT / "_hung.json", OUT / "_current.txt"
    hung = json.loads(hung_p.read_text(encoding="utf-8")) if hung_p.exists() else []
    if cur_p.exists():
        k = cur_p.read_text(encoding="utf-8")
        if k and k not in hung:
            hung.append(k); dump_json(hung_p, hung)
        cur_p.unlink()
    todo = [x for x in lab_vis() if (not only or only in x[0])][:limit]
    done = 0
    for key, path, broken in todo:
        fn = OUT / (key.replace("\\", "__") + ".json")
        if fn.exists() or key in hung:
            continue
        cur_p.write_text(key, encoding="utf-8")
        r = timed(lambda: lv.c(read_vi, lv.lv, path, broken, t=600))
        cur_p.unlink()
        dump_json(fn, {"key": key, "path": path, "broken": broken, **r})
        rr = r.get("result") or {}
        say(key[:70], "|", r["ok"], r.get("error", "")[:150], "| fp", len(rr.get("fp", [])), "const", len(rr.get("const", [])),
            "concat", len(rr.get("concat", [])), "missing", len(rr.get("missing", [])))
        done += 1
    say("hung:", hung)
    say("GT COMPLETE")
if __name__ == "__main__":
    main_guard(main)
