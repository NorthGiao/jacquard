"""Check against the answers: pair the online vi_dump (ground truth) with the BDHb.xml from offline pylabview extraction by uid.
Output: node/wire uid coverage, and the "(node class, primResID, terminal index) -> terminal name" entries learned from the pairing.
Usage: python pair_check.py <dump.json> <offline_dir>"""
import json, sys, xml.etree.ElementTree as ET
from pathlib import Path
import linkmap

def offline(d):
    root = ET.parse(next(Path(d).glob("*_BDHb.xml"))).getroot()
    owner, nodes, sigs = {}, {}, {}
    callee = linkmap.subvi_map(d)
    for el in root.iter("SL__arrayElement"):
        cls, uid, tl = el.get("class"), el.get("uid"), el.find("termList")
        if cls == "signal" and tl is not None:
            sigs[uid] = [e.get("uid") for e in tl.findall("SL__arrayElement")]
        elif tl is not None and cls not in (None, "term", "fPTerm"):
            rid = el.findtext("primResID")
            if rid is None and cls in ("iUse", "polyIUse"):   # subVI: use the called VI's name instead of primResID
                c = callee.get(uid) or {}
                nm = c.get("vi") or c.get("poly")   # polymorphic VIs use the actual instance (different instances have different connector panes)
                pub = (c.get("path") or [""])[0] == "<vilib>"   # vi.lib ones are NI's; the rest are subVIs written by the user
                rid = ("vi:" if pub else "user:") + nm if nm and not nm.isdigit() else "vi:?express"
            if cls not in ("iUse", "polyIUse"):      # terminal names of growable nodes (Build Array/Index Array/Bundle...) change with size: the key includes the terminal count
                rid = f"{rid}#{len(tl.findall('SL__arrayElement'))}"
            of = int(el.findtext("objFlags") or 0)   # checked against the true values read by LabVIEW (146/146, 184/184)
            if cls == "aBuild" and of & 0x10000:     # Build Array "Concatenate Inputs" mode
                rid += "+concat"
            if cls == "nMux":                        # bit16 = unbundle direction, bit19 = waveform (Get/Build Waveform Components)
                rid += ("+unb" if of & 0x10000 else "+bun") + ("+wfm" if of & 0x80000 else "")
            nodes[uid] = {"class": cls, "primResID": rid}
            for i, e in enumerate(tl.findall("SL__arrayElement")):
                owner.setdefault(e.get("uid"), (uid, i))
    return nodes, sigs, owner

def pair(dump, off_dir):
    """dump: vi_dump result dict; off_dir: offline extraction directory. Returns match statistics and the learned terminal entries."""
    nodes, sigs, owner = offline(off_dir)
    by_id = {n["id"]: n for n in dump["nodes"]}
    dn = {str(n["uid"]) for n in dump["nodes"] if "uid" in n}
    dw = {str(w["uid"]): w for w in dump["wires"] if "uid" in w}
    # nodes: any offline object with that uid counts as a hit (controls/constants don't necessarily have a termList)
    all_uids = set(nodes) | set(owner) | set(_all_uids(off_dir))
    learned = set()
    for wu, w in dw.items():
        terms = sigs.get(wu, [])
        for e in [w["from"]] + w["to"]:
            nid, _, tname = e.partition(".")
            n = by_id.get(nid)
            if not n or "uid" not in n or not tname:
                continue
            mine = [owner[t] for t in terms if owner.get(t) and owner[t][0] == str(n["uid"])]
            if len(mine) == 1:   # one wire connected to two terminals of the same node (x·x, x−x) can't tell which is which: don't learn
                o = mine[0]
                off = nodes[o[0]]
                learned.add((off["class"], off["primResID"], n.get("function") or n["kind"], o[1], tname))
    node_truth = sorted({(nodes[str(n["uid"])]["class"], nodes[str(n["uid"])]["primResID"], n.get("function") or n["kind"])
                         for n in dump["nodes"] if "uid" in n and str(n["uid"]) in nodes}, key=str)
    return {"node_truth": node_truth, "node_n": len(dn), "node_hit": len(dn & all_uids), "node_hit_with_terms": len(dn & set(nodes)),
            "wire_n": len(dw), "wire_hit": len(set(dw) & set(sigs)),
            "missing_nodes": sorted(n["id"] for n in dump["nodes"] if "uid" in n and str(n["uid"]) not in all_uids)[:30],
            "learned": sorted(learned, key=str)}


def _all_uids(d):
    root = ET.parse(next(Path(d).glob("*_BDHb.xml"))).getroot()
    return [el.get("uid") for el in root.iter() if el.get("uid")]


def main(dump_path, off_dir):
    st = pair(json.loads(Path(dump_path).read_text(encoding="utf-8")), off_dir)
    print(f"nodes: dump has uid {st['node_n']}, offline objects with the same uid {st['node_hit']} (of which with terminal lists {st['node_hit_with_terms']})")
    print(f"wires: dump {st['wire_n']}, offline hits {st['wire_hit']}; nodes not hit {st['missing_nodes']}")
    print("learned terminal entries (offline class, primResID, function name, offline terminal index, terminal name):")
    for x in st["learned"]:
        print("  ", x)


if __name__ == "__main__":
    main(*sys.argv[1:3])
