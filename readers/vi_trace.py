"""Trace data flow along the wires of an on-site scan dump (read-only, local offline).

Both dump forms are accepted: dumps/<VI>.json pulled back by a job ({"ok", "result"}) and whole records from lab_dump_corpus ({"dump_scan": {"result"}}).

Usage:
    python readers/vi_trace.py <dump.json> nodes [substring]            # list nodes: id, category, label, type, enclosing structure
    python readers/vi_trace.py <dump.json> fwd <node id> [depth]      # where this node's outputs flow to
    python readers/vi_trace.py <dump.json> back <node id> [depth]     # where this node's inputs come from
    python readers/vi_trace.py <dump.json> unwired                 # unwired input terminals
--stop prefix1,prefix2 : stop going further at node ids with these prefixes (by default no stop).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def load(p) -> dict:
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:   # doesn't exist / can't be read / not JSON (a truncated pull-back also looks like this)
        raise SystemExit(f"{p}: can't be read ({type(e).__name__}: {str(e)[:120]})") from None
    r = None
    if isinstance(d, dict):
        for k in ("dump_scan", "dump_dfir"):
            if isinstance(d.get(k), dict) and d[k].get("ok", True) and "result" in d[k]:
                r = d[k]["result"]
                break
        else:
            if "result" in d:
                r = d["result"]
            elif "wires" in d and "nodes" in d:
                r = d
    if not isinstance(r, dict):
        raise SystemExit(f"{p}: unrecognized dump format ({'keys: ' + str(sorted(d)[:8]) if isinstance(d, dict) else 'top level is not an object'})")
    return r


def graph(r: dict):
    fwd, back = defaultdict(list), defaultdict(list)
    for w in r.get("wires", []):
        src = w["from"]
        for t in w["to"]:
            fwd[src.split(".")[0]].append((src, t))
            back[t.split(".")[0]].append((src, t))
    return fwd, back


def walk(r: dict, start: str, direction: str, depth: int = 6, stop: tuple = ()) -> list[str]:
    fwd, back = graph(r)
    g = fwd if direction == "fwd" else back
    seen, out = set(), []

    def rec(n, lvl):
        if lvl > depth or n in seen:
            return
        seen.add(n)
        for s, t in g.get(n, []):
            nxt = (t if direction == "fwd" else s).split(".")[0]
            out.append("  " * lvl + f"{s} -> {t}")
            if not any(nxt.startswith(x) for x in stop):   # stop here: show the wires going into it, don't expand it further
                rec(nxt, lvl + 1)

    rec(start, 0)
    return out


def nodes(r: dict, sub: str = "") -> list[str]:
    res = []
    for n in r.get("nodes", []):
        line = "\t".join(str(x) for x in (n.get("id"), n.get("kind") or n.get("class"), n.get("label") or n.get("function") or "",
                                          json.dumps(n.get("type"), ensure_ascii=False)[:40] if n.get("type") is not None else "",
                                          n.get("in", "")))
        if sub.lower() in line.lower():
            res.append(line)
    return res


_MAX_ARGS = {"nodes": 3, "fwd": 4, "back": 4, "unwired": 2}   # maximum number of positional arguments per subcommand (including the dump path and subcommand name)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    stop = ()
    if "--stop" in a:
        i = a.index("--stop")
        if i + 1 >= len(a) or a[i + 1].startswith("--"):   # missing value
            raise SystemExit("--stop must be followed by comma-separated node id prefixes, e.g. --stop add_,sel_")
        stop = tuple(x for x in a[i + 1].split(",") if x)
        del a[i:i + 2]
    if len(a) < 2:
        raise SystemExit(__doc__)
    unknown = [x for x in a if x.startswith("--")]   # misspelled options: not taken as substring / node id / file name
    if unknown:
        raise SystemExit(f"unknown options {unknown} (see usage)")
    cmd = a[1]
    if cmd not in _MAX_ARGS or len(a) > _MAX_ARGS[cmd]:   # extra arguments must not be silently dropped
        raise SystemExit(__doc__)
    r = load(a[0])
    if cmd == "nodes":
        out = nodes(r, a[2] if len(a) > 2 else "")
    elif cmd in ("fwd", "back"):
        if len(a) < 3:
            raise SystemExit(__doc__)
        ids = {n.get("id") for n in r.get("nodes", [])}
        if a[2] not in ids:
            raise SystemExit(f"no node {a[2]!r}; look up the id with the nodes subcommand first")
        if len(a) > 3 and not a[3].isdigit():
            raise SystemExit(f"depth must be a non-negative integer, not {a[3]!r}")
        out = walk(r, a[2], cmd, int(a[3]) if len(a) > 3 else 6, stop) or ["(no wires)"]
    elif cmd == "unwired":
        out = [json.dumps(x, ensure_ascii=False) for x in r.get("unwired_inputs", [])] or ["(none)"]
    else:
        raise SystemExit(__doc__)
    print("\n".join(out))


if __name__ == "__main__":
    main()
