"""Read the terminal names of functions (function_terminals), one TERMS <json> line per function. Usage: python probe_terms.py "Square Root" "Sine" ..."""
import json, sys
from lablib import LV, timed, say, main_guard


def main():
    lv = LV()
    for fn in sys.argv[1:]:
        r = timed(lv.terminals, fn)
        say("TERMS " + json.dumps({"function": fn, "ok": r["ok"], "result": r.get("result"), "error": r.get("error")},
                                  ensure_ascii=False, default=str))
    say("PROBE TERMS COMPLETE")


if __name__ == "__main__":
    main_guard(main)
