"""Terminal-name catalog: lookup, fuzzy suggestions, probe queue. Shared by lint and humans/agents.

Tiers (from trusted to untrusted):
  confirmed  catalog/terminals_confirmed.json -- wired successfully on-site or read back by function_terminals (only written by probe_terms.sh / the collection script)
  learned    catalog/catalog_public.json      -- learned from vi_dump of probe VIs (names come from LabVIEW, but most were never wired by name)
  seen       private/catalog_learned.json     -- appeared in dumps of existing VIs (private, may not exist)

Usage:
  python -m jacq.catalog terms "Add"                 # full terminal set + tier; for a wrong name, gives the closest few
  python -m jacq.catalog search queue                # functions whose names contain queue
  python -m jacq.catalog queue add "Wait (ms)" [--term "milliseconds to wait"] [--job cld1] [--why "reason"]   # each option at most once, each needs a value
  python -m jacq.catalog queue list | queue --names
  python -m jacq.catalog queue remove "<function name>"     # remove an entry (queued by mistake, no longer used); entries probed twice on-site without reading anything are moved to rejected automatically
  ... | python -m jacq.catalog record                # used by probe_terms.sh: TERMS lines → confirmed, and removed from the queue
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from collections import defaultdict
from functools import lru_cache

from .config import CATALOG, PRIVATE

CONFIRMED = CATALOG / "terminals_confirmed.json"
PUBLIC = CATALOG / "catalog_public.json"
SEEN = PRIVATE / "catalog_learned.json"
QUEUE = CATALOG / "probe_queue.json"
BAD_FN = re.compile(r"""['"$`\x00-\x1f\x7f]""")   # characters not allowed in function names: scripts/probe_terms.sh --queue splices queued names into a PowerShell command sent over SSH to the LabVIEW machine, and these would break its quoting


def valid_fn(fn) -> bool:
    """Function names that may enter the queue / be recorded as confirmed: non-empty strings without the characters in BAD_FN (LabVIEW function names don't contain them either)."""
    return isinstance(fn, str) and bool(fn.strip()) and not BAD_FN.search(fn)


_NOT_FUNCTIONS = {"subvi", "property_node", "in_place"}   # node kinds in dumps, not function names (got mixed into the mapping table during pairing)


def _learned(path):
    T = defaultdict(set)
    if not path.exists():
        return T
    d = json.loads(path.read_text(encoding="utf-8"))
    fn = {k: max(v, key=v.get) for k, v in d["functions"].items()}
    fn = {k: v for k, v in fn.items() if v not in _NOT_FUNCTIONS}
    for k, v in d["terminals"].items():
        base = k.rsplit("|", 1)[0]
        if base in fn:
            T[fn[base]] |= set(v)
    return T


@lru_cache(maxsize=1)
def load():
    conf = json.loads(CONFIRMED.read_text(encoding="utf-8"))
    tiers = {"confirmed": {k: set(v) for k, v in conf["functions"].items()},
             "learned": dict(_learned(PUBLIC)), "seen": dict(_learned(SEEN))}
    return conf, tiers


def known_terms(fn: str) -> tuple[set, str | None]:
    """(all known terminal names of the function, most trusted tier); an unknown function returns (set(), None)."""
    _, tiers = load()
    out, best = set(), None
    for tier in ("confirmed", "learned", "seen"):
        if fn in tiers[tier]:
            out |= tiers[tier][fn]
            best = best or tier
    return out, best


def tier_of(fn: str, term: str) -> str | None:
    _, tiers = load()
    for tier in ("confirmed", "learned", "seen"):
        if term in tiers[tier].get(fn, ()):
            return tier
    return None


def all_functions() -> list[str]:
    _, tiers = load()
    return sorted(set().union(*[set(t) for t in tiers.values()]))


def subvi_terms(name: str):
    return set(load()[0].get("subvis", {}).get(name, [])) or None


def banned() -> dict:
    return load()[0].get("unwireable_by_name", {})


def by_name(fn: str):
    return load()[0].get("by_name", {}).get(fn)


def close(word: str, pool, n=3, cutoff=0.5) -> list[str]:
    pool = list(pool)
    low = {p.lower(): p for p in pool}
    hits = difflib.get_close_matches(word.lower(), list(low), n=n, cutoff=cutoff)
    sub = [p for p in pool if word.lower() in p.lower() and p not in hits]
    return [low[h] for h in hits] + sub[: max(0, n - len(hits))]


# ---------- probe queue ----------
def queue_load() -> dict:
    return json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {"_doc": "functions/terminals accumulated by offline lint, awaiting on-site verification; scripts/probe_terms.sh --queue probes them all in one go", "items": {}}


def queue_add(fn: str, term: str | None = None, job: str | None = None, why: str = "") -> bool:
    if not valid_fn(fn):
        raise ValueError(f"function name {fn!r} must not be empty and must not contain quotes, $, backticks or control characters (queued names get spliced into a command sent over SSH to the LabVIEW machine)")
    q = queue_load()
    new = fn not in q["items"]
    it = q["items"].setdefault(fn, {"terms": [], "jobs": [], "why": why})
    changed = new
    if term and term not in it["terms"]:
        it["terms"].append(term); changed = True
    if job and job not in it["jobs"]:
        it["jobs"].append(job); changed = True
    if changed:
        QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    return changed


def rejected() -> dict:
    """Function names probed twice on-site without reading any terminals (most likely nonexistent): {function name: {"failed": n, "last_error": ...}}"""
    return queue_load().get("rejected", {})


def queue_remove(fn: str) -> bool:
    q = queue_load()
    hit = q["items"].pop(fn, None) is not None
    hit = q.get("rejected", {}).pop(fn, None) is not None or hit
    if hit:
        QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    return hit


def queue_prune(job: str, keep) -> bool:
    """Offline lint wrap-up: for function names the job queued earlier but didn't use this time, remove this job from their jobs; if no other job needs the entry, delete it.
    Entries added manually with `queue add` (without --job) are unaffected."""
    q = queue_load()
    changed = False
    for fn in list(q["items"]):
        it = q["items"][fn]
        if job in it.get("jobs", []) and fn not in keep:
            it["jobs"].remove(job)
            changed = True
            if not it["jobs"]:
                del q["items"][fn]
    if changed:
        QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    return changed


MAX_PROBE_FAILS = 2


def _probe_line(line):
    """One line of probe_terms.sh output → (function name, ok, result, list of terminal names, error text); a wrong shape raises ValueError (record then records nothing from the whole batch).
    This line goes into terminals_confirmed.json: the function name must be valid, ok must be a boolean (the string "false" is truthy), terminals must be lists of strings ("xy" would be split into x and y)."""
    r = json.loads(line)
    if not isinstance(r, dict):
        raise ValueError("not a JSON object")
    fn, ok, res = r.get("function"), r.get("ok"), r.get("result") or {}
    if not valid_fn(fn) or not isinstance(ok, bool) or not isinstance(res, dict):
        raise ValueError(f"function / ok / result has the wrong shape (function={fn!r}, ok={ok!r})")

    def names(key):
        v = res.get(key)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise ValueError(f"result.{key} is not a list of strings")
        return v or []
    return fn, ok, res, names("terminals") or (names("inputs") + names("outputs")), str(r.get("error") or "")[:300]


def record(lines) -> None:
    conf = json.loads(CONFIRMED.read_text(encoding="utf-8"))
    q = queue_load()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        fn, ok, res, terms, err = _probe_line(line)
        if ok and terms:
            conf["functions"][fn] = sorted(set(conf["functions"].get(fn, [])) | set(terms))
            conf.setdefault("provenance", {}).setdefault(fn, []).append("function_terminals (probe_terms.sh)")
            want = set(q["items"].get(fn, {}).get("terms", []))
            miss = sorted(want - set(terms))
            print("recorded", fn, "inputs", res.get("inputs"), "outputs", res.get("outputs"),
                  f"; terminal names {miss} guessed in the queue don't exist, the related jobs must be changed" if miss else "")
            q["items"].pop(fn, None)
            q.get("rejected", {}).pop(fn, None)
        else:
            it = q["items"].get(fn)
            n = (it or {}).get("failed", 0) + 1
            print("failed", fn, f"(attempt {n})", err)
            if it is not None:   # names that can't be probed must not stay in the queue forever: on the MAX_PROBE_FAILS-th failure move to rejected, and lint will tell the jobs using it
                it["failed"], it["last_error"] = n, err
                if n >= MAX_PROBE_FAILS:
                    q.setdefault("rejected", {})[fn] = q["items"].pop(fn)
                    print(f"  {fn!r} moved to rejected (jobs using it: {it.get('jobs')}); if the function really exists, queue remove it and queue it again")
    CONFIRMED.write_text(json.dumps(conf, ensure_ascii=False, indent=1), encoding="utf-8")
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    load.cache_clear()


def _print_terms(fn):
    ts, tier = known_terms(fn)
    if not tier:
        print(f"unknown function {fn!r}. Closest: {close(fn, all_functions(), 6)}")
        print(f"If it really is a LabVIEW function: python -m jacq.catalog queue add {fn!r} (write it offline first, verify it when probing the queue on-site)")
        return 1
    print(f"{fn}  [{tier}]")
    for t in sorted(ts):
        print(f"  {t!r:40s} {tier_of(fn, t)}")
    if by_name(fn):
        print("  note:", by_name(fn))
    if fn in banned():
        print("  ✗ unusable:", banned()[fn])
    prov = load()[0].get("provenance", {}).get(fn)
    if prov:
        print("  source:", "; ".join(prov))
    if tier != "confirmed":
        print("  (not confirmed: the name comes from a LabVIEW dump but was never verified by wiring by name; it will be verified the first time it is used on-site)")
    return 0


def _queue_add_args(a):
    """Arguments after `queue add` → (function name, {term/job/why: value}); a wrong form returns None (the caller gives the usage and exit code 2).
    Only --term / --job / --why are accepted, each at most once and each with a value (not empty, not another option): this command writes catalog/probe_queue.json
    (scripts/probe_terms.sh --queue sends the names in it to the LabVIEW machine); with a missing value an option name like `--job` must not be written in as the value, and misspelled options must not be silently dropped either."""
    if not a or not a[0].strip() or a[0].startswith("--"):
        return None
    opt, i = {}, 1
    while i < len(a):
        k = a[i][2:]
        if not a[i].startswith("--") or k not in ("term", "job", "why") or k in opt \
                or i + 1 >= len(a) or not a[i + 1].strip() or a[i + 1].startswith("--"):
            return None
        opt[k] = a[i + 1]
        i += 2
    return a[0], opt


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a:
        print(__doc__); return 0
    cmd = a.pop(0)
    if cmd == "terms":
        if not a:
            print(__doc__); return 2
        return max(_print_terms(f) for f in a)
    if cmd == "search":
        w = " ".join(a).strip().lower()
        if not w:   # no word given / empty string / pure whitespace (the latter two would match everything and dump the whole catalog)
            print(__doc__); return 2
        fs = all_functions()
        hits = [f for f in fs if w in f.lower()]
        for f in hits:
            print(f, f"[{known_terms(f)[1]}]")
        by_term = [(f, t) for f in fs if f not in hits for t in sorted(known_terms(f)[0]) if w in t.lower()]
        for f, t in by_term:
            print(f, f"[{known_terms(f)[1]}]", f"(terminal {t!r} contains {w!r})")
        if not hits and not by_term:
            fz = close(w, fs, 5, 0.5)
            print("No name or terminal contains this word." + (f" Similar spellings (not necessarily related): {fz}" if fz else "")
                  + "\nNot in the catalog ≠ not in LabVIEW: write the English name from Context Help into the spec, lint --offline will queue it into the probe queue")
        return 0
    if cmd == "queue":
        if a in ([], ["list"]):
            print(json.dumps(queue_load()["items"], ensure_ascii=False, indent=1)); return 0
        if a == ["--names"]:
            print("\n".join(queue_load()["items"])); return 0
        if a[0] == "remove" and len(a) == 2 and a[1].strip():   # exactly one name (`remove a b` must not silently remove only a), not empty
            print("removed" if queue_remove(a[1]) else "not in the queue:", a[1]); return 0
        if a[0] == "add":
            parsed = _queue_add_args(a[1:])
            if parsed is None:
                print(__doc__); return 2
            fn, opt = parsed
            try:
                queue_add(fn, opt.get("term"), opt.get("job"), opt.get("why", ""))
            except ValueError as e:   # quotes / $ / backticks / control characters in the function name
                print(f"==== {e}\n==== not queued"); return 2
            print("queued", fn, opt); return 0
    if cmd == "record":
        if a:
            print(__doc__); return 2
        try:
            record(sys.stdin)
        except ValueError as e:   # error while reading in the loop (broken JSON, wrong shape): both files are only written after the loop ends, so neither has been touched yet
            print(f"==== the output of probe_terms.sh has unreadable lines ({type(e).__name__}: {str(e)[:100]}): nothing recorded, neither confirmed.json nor the queue was touched")
            return 2
        return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main())
