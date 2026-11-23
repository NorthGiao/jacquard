"""Read-only: list the properties of a VI Server class (as given by LabVIEW itself). Usage: class_props.py <class> [query]"""
import sys
from lablib import LV, say, main_guard, timed
def main():
    lv = LV()
    r = timed(lambda: lv.c(lv.lv.class_properties, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
    say(r["ok"], r.get("error", ""))
    for p in (r.get("result") or {}).get("properties", []):
        say("  P", p["name"], "|", p["long_name"])
    say("PROPS DONE")
if __name__ == "__main__":
    main_guard(main)
