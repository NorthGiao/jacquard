"""spec wires vs. vi_dump(scan) wires: merge tunnels into nets, then compare as multisets of (source node type.terminal → sink node type.terminal).
(Called by jacq.job_check; only compares the multiset of wires: swaps between nodes of the same type can't be detected, see docs/lab_pending.md "To extend")

case / event frames (representation determined from on-site dumps):
- in the dump, case/event tunnels are all SelectorTunnel, with one inner endpoint `inner[frame k]` per frame;
  input tunnel = the outside is driven → the outside and the inside of each frame are merged into one net;
  output tunnel with only one frame wired (e.g. stop → W.stop inside an event frame, generated automatically by the writer) → merged likewise;
  several frames each wired once (the spec's output_tunnels) → treated as one node "tun", consistent on both sides.
- the case selector and the event timeout terminal are class "Tunnel" in the dump (only wired on the outside) → "case.selector" / "event.timeout".
- spec's {"event": "Timeout", "timeout": n}: the writer automatically places an i32 constant wired to the timeout terminal → this wire is added on the spec side."""
import json, re, sys
from collections import Counter, defaultdict

# terminals of growable functions are wired by position (spec writes element[k]; the dump reports the real name, e.g. string[k] of Concatenate Strings) → both sides compare positions
_POS = re.compile(r"^.+\[(\d+)\]$")
def _pos(t): m = _POS.match(t); return f"#{m.group(1)}" if m else t
# when the inputs of Build Array are arrays, the dump reports "array"/"element" (no position; only with two scalars is it element[1]/[2]) → position unknown "#?"
_NOPOS = {("Build Array", "array"), ("Build Array", "element")}

_SR = {("left", False): "SRL.init", ("left", True): "SRL.val", ("right", False): "SRR.in", ("right", True): "SRR.final"}


def spec_sig(spec):
    kind, label, extra = {}, {}, Counter()

    def walk(d):
        for c in d.get("controls", []) + d.get("indicators", []):
            label[c["name"]] = c.get("label") or c["name"]
        for c in d.get("controls", []): kind[c["name"]] = ("ctl", c["name"])
        for c in d.get("indicators", []): kind[c["name"]] = ("ind", c["name"])
        for c in d.get("constants", []): kind[c["name"]] = ("const", c["type"])
        for f in d.get("functions", []): kind[f["name"]] = ("fn", f["function"])
        for s in d.get("subvis", []): kind[s["name"]] = ("vi", s["path"].replace("\\", "/").split("/")[-1])
        for p in d.get("property_nodes", []): kind[p["name"]] = ("pn", p["control"])
        for st in d.get("structures", []):
            kind[st["name"]] = ("st", st["type"])
            for r in st.get("shift_registers", []): kind[r] = ("sr", None)
            for t in st.get("output_tunnels", []): kind[t] = ("otun", st["type"])
            walk(st)
            for fr in st.get("cases", []) + st.get("events", []):
                walk(fr)
                if fr.get("data"): kind[fr["data"]] = ("fn", "Event Data Node")
                if fr.get("event") == "Timeout": extra[("const:i32", "event.timeout")] += 1
    walk(spec)
    node = lambda end: end.partition(".")[0]

    def sig(end, as_src):
        n, _, t = end.partition(".")
        k, v = kind[n]
        if k in ("ctl", "ind"): return f"{k}:{label[v]}"
        if k == "const": return f"const:{v}"
        if k == "st":
            if v == "case": return "case.selector"
            if v == "event": return f"event.{t}"
            return f"loop.{t}"
        if k == "sr": return _SR[(t, as_src)]
        if k == "pn": return f"pn:{label.get(v, v)}.{t}"
        if k == "otun": return "tun"
        return f"{v}.{_pos(t)}"

    ins = defaultdict(list)
    for a, b in spec["wires"]:
        if kind[node(b)][0] == "otun": ins[b].append(a)
    one = lambda x: kind[node(x)][0] == "otun" and len(ins[x]) == 1   # an output tunnel with only one frame wired = pass-through
    out = Counter(extra)
    for a, b in spec["wires"]:
        if one(b):
            continue
        out[(sig(ins[a][0] if one(a) else a, True), sig(b, False))] += 1
    return out


def dump_sig(r):
    nodes = {n["id"]: n for n in r["nodes"]}
    parent = {}

    def f(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def u(a, b): parent[f(a)] = f(b)
    for w in r["wires"]:
        for t in w["to"]: u(w["from"], t)
    ends = {w["from"] for w in r["wires"]} | {t for w in r["wires"] for t in w["to"]}
    srcs = {w["from"] for w in r["wires"]}
    skind = lambda n: nodes.get(n.get("structure"), {}).get("kind")
    special = {}   # nid → signature (these tunnels are real endpoints, not merged)
    for nid, n in nodes.items():
        if n.get("kind") != "tunnel":
            continue
        if n.get("class") == "Tunnel":
            # scan reports For's N, case's selector and event's timeout terminal all as class "Tunnel" (real tunnels are LoopTunnel / SelectorTunnel)
            s = {"for_loop": "loop.N", "case": "case.selector", "event_structure": "event.timeout"}.get(skind(n))
            if s:
                special[nid] = s; continue
        inner = sorted(e for e in ends if e.startswith(f"{nid}.inner"))
        if n.get("class") == "SelectorTunnel" and f"{nid}.outer" in srcs and len(inner) > 1:
            special[nid] = "tun"; continue                # an output tunnel with several frames each wired once
        for e in inner or [f"{nid}.inner"]:
            u(f"{nid}.outer", e)
    is_tunnel = lambda e: nodes.get(e.partition(".")[0], {}).get("kind") == "tunnel" and e.partition(".")[0] not in special
    nets = {}
    for e in list(parent):
        nets.setdefault(f(e), []).append(e)

    def sig(end, as_src):
        nid, _, t = end.partition(".")
        n = nodes[nid]; k = n["kind"]
        if nid in special: return special[nid]
        if k == "control": return f"ctl:{n['label']}"
        if k == "indicator": return f"ind:{n['label']}"
        if k == "constant": return f"const:{n['type']}"
        if k == "function": return f"{n['function']}.{'#?' if (n['function'], t) in _NOPOS else _pos(t)}"
        if k == "subvi": return f"{n['vi']}.{t}"
        if k == "property_node": return f"pn:{n['control']}.{ {'Number of Rows': 'NumRows', 'Number of Columns': 'NumCols'}.get(t, t)}"
        if k in ("for_loop", "while_loop"): return f"loop.{t}"
        if k == "shift_register":
            left = n["class"].startswith("Left")
            return ("SRL." + ("val" if t == "inner" else "init")) if left else ("SRR." + ("final" if t == "outer" else "in"))
        return f"{k}:{end}"
    out, bad = Counter(), []
    for root, es in nets.items():
        real = [e for e in es if not is_tunnel(e)]
        if not real:
            continue                                     # unwired tunnel
        drv = [e for e in real if e in srcs]
        if not drv and all(special.get(e.partition(".")[0]) == "loop.N" for e in es):
            continue                                     # unwired N (iteration count determined by auto-indexing)
        if len(drv) != 1:
            bad.append(es); continue
        for e in real:
            if e != drv[0]: out[(sig(drv[0], True), sig(e, False))] += 1
    return out, bad


def diff(want, got):
    """[(key, spec count, dump count), ...] only mismatches are listed; a "#?" (position unknown) on the dump side can offset any "#k" of the same pair on the spec side."""
    want, got = Counter(want), Counter(got)
    for k in [k for k in got if k[1].endswith(".#?")]:
        stem = k[1][:-1]
        for w in sorted(w for w in want if w[0] == k[0] and w[1].startswith(stem) and w[1][len(stem):].isdigit()):
            m = min(want[w] - got[w], got[k]) if want[w] > got[w] else 0
            if m > 0:
                want[w] -= m; got[k] -= m
        got = +got; want = +want
    return [(k, want[k], got[k]) for k in sorted(set(want) | set(got)) if want[k] != got[k]]


if __name__ == "__main__":
    # usage: python -m jacq.wire_diff <job> <VI name>    (reads the spec in jobs/<job>.py and private/lab_corpus/.../dumps/<VI name>.json)
    from jacq.lint import load_job_arg
    from jacq.config import job_local_dir as D_OF
    if len(sys.argv) != 3:
        print("usage: python -m jacq.wire_diff <job> <VI name>")
        sys.exit(2)
    name, job = load_job_arg(sys.argv[1])   # job name or jobs/<job>.py; on error say one sentence
    if job is None:
        sys.exit(2)
    specs = {v["name"]: v["spec"] for v in job.VIS}
    if sys.argv[2] not in specs:
        print(f"==== {name} has no VI {sys.argv[2]!r} (VIS has: {sorted(specs)})")
        sys.exit(2)
    try:
        want = spec_sig(specs[sys.argv[2]])
    except (KeyError, AttributeError, TypeError, IndexError, ValueError) as e:   # spec isn't a dict / missing wires: lint will give SHAPE
        print(f"==== in {name}, the spec of VI {sys.argv[2]!r} has the wrong shape ({type(e).__name__}: {str(e)[:120]}): first run python -m jacq.lint {name} and look at SHAPE")
        sys.exit(2)
    dp = D_OF(name) / "dumps" / f"{sys.argv[2]}.json"
    try:
        got, bad = dump_sig(json.loads(dp.read_text(encoding="utf-8"))["result"])
    except (OSError, ValueError, KeyError, TypeError) as e:   # not run on the LabVIEW machine / incomplete pull-back / not a dump: say one sentence, no traceback
        print(f"==== can't read {dp} ({type(e).__name__}: {str(e)[:120]}): first run a round on the LabVIEW machine (remote: scripts/vi_job.sh {name}; same machine: python -m jacq.run {name})")
        sys.exit(2)
    print(f"spec wires {sum(want.values())}, dump net endpoint pairs {sum(got.values())}, nets with non-unique drivers {len(bad)}")
    for k, a, b in diff(want, got):
        print(f"  mismatch spec={a} dump={b}  {k[0]}  →  {k[1]}")
