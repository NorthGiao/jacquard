"""Read-only: scan dump one sandbox VI, print its nodes and wires. Usage: dump_one.py <sandbox-relative path>"""
import sys, json
from lablib import BASE, LV, say, main_guard, timed
def main():
    lv = LV()
    for rel in sys.argv[1:]:
        d = timed(lv.dump, BASE / rel, "scan")
        r = d.get("result") or {}
        if not d.get("ok"): say("  ERR", str(d.get("error"))[:400])
        say("##", rel)
        for n in r.get("nodes", []):
            say("  N", n["id"], n.get("class"), n.get("in", ""))
        for w in r.get("wires", []):
            say("  W", w["from"], "->", w["to"])
        say("  unwired:", r.get("unwired_inputs"))
    say("DUMP1 DONE")
if __name__ == "__main__":
    main_guard(main)
