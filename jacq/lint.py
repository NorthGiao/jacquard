"""Offline local check of a job spec (does not touch LabVIEW). Must run before going to the LabVIEW machine; jacq.run / scripts/vi_job.sh also run it first, and with any E it does not go to the LabVIEW machine.

Usage:
  python -m jacq.lint <job name | jobs/<job>.py path> [--offline] [--json]
    --offline  Use when LabVIEW cannot be operated: unverified function/terminal names are downgraded to W and automatically queued into catalog/probe_queue.json
               (obvious typos -- very similar to a known name -- are still E). On the LabVIEW machine the queue is probed in one go (scripts/probe_terms.sh --queue, or the same-machine steps in skill labview-lab-session).
    --json     Machine-readable output (used by the hook)

Each finding = level + code + description + "Fix". Levels: E must fix (exit code 1); W probably a problem; I hint.
The checks and their origins are in docs/pitfalls.md (each pitfall maps to one code; the "Symptom (on-site)" column in the table is what was observed on-site).
"""
from __future__ import annotations

import codecs
import json
import os
import re
import sys
import types
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path, PureWindowsPath

from . import catalog as C
from .config import GUARD, JOBS, REMOTE_BASE, REPO, WRITER_SRC

sys.path.insert(0, str(WRITER_SRC))
try:
    codecs.lookup("mbcs")
except LookupError:   # Linux has no mbcs; the writer only really uses it on Windows
    codecs.register(lambda n: codecs.lookup("gbk") if n == "mbcs" else None)
from labview_mcp import gen  # noqa: E402
from . import typecheck as TC  # noqa: E402
from . import wire_diff  # noqa: E402

PREDICTIVE = {"TYPE", "DIMS", "AUTOIDX", "REQ", "CASE-DUP", "CASE-DEFAULT", "CASE-VAL"}   # type-class codes that predict "will be broken" (downgraded under BUILD_ONLY; jacq.oracle compares against on-site)
COVER: dict = {}   # VI name → {"wires": number of wires, "typed": number of wires with an inferred type} (printed by main)
GRAPH_STYLES = {"Waveform Graph", "Waveform Chart", "XY Graph", "Intensity Graph", "Intensity Chart"}
FIXED_STRUCT_TERMS = {"for": {"N", "i"}, "while": {"i", "stop", "condition", "iteration"}}
LAB_CODEPAGE = "cp936"   # system code page of the LabVIEW machine (Simplified Chinese Windows). Labels are encoded with it via COM
_QUEUED: set = set()   # function names this lint_job run queued into the probe queue (at the end, used to clear entries this job queued earlier that are no longer used)


def _queue(fname, term, job, why):
    _QUEUED.add(fname)
    C.queue_add(fname, term, job, why)


@dataclass
class F:
    level: str      # E / W / I
    code: str       # see docs/pitfalls.md
    msg: str
    fix: str = ""
    vi: str = ""

    def __str__(self):
        s = f"{self.level} [{self.code}] {self.msg}"
        return s + (f"\n      Fix: {self.fix}" if self.fix else "")


# ---------- job loading ----------
class JobArgError(ValueError):
    """The job argument on the command line is wrong: the .py path is not directly under jobs/, or the job name is invalid."""


def job_name(name_or_path: str) -> str:
    """Command-line argument → job name: for something ending in .py take the file name, otherwise use as-is. A .py path with a directory must be directly under jobs/ (relative paths are resolved against the current directory or the repo root),
    otherwise `elsewhere/gallery.py` would silently be linted as jobs/gallery.py. A bare `x.py` with no directory is just the job name `x`; whether such a file exists in the current directory is not looked at.
    **However it was obtained, the returned name must match JOB_NAME** (letters, digits, underscore), otherwise JobArgError is raised: the record key (verified.json), the result directory,
    and the loaded file all use this name; if the name contains a dot or slash, the key and the loaded file don't match (`x.py.py` yields `x.py`; the bare name `old.x` would load jobs/old/x.py).
    jacq.run and jacq.job_check also use it; the first line of vi_job.sh copies the file-name half, and the directory and name halves are left to the lint pass it hands off to."""
    ends_py = name_or_path.endswith(".py")   # same test as vi_job.sh's case *.py) (Path.suffix also treats "zz.py/" as .py, bash doesn't)
    if ends_py:
        p = Path(name_or_path)
        if p.parent != Path(".") and JOBS.resolve() not in {p.parent.resolve(), (REPO / p.parent).resolve()}:
            raise JobArgError(f"{name_or_path!r} is not in {JOBS}: job files may only live directly under jobs/ (on the command line write the job name, or jobs/<job>.py)")
        name = p.stem
    else:
        name = name_or_path
    if not GUARD.JOB_NAME.fullmatch(name):
        raise JobArgError(f"{name_or_path!r}" + (f" yields job name {name!r}, which" if ends_py else "") +
                          " is invalid: a job name may only contain letters, digits, underscores (file names like x.py.py, or with spaces, dots or slashes are not allowed)")
    return name


def load_job(name_or_path: str):
    """Load jobs/<name>.py. `from lablib import BASE` in the job file is swapped for a fake lablib locally."""
    sys.modules.setdefault("lablib", types.SimpleNamespace(
        BASE=PureWindowsPath(os.environ.get("LVAGENT_BASE", r"D:\agent_sandbox\lv_agentic")),
        LV=None, timed=None, dump_json=None, say=None, main_guard=None, must_sandbox=None))
    name = job_name(name_or_path)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    return GUARD.load_job(name)   # the same loader as on the LabVIEW machine: load from source, re-execute every time, never use a possibly stale .pyc


def load_job_arg(arg: str):
    """Command-line argument → (job name, job module). Except jacq.lint itself (which turns errors into E), every command-line entry point that reads a job (job_check, oracle, wire_diff) goes through this one:
    the argument is normalized into a job name by job_name (jobs/<job>.py also works); which directory is read afterwards, and the key under which verified is recorded / revoked, all use this name, never the original string.
    A path outside jobs/, an invalid name, no such job file, or the file itself failing to execute (syntax error, runtime exception, an import inside it failing, sys.exit): say one sentence and return (None, None);
    the caller exits with code 2 on that; no verdict, no record written. Loading successfully but without VIS (empty file, only X = 1, jobs/__init__.py) also counts: every entry point that reads a job iterates over VIS."""
    try:
        name = job_name(arg)
    except JobArgError as e:
        print(f"==== {e}")
        return None, None
    try:
        job = load_job(name)
    except ModuleNotFoundError as e:
        if e.name != f"jobs.{name}":   # the job file imports a module that doesn't exist: this is not "no such job"
            print(f"==== jobs/{name}.py failed to execute ({type(e).__name__}: {str(e)[:200]}): no verdict. First run python -m jacq.lint {name} to see the full error")
        else:
            print(f"==== no job {name!r} (jobs/{name}.py cannot be loaded: {e})")
    except Exception as e:  # noqa: BLE001  job files are code written by an agent, any error is possible; lint will give an IMPORT E and the traceback
        print(f"==== jobs/{name}.py failed to execute ({type(e).__name__}: {str(e)[:200]}): no verdict. First run python -m jacq.lint {name} to see the full error")
    else:
        vis = getattr(job, "VIS", None)
        if isinstance(vis, list) and all(isinstance(v, dict) and "name" in v and "spec" in v for v in vis):
            return name, job
        print(f"==== jobs/{name}.py has no usable VIS (must be a list, each item with name and spec): no verdict. First run python -m jacq.lint {name} to see the full error")
    return None, None


def check_job_args(arg, rest):
    """[--force] [--only a,b] after the job (name or jobs/<job>.py): parsed by the same code as lab/vi_job.py, then checks that the names in --only are all in this job's VIS.
    Returns one sentence if there is a problem, None otherwise. jacq.run and scripts/vi_job.sh call it before going to the LabVIEW machine / syncing the writer -- when the LabVIEW machine rejects, the old report has already been deleted,
    and vi_job.sh has already synced the writer over SSH."""
    try:
        name = job_name(arg)
        _, _, only = GUARD.parse_job_args([name, *rest])
    except (JobArgError, SystemExit) as e:
        return str(e)
    if only:
        job = load_job_arg(arg)[1]   # when it can't be loaded, load_job_arg has already said its sentence
        if job is None:
            return "job cannot be loaded (reason above)"
        return GUARD.check_only(only, [v["name"] for v in job.VIS])
    return None


# ---------- single spec ----------

def _walk(d, chain, kind, cont, info, loops, dummy_vi):
    for key, k in (("controls", "ctl"), ("indicators", "ind"), ("constants", "const"), ("functions", "fn"),
                   ("property_nodes", "pn"), ("invoke_nodes", "pn")):
        for c in d.get(key, []):
            kind[c["name"]], cont[c["name"]], info[c["name"]] = k, chain, c
    for s in d.get("subvis", []):
        kind[s["name"]], cont[s["name"]], info[s["name"]] = "vi", chain, dict(s, _vi=PureWindowsPath(str(s.get("path", ""))).name)
        s["path"] = dummy_vi                       # the paths on the LabVIEW machine don't exist locally: generate a placeholder file for the check
    for st in d.get("structures", []):
        kind[st["name"]], cont[st["name"]], info[st["name"]] = st["type"].split("_")[0], chain, st
        loops[st["name"]] = st
        for r in st.get("shift_registers", []):
            kind[r], cont[r], info[r] = "sr", chain, {"loop": st["name"]}
        for t in st.get("output_tunnels", []):
            kind[t], cont[t], info[t] = "tun", chain, {"struct": st["name"]}
        _walk(st, chain + [st["name"]], kind, cont, info, loops, dummy_vi)
        for fr in st.get("cases", []) + st.get("events", []):
            _walk(fr, chain + [st["name"]], kind, cont, info, loops, dummy_vi)
            if fr.get("data") and fr.get("control"):
                info.setdefault("__frame_of", {})[fr["data"]] = fr["control"]
            for pw in fr.get("pass") or []:   # a case frame's "pass": [source, output tunnel] -- the writer adds an invisible wire (gen.py extra_wires)
                info.setdefault("__pass", []).append(tuple(pw))


def spec_io_types(spec):
    """Terminal types when used as a subVI: {"in": {label: T}, "out": {label: T}}"""
    lab = lambda c: c.get("label") or c["name"]
    return {"in": {lab(c): TC.parse(c.get("type", "dbl")) for c in spec.get("controls", [])},
            "out": {lab(c): TC.parse(c.get("type", "dbl")) for c in spec.get("indicators", [])}}


def lint_spec(spec, job_terms, dummy_vi, offline=False, job=None, vi=None, job_types=None) -> list[F]:
    out: list[F] = []
    add = lambda *a: out.append(F(*a, vi=vi or ""))
    sp = json.loads(json.dumps(spec))
    kind, cont, info, loops = {}, {}, {}, {}
    _walk(sp, [], kind, cont, info, loops, dummy_vi)
    frame_of = info.pop("__frame_of", {})
    pass_wires = info.pop("__pass", [])
    try:
        scr = gen.build(sp)
        # writer self-check: style/default written in the spec must appear in the generated result (when they don't land, on-site doesn't report an error, they're just silently dropped)
        for c in (sp.get("controls") or []) + (sp.get("indicators") or []):
            for key, got in (("style", getattr(scr, "styles", None)), ("default", getattr(scr, "defaults", None))):
                lab = str(c.get("label") or c["name"])
                lab = lab if lab.isascii() else str(c["name"])     # non-ASCII label: build with name first, then relabel
                if c.get(key) is not None and got is not None and lab not in got:
                    add("E", "WRITER", f"{c['name']}: the spec sets {key}, but it is missing from the writer's generated result (writer regression)",
                        "This is a writer bug, not your spec: report it to the maintainer (after changing writer/labview_mcp/gen.py run scripts/regen_patch.sh + pytest)")
    except gen.SpecError as e:
        m = str(e)
        fix = "Fix the spec according to the error (the writer's own validation; its hint usually already gives the correct form)"
        mm = re.search(r"no node named '([^']+)'", m)
        if mm:
            fix = f"Node name misspelled or not declared? Closest declared names: {C.close(mm.group(1), kind, 4, 0.4)}"
        elif "at least one" in m:
            fix = "Put at least one constant/function/subVI/structure on the block diagram (a control wired straight to an indicator alone is not enough)"
        elif "cannot be wired to" in m and "tunnel" in m:
            fix = ("A property node / By Name cannot be wired directly to a tunnel: put the property node inside each case frame, and in each frame wire its own constant to p.Value"
                   " (docs/spec_reference.md \"Updating an indicator live inside a loop\")")
        elif "names must be ASCII" in m:
            fix = "Use an ASCII identifier for name, and put the Chinese text in \"label\""
        add("E", "GEN", f"gen.build: {m}", fix)
        return out

    # front-panel labels must all be distinct: vi_job / vi_test set and read values by visible label, the writer finds control references by label, and a subVI's terminal names are its labels;
    # controls with non-ASCII labels are first built with name as a temporary label and only renamed at the end, so the temporary name must not collide with another label either
    owner = {}
    for c in (sp.get("controls") or []) + (sp.get("indicators") or []):
        lab = str(c.get("label") or c["name"])
        for key in sorted({lab} | (set() if lab.isascii() else {str(c["name"])})):
            if owner.setdefault(key, c["name"]) != c["name"]:
                add("E", "LABEL-DUP", f"the front-panel labels (or temporary labels while building the VI) of {owner[key]} and {c['name']} are both {key!r}",
                    "Every control / indicator label must be distinct; for a control with a Chinese label, its name must not equal another control's label either")
        try:
            lab.encode(LAB_CODEPAGE)
        except UnicodeEncodeError:
            add("W", "LABEL-CP", f"{c['name']}: label {lab!r} contains characters the LabVIEW machine's code page ({LAB_CODEPAGE}) cannot represent → on-site they become ??, and afterwards the control can't be found by label",
                "Use characters that exist in that code page (if the LabVIEW machine is not a Simplified Chinese system, change LAB_CODEPAGE in jacq/lint.py)")

    def end_chain(end, as_src):
        n, _, t = end.partition(".")
        k = kind.get(n)
        if k == "sr":
            lp = info[n]["loop"]
            inside = (t == "left") == as_src   # reading left = inside the loop; writing right = inside the loop
            return cont[n] + [lp] if inside else cont[n]
        if k in ("for", "while") and t:
            return cont[n] + [n] if t in ("i", "stop", "condition", "iteration") else cont[n]
        return cont.get(n, [])

    def typ(n):
        c = info.get(n, {})
        try:
            return gen.parse_type(c.get("type", "dbl")) if kind.get(n) in ("ctl", "ind", "const") else None
        except gen.SpecError:
            return None

    wires = sp.get("wires", [])
    tfind, tsrc, tstats = TC.check(sp, kind, cont, info, end_chain, job_types, frame_of)
    COVER[vi or ""] = tstats
    for lvl, code, msg, fix in tfind:
        add(lvl, code, msg, fix)
    sinks = Counter(w[1] for w in wires)
    for s, c in sinks.items():
        if c > 1 and kind.get(s) != "tun":     # a case structure output tunnel wired once per frame is correct
            add("E", "MULTI", f"{s} is driven by {c} wires", "An input terminal can only take one source; to merge use Select / a case structure / Build Array")
    used = {e.partition(".")[0] for w in wires for e in w} | set(_event_controls(sp))
    # property node {"control": X, "properties": ["Value"], "write": ...} writing Value = drives X (the standard way to update an indicator live inside a loop)
    pn_written = {info[n]["control"] for n, k in kind.items() if k == "pn" and info[n].get("control")
                  and "Value" in info[n].get("properties", []) and info[n].get("write")}
    used |= {info[n]["control"] for n, k in kind.items() if k == "pn" and info[n].get("control")}
    for n, k in kind.items():
        if k == "ind" and n not in sinks and n not in pn_written:
            add("W", "UNDRIVEN", f"indicator {n} is not driven", "Wire a source to it, or delete this indicator")
        if k in ("ctl", "const") and n not in used:
            add("W", "UNUSED", f"{k} {n} is not used", "Delete it, or wire it where it should be used")
        if k == "fn" and info[n]["function"] in C.banned():
            add("E", "BANNED", f"{n}: {info[n]['function']} —— {C.banned()[info[n]['function']]}",
                "Use the alternative given in the table (unwireable_by_name in catalog/terminals_confirmed.json)")
        if k in ("ctl", "ind") and info[n].get("style"):
            t = typ(n)
            if t is not None and t.kind == "array" and info[n]["style"] not in GRAPH_STYLES:
                add("E", "ARRSTYLE", f"{n}: an array as a whole cannot be changed to {info[n]['style']!r}",
                    f"Write \"element_style\": {info[n]['style']!r} (changes the style of the array elements), and don't set style on the array itself")

    learned_seen = set()
    interactive = any(k == "event" for k in kind.values())   # has an event structure = UI program: changing controls while running is the normal case
    ev_ctl = set(_event_controls(sp))   # controls registered by event frames: the writer moves their terminals into that frame (NI convention), so it doesn't count as "read once"
    # first find all "control outside the loop → While stop terminal" paths (direct / via functions, shift registers, tunnels; functions inside or outside the loop both count), then report wire by wire:
    # the result must not depend on the order in which wires are written. A control terminal is at top level; reaching the stop terminal along wires = that value is read only once.
    fwd_ends = {}
    for a, b in list(wires) + pass_wires:   # a case frame's pass is also a wire
        fwd_ends.setdefault(a.partition(".")[0], []).append(b)
    is_stop = lambda e: kind.get(e.partition(".")[0]) == "while" and e.partition(".")[2] in ("stop", "condition")
    in_event = lambda e: any(kind.get(x) == "event" for x in end_chain(e, False))
    stop_direct, stop_via, stop_seen, read_seen = set(), {}, set(), set()
    for na, k in kind.items():
        if k != "ctl" or na in ev_ctl:
            continue
        seen_n, todo = set(), []
        for e in fwd_ends.get(na, []):
            if in_event(e):          # a control only used in event frames: the writer moves its terminal into that frame, so it isn't read once
                continue
            if is_stop(e):
                stop_direct.add(na)
            elif kind.get(e.partition(".")[0]) in ("fn", "vi", "sr", "tun"):
                todo.append(e.partition(".")[0])
        while todo:
            n = todo.pop()
            if n in seen_n:
                continue
            seen_n.add(n)
            for e in fwd_ends.get(n, []):
                if is_stop(e):
                    stop_via.setdefault(na, e.partition(".")[0])
                elif kind.get(e.partition(".")[0]) in ("fn", "vi", "sr", "tun"):
                    todo.append(e.partition(".")[0])
    for na, w in stop_via.items():
        if na not in stop_direct and getattr(typ(na), "kind", None) == "bool":
            add("W", "STOP-ONCE?", f"{na} → … → {w}.stop: the boolean control is read only once before the loop starts, then reaches the stop terminal via functions / shift registers / tunnels → pressing it while running won't stop",
                f"To stop when it is pressed: put an event structure inside the While and add a {{\"control\": \"{na}\"}} frame, or read {na}'s Value with a property node inside the loop and use that in the computation; "
                "if the loop stops on its own by some other condition and reading this control only once before the start is what you want, ignore this finding")
    for a, b in wires:
        for end, as_src in ((a, True), (b, False)):
            n, _, t = end.partition(".")
            k = kind.get(n)
            if k == "fn":
                fname = info[n]["function"]
                if C.by_name(fname) and t not in ("input cluster", "output cluster"):
                    continue                       # By Name terminals = cluster element names, already checked by gen.build
                known, tier = C.known_terms(fname)
                if re.fullmatch(r"element\[\d+\]", t) and (fname in gen._GROWABLE_FUNCS or any(x.startswith("element") for x in known)):
                    continue                       # growable functions are wired by position: element[k] (gen.build already checked k ≤ size)
                if not tier:
                    if not C.valid_fn(fname):   # such a name in the probe queue would be spliced into a command sent over SSH to the LabVIEW machine; LabVIEW has no such function either
                        add("E", "FUNC?", f"{end}: function name {fname!r} is invalid (empty, or contains quotes, $, backticks, control characters)", "Write the English name from LabVIEW's Context Help; real names contain none of these characters")
                        continue
                    near = C.close(fname, C.all_functions(), 4, 0.6)
                    if near and offline and C.close(fname, C.all_functions(), 1, 0.9):
                        add("E", "FUNC?", f"{end}: unknown function {fname!r}, very similar to {near}", f"Most likely a typo: change it to {near[0]!r}")
                    elif offline:
                        rej = C.rejected().get(fname)
                        if not rej:
                            _queue(fname, t, job, "lint --offline")
                        add("W", "UNVERIFIED", f"{end}: the terminal names of function {fname!r} have not been verified"
                            + (f" (probed on-site {rej.get('failed', '?')} times without reading any terminals: most likely there is no such function, not queued again)" if rej else " (queued into the probe queue)"),
                            "Write the terminal names from LabVIEW's Context Help; probing the queue on-site will verify them, change them if a name is wrong"
                            + (f"; closest known function: {near}" if near else ""))
                    else:
                        add("E", "UNVERIFIED", f"{end}: the terminal names of function {fname!r} have not been verified",
                            f"probe {fname!r} on-site before building the VI (scripts/probe_terms.sh, or the same-machine steps in skill labview-lab-session)" + (f"; or perhaps you meant {near}" if near else ""))
                elif t in known and tier != "confirmed" and fname not in learned_seen:
                    learned_seen.add(fname)
                    add("I", "LEARNED", f"{fname!r}: terminal names come from offline dump pairing (tier {tier}), never wired by name in LabVIEW on-site",
                        "Usually trustworthy; if the first on-site round reports a wiring error, suspect this first")
                elif t not in known:
                    near = C.close(t, known, 3, 0.4)
                    if offline and tier != "confirmed" and not C.close(t, known, 1, 0.85):
                        _queue(fname, t, job, "lint --offline: the known terminal set may be incomplete")
                        add("W", "TERM?", f"{end}: the known terminals of {fname!r} don't include {t!r} (the known set is incomplete; queued into the probe queue)",
                            f"Known: {sorted(known)}")
                    else:
                        add("E", "TERM", f"{end}: {fname!r} has no terminal {t!r}",
                            (f"Closest: {near}; " if near else "") + f"All: {sorted(known)} (python -m jacq.catalog terms {fname!r})")
            elif k == "vi":
                base = PureWindowsPath(info[n]["path"]).name
                known = job_terms.get(base) or C.subvi_terms(base)
                if known is None:
                    add("E", "SUBVI?", f"{end}: the terminals of subVI {base} have not been verified",
                        "A subVI must either be a VI built earlier in this job (placed earlier in VIS), or be listed in subvis of catalog/terminals_confirmed.json")
                elif t not in known:
                    add("E", "TERM", f"{end}: subVI {base} has no terminal {t!r}",
                        f"subVI terminals = the labels of its front-panel controls (name when there is no label): {sorted(known)}")
                if not t.isascii():
                    add("E", "ZHSUB", f"{end}: subVI terminal name is non-ASCII -- the caller's wires will be silently dropped",
                        "Don't give Chinese labels to the controls of a VI used as a subVI (only top-level VIs use Chinese labels)")
        ca, cb = end_chain(a, True), end_chain(b, False)
        exits = [x for x in ca if x not in cb and kind[x] == "for"]
        enters = [x for x in cb if x not in ca and kind[x] == "for"]
        nb = b.partition(".")[0]
        na = a.partition(".")[0]
        if kind.get(nb) == "ind" and exits:
            td, ta = typ(nb), tsrc(a)
            dims_b = td.dims if td is not None and td.kind == "array" else 0
            if dims_b < len(exits):
                add("E", "AUTOIDX", f"{a} → {b}: leaving For loop {exits} auto-indexes into an array of ≥{len(exits)} dimensions, but {nb} has {dims_b} dimensions",
                    "For the last value: wire the source to the shift register's right first, then from the shift register (outside the loop) to the indicator; for every value: change the indicator to an array")
            elif ta is not None:
                da = ta.dims
                if da + len(exits) != dims_b:
                    add("E", "DIMS", f"{a}({da}-D) → {b}({dims_b}-D): dimensions don't match after leaving {len(exits)} levels of For loop",
                        f"The indicator should be {da + len(exits)}-D, or go through a shift register instead")
            else:
                add("W", "DIMS?", f"{a} → {b}: leaving For loop {exits}, {nb} is {dims_b}-D → the source must be {dims_b - len(exits)}-D",
                    "Check the source's dimensions; if unsure, use the shift register's right to take the last value")
        w_in = [x for x in cb if x not in ca and kind[x] == "while"]
        if kind.get(na) == "ctl" and w_in and not any(kind[x] == "event" for x in cb) and na not in ev_ctl:
            if nb in w_in and b.partition(".")[2] in ("stop", "condition"):
                if (na, nb) not in stop_seen:
                    stop_seen.add((na, nb))
                    add("E", "STOP-ONCE", f"{a} → {b}: the control terminal is outside the loop and is read only once before the loop starts → pressing the button won't stop it",
                        f"Put an event structure inside the While and add a {{\"control\": \"{na}\"}} frame (the writer puts [{na}, {nb}.stop] into that frame, "
                        "NI convention, see gallery w10_event_ui); or read Value with a property node inside the loop")
            elif na in stop_direct or na in read_seen or (na in stop_via and getattr(typ(na), "kind", None) == "bool"):
                pass   # STOP-ONCE / STOP-ONCE? already reported (or READ-ONCE was already reported for this control)
            else:
                read_seen.add(na)
                add("W" if interactive else "I", "READ-ONCE", f"{a} → {b}: the control terminal is outside While loop {w_in} and is read only once before the loop starts; changing it while running has no effect",
                    f"To read the latest value on every iteration: put a property node {{\"control\": \"{na}\", \"properties\": [\"Value\"]}} inside the loop"
                    " (without write), and wire p.Value; if reading it only once is what you want, ignore this finding")
        if enters and kind.get(na) in ("ctl", "const"):
            ta = typ(na)
            if ta is not None and ta.kind == "array" and kind.get(nb) != "sr":
                add("I", "AUTOIDX-IN", f"{a} → {b}: a {ta.dims}-D array goes straight into For loop {enters} = auto-indexing (element by element)",
                    "For the whole array: wire it to the initial value of the shift register's left first (outside the loop), take it from sr.left inside the loop, and pass sr.left → sr.right through")
    # two pitfalls of property nodes inside event frames (wired by the writer's second pass)
    in_ev = lambda n: any(kind.get(x) == "event" for x in cont.get(n, []))
    pns = [n for n, k in kind.items() if k == "pn"]
    for c in (sp.get("controls") or []) + (sp.get("indicators") or []):
        if not c.get("style"):
            continue
        refs = [n for n in pns if info[n].get("control") == c["name"]]
        if refs and all(in_ev(n) for n in refs):
            add("W", "STYLE-LINK", f"{c['name']}: has a style but is only referenced by property nodes inside event frames → the writer won't change the style first (lv.py only looks at top-level property nodes), the property node may lose its link",
                "Remove this control's style, or put another property node referencing it at top level")
    for a, b in wires:
        na_, nb_ = a.partition(".")[0], b.partition(".")[0]
        for pn_end, other in ((na_, b), (nb_, a)):
            if kind.get(pn_end) == "pn":
                on, _, ot = other.partition(".")
                if kind.get(on) == "fn" and re.fullmatch(r"element\[\d+\]", ot):
                    add("W", "LATE-ELEM", f"{a} → {b}: the property node's wire is drawn only in the writer's second pass, when {on}'s element[k] finds its terminal by position; not verified on-site",
                        f"Put a Select in between (s/t/f all wired to the same source, or to constants) before {on}.{ot}, so that element[k] is wired in the first pass")
    # case structure case values vs. selector type (LabVIEW: duplicate values, an integer/string selector without a default case, a value not in the enum → broken or build failure)
    # confirmed on-site are reported as E (typeprobe: tp33 i32 duplicate value, tp34 i32 no default, tp35 value outside the enum → broken; tp36 enum fully covered without default can be built);
    # the same kind of problems for string/bool selectors are not confirmed on-site → W
    for cn, st in loops.items():
        if st["type"] != "case":
            continue
        frs = st.get("cases", [])
        vals = [fr["value"] for fr in frs if fr.get("value") is not None]
        has_def = any(fr.get("default") for fr in frs)
        dup = sorted({repr(v) for v in vals if vals.count(v) > 1})
        sel = next((tsrc(a) for a, b in wires if b == f"{cn}.selector"), None)
        if dup:
            dnum = sel is not None and sel.cls == "num"
            add("E" if dnum else "W", "CASE-DUP" if dnum else "CASE-DUP?", f"case structure {cn}: duplicate case values {dup}", "Each case value may appear only once")
        if sel is None:
            continue
        # integer selectors may also use range/list strings: "1..3", "5, 7, 9.." (upstream case_multi passed on-site)
        rng = re.compile(r"^\s*-?\d*\s*(\.\.\s*-?\d*)?(\s*,\s*-?\d*\s*(\.\.\s*-?\d*)?)*\s*$")
        isnum = lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, str) and bool(rng.match(v)) and any(ch.isdigit() for ch in v))
        if sel.cls == "enum":
            items = list(sel.sub or ())
            bad = [v for v in vals if v not in items]
            if bad:
                add("E", "CASE-VAL", f"case structure {cn}: case values {bad} are not items of the selector enum {items}", f"Write enum item names as case values: {items}")
            elif not has_def and set(vals) != set(items):
                add("W", "CASE-DEFAULT?", f"case structure {cn}: enum items {sorted(set(items) - set(vals))} have no case, and there is no default case",
                    "Add cases for these items, or give one case \"default\": true")
        elif sel.cls == "bool":
            bad = [v for v in vals if not isinstance(v, bool)]
            if bad:
                add("W", "CASE-VAL?", f"case structure {cn}: the selector is bool, case values {bad} are not true/false", "Cases of a bool selector use \"value\": true / false")
            elif not has_def and set(vals) != {True, False}:
                add("W", "CASE-DEFAULT?", f"case structure {cn}: the bool selector only has the {vals} case, and no default case", "Add the other case, or add \"default\": true")
        elif sel.cls in ("num", "str", "err"):
            want = isnum if sel.cls == "num" else (lambda v: isinstance(v, str))
            bad = [v for v in vals if not want(v)] if sel.cls != "err" else []
            if bad:
                add("W", "CASE-VAL?", f"case structure {cn}: the selector is {sel}, case values {bad} have the wrong type",
                    "Integer selectors take integer case values; string selectors take string case values")
            if sel.cls in ("num", "str") and not has_def:
                add("E" if sel.cls == "num" else "W", "CASE-DEFAULT" if sel.cls == "num" else "CASE-DEFAULT?",
                    f"case structure {cn}: the values of a {sel} selector cannot be exhausted, there must be a default case",
                    "Give one case \"default\": true")
    for lp, st in loops.items():
        if st["type"].startswith("for") and not any(w[1] == f"{lp}.N" for w in wires):
            entering = [w for w in wires if lp in end_chain(w[1], False) and lp not in end_chain(w[0], True)
                        and kind.get(w[1].partition(".")[0]) != "sr"]
            if not entering:
                add("E", "FOR-N", f"For loop {lp}: N is not wired, and nothing enters the loop (the iteration count can't be determined)", f"Wire [\"<count>\", \"{lp}.N\"]")
            else:
                add("I", "FOR-N", f"For loop {lp}: N is not wired → the count is determined by the auto-indexed arrays ({len(entering)} wires enter the loop, at least one must be an array)")
        if st["type"].startswith("while") and not any(w[1] in (f"{lp}.stop", f"{lp}.condition") for w in wires):
            add("E", "WHILE-STOP", f"While loop {lp}: the stop terminal is not wired → it never stops once running (vi_run will time out)",
                f"Wire a boolean to \"{lp}.stop\" (true = stop)")
    return out


# ---------- job level ----------
def _names(spec, key):
    out = {}
    for c in spec.get(key, []):
        out[c["name"]] = c
    for st in spec.get("structures", []):   # controls are only at top level; this is just for robustness
        out.update(_names(st, key))
    return out


_NODE_KEYS = ("controls", "indicators", "constants", "functions", "subvis", "property_nodes", "invoke_nodes", "structures")
_T = lambda x: ("tuple (a tuple: jobs always use lists [...] by convention, change it to a list -- the spec's controls / functions / wires etc. get concatenated as lists, and a tuple crashes there)" if isinstance(x, tuple)
                else type(x).__name__)


def _shape_spec(d, where, errs):
    """The shape of the spec (where it must be list / dict / str). With a wrong shape the later checks would crash outright, so check first and stop if it fails."""
    if not isinstance(d, dict):
        errs.append((f"{where} must be a dict, but is {_T(d)}", "Write it like SPEC_SUB in jobs/_template.py"))
        return
    for key in _NODE_KEYS:
        xs = d.get(key)
        if xs is None:
            continue
        if not isinstance(xs, list):
            errs.append((f'{where}["{key}"] must be a list, but is {_T(xs)}', f'Write "{key}": [{{"name": ...}}, ...]'))
            continue
        for i, c in enumerate(xs):
            w = f'{where}["{key}"][{i}]'
            if not isinstance(c, dict):
                errs.append((f"{w} must be a dict, but is {_T(c)}", 'Each item is {"name": ..., ...}'))
                continue
            if not isinstance(c.get("name"), str) or not c["name"]:
                errs.append((f'{w} is missing "name" (a string)', "Every control / constant / function / subVI / structure / property node needs a unique name, wires use it"))
            if key == "subvis" and not isinstance(c.get("path"), str):
                errs.append((f'{w}\'s "path" ' + ("is missing" if c.get("path") is None else f"must be a string, but is {_T(c['path'])}"),
                             'Write "path": str(OUT / "sub.vi") (don\'t forget str()); for VIs in vi.lib write an absolute path string'))
            if key == "structures":
                if not isinstance(c.get("type"), str):
                    errs.append((f'{w} is missing "type"', '"type": "for_loop" / "while_loop" / "case" / "event"'))
                for fk in ("cases", "events"):
                    frs = c.get(fk)
                    if frs is None:
                        continue
                    if not isinstance(frs, list):
                        errs.append((f'{w}["{fk}"] must be a list, but is {_T(frs)}', f'"{fk}": [{{...}}, ...]'))
                        continue
                    for j, fr in enumerate(frs):
                        _shape_spec(fr, f'{w}["{fk}"][{j}]', errs)
                for lk in ("shift_registers", "output_tunnels"):
                    xs2 = c.get(lk)
                    if xs2 is not None and not (isinstance(xs2, list) and all(isinstance(x, str) and x for x in xs2)):
                        errs.append((f'{w}["{lk}"] must be a list of names', f'"{lk}": ["s"] (even one must be written as a list)'))
                _shape_spec(c, w, errs)
    if "wires" in d and not isinstance(d["wires"], list):
        errs.append((f'{where}["wires"] must be a list, but is {_T(d["wires"])}', '"wires": [["source", "sink"], ...] (omit this key when there are no wires)'))


def _shape_vi(v, errs):
    """The shape of tests / runs / runsnap."""
    tests = v.get("tests")
    if tests is not None and not isinstance(tests, list):
        errs.append((f"tests must be a list, but is {_T(tests)}", '"tests": [{"inputs": {...}, "expect": {...}}, ...]'))
    for i, c in enumerate(tests if isinstance(tests, list) else []):
        if not isinstance(c, dict):
            errs.append((f"tests[{i}] must be a dict, but is {_T(c)}", '{"inputs": {control name: value}, "expect": {indicator name: expected value}}'))
            continue
        for part in ("inputs", "expect"):
            if c.get(part) is not None and not isinstance(c[part], dict):
                errs.append((f"tests[{i}].{part} must be a dict, but is {_T(c[part])}", f'"{part}": {{name: value}}'))
    R = v.get("runs")
    if R is not None and not isinstance(R, dict):
        errs.append((f"runs must be a dict, but is {_T(R)}", '"runs": {"inputs": {...}, "outputs": [...], "check": "check_xxx"} (for multiple runs write "repeat": n)'))
    for part, typ_ in (("inputs", dict), ("outputs", list), ("actions", list), ("check", str)):
        if isinstance(R, dict) and R.get(part) is not None and not isinstance(R[part], typ_):
            errs.append((f"runs.{part} must be a {typ_.__name__}, but is {_T(R[part])}", "See docs/spec_reference.md \"Job file\""))
    for i, act in enumerate(R.get("actions") or [] if isinstance(R, dict) and isinstance(R.get("actions"), list) else []):
        how = 'One key per step: {"set": {control: value}} / {"click": button} / {"wait": seconds} / {"read": [indicator, ..]}'
        if not isinstance(act, dict) or len(act) != 1:
            errs.append((f"runs.actions[{i}] must be a dict with exactly one key, but is {act!r}", how))
            continue
        k, x = next(iter(act.items()))
        ok = {"set": lambda x: isinstance(x, dict), "click": lambda x: isinstance(x, str),
              "wait": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
              "read": lambda x: isinstance(x, list) and all(isinstance(n, str) for n in x)}.get(k)
        if ok is None or not ok(x):   # _act_to_labels on the LabVIEW machine converts labels for these shapes; a wrong shape would crash on-site
            errs.append((f"runs.actions[{i}] = {act!r}: " + (f"unknown action {k!r}" if ok is None else f"the value of {k} has the wrong shape ({_T(x)})"), how))
    if isinstance(R, dict) and R.get("repeat") is not None and (not isinstance(R["repeat"], int) or isinstance(R["repeat"], bool) or R["repeat"] < 1):
        errs.append((f"runs.repeat must be a positive integer, but is {R['repeat']!r}", '"repeat": 3'))
    S = v.get("runsnap")
    if S is not None and (not isinstance(S, dict) or not isinstance(S.get("inputs", {}), dict)):
        errs.append(("runsnap must be {\"inputs\": {...}}", '"runsnap": {"inputs": {control name: value}}'))


def lint_job(job, offline=False) -> list[F]:
    out: list[F] = []
    jobname = job.__name__.rsplit(".", 1)[-1]
    _QUEUED.clear()
    if not hasattr(job, "VIS") or not hasattr(job, "OUT"):
        return [F("E", "JOB", f"{jobname}: the job file must have OUT and VIS", "Write it like jobs/_template.py")]
    if not isinstance(job.VIS, list):
        return [F("E", "SHAPE", f"VIS must be a list, but is {_T(job.VIS)}", 'VIS = [{"name": "sub", "spec": SPEC_SUB, "tests": [...]}, ...] (like jobs/_template.py)')]
    shape_bad = set()
    for i, v in enumerate(job.VIS):
        errs = []
        if not isinstance(v, dict) or not isinstance(v.get("name"), str) or not v.get("name"):
            out.append(F("E", "SHAPE", f"VIS[{i}] must be a dict with \"name\" (a string)", '{"name": "main", "desc": "...", "spec": SPEC_MAIN, "tests": [...]}'))
            shape_bad.add(i)
            continue
        _shape_spec(v.get("spec"), "spec", errs)
        _shape_vi(v, errs)
        if not errs:
            try:
                json.dumps(v["spec"])
            except (TypeError, ValueError) as e:
                errs.append((f"the spec contains a value that is not dict / list / str / number / bool ({e})", 'Write paths as strings: str(OUT / "sub.vi")'))
        for m, fix in errs:
            out.append(F("E", "SHAPE", f"{v['name']}: {m}", fix, v["name"]))
        if errs:
            shape_bad.add(i)
    VIS = [v for i, v in enumerate(job.VIS) if i not in shape_bad]   # VIs with a wrong shape aren't checked further (would crash), the rest as usual
    for v in VIS:
        if GUARD.BAD_VI_NAME.search(v["name"]):   # the same regex as vi_job.py on the LabVIEW machine
            out.append(F("E", "JOB-NAME", f"VI name {v['name']!r} cannot be used as a file name (empty, contains \\ / : * ? \" < > | or control characters, starts or ends with a space / dot, is a Windows reserved name like CON / NUL / COM1, or is longer than 100 characters)",
                         "The VI name is the file name under OUT and cannot contain a path; to be safe use only letters, digits, underscores, hyphens", v["name"]))
        elif not v["name"].isascii():
            out.append(F("W", "JOB-NAME?", f"VI name {v['name']!r} contains non-ASCII characters: non-ASCII characters in file names and subVI paths have not been verified on-site",
                         "Use ASCII for VI names, put the Chinese text in desc and the front-panel labels", v["name"]))
    want_out = GUARD.job_out(REMOTE_BASE, jobname)   # vi_job.py on the LabVIEW machine only accepts this OUT (computed from the job name); here the same function and the same criterion are used
    if not GUARD.JOB_NAME.fullmatch(jobname):
        out.append(F("E", "JOB-NAME", f"job name {jobname!r} may only contain letters, digits, underscores: the LabVIEW machine uses it as a module name and output directory name, and refuses to run an invalid one",
                     "Rename jobs/<job>.py, e.g. replace hyphens with underscores (VI names may contain hyphens, job names may not)"))
    for vi, node, why in GUARD.scan_vis(VIS, want_out):
        out.append(F("E", "HW-IO", f"{node}: {why}",
                     "The job pipeline only builds and runs pure-computation VIs (bottom line: no hardware writes, never run existing VIs). For subVIs use VIs built earlier in this job or pure-computation VIs from vi.lib; "
                     "the acquisition / instrument-communication part is wired up by the user in LabVIEW (skill labview-vi-write \"Involving a DAQ card\")", vi))
    if GUARD.norm_path(job.OUT) != GUARD.norm_path(want_out):
        out.append(F("E", "JOB-OUT", f"OUT={job.OUT} is not {want_out}",
                     f'OUT = BASE / "write2" / "jobs" / "{jobname}" (only VIs under write2 may be run; pulling back / checking also look for this path)'))
    for k, m in GUARD.production_problems(job):   # the same check lab/production.py refuses to start on
        out.append(F("E", "PROD", f"PRODUCTION {k}: {m}",
                     'Steps are {"key": "calib", "vi": "<VI in VIS>", "inputs": {control: value or {"from": "<earlier key>", "out": "<indicator>"}}, "timeout_s": 3600} (lab/production.py docstring)'))
    dummy = REPO / "private" / ".lint_dummy.vi"
    dummy.parent.mkdir(exist_ok=True)
    dummy.write_bytes(b"")
    job_terms, job_types, seen = {}, {}, set()
    for v in VIS:   # when a VI of this job is used as a subVI, its terminals = its front-panel labels (name when there is no label)
        cs = v["spec"].get("controls", []) + v["spec"].get("indicators", [])
        job_terms[f"{v['name']}.vi"] = {c.get("label") or c["name"] for c in cs}
        job_types[f"{v['name']}.vi"] = spec_io_types(v["spec"])
    built = set()
    for v in VIS:
        n, spec = v["name"], v["spec"]
        if n in seen:
            out.append(F("E", "JOB-DUP", f"{n} is duplicated in VIS", "Each VI name must be unique", n))
        seen.add(n)
        # subVI dependencies must be built first
        for s in _subvis(spec):
            sp = PureWindowsPath(str(s.get("path", "")))
            dep = sp.stem
            if GUARD.norm_path(sp).startswith(GUARD.norm_path(want_out) + "\\") and (
                    GUARD.norm_path(sp.parent) != GUARD.norm_path(want_out) or f"{dep}.vi" not in job_terms):
                out.append(F("E", "JOB-SUBVI", f"{n}: subVI {sp} is in this job's directory but is not a VI in VIS -- the file doesn't exist on-site and can't be built",
                             'Write this job\'s subVIs as str(OUT / "<name in VIS>.vi") (the case of the name and extension must match VIS: the LabVIEW machine\'s cached dependencies are recognized by this name), and place them before the caller; other subVIs must be registered ones from vi.lib', n))
            if f"{dep}.vi" in job_terms and dep not in built:
                out.append(F("E", "JOB-ORDER", f"{n} calls {dep}.vi, but {dep} comes later in VIS", f"Move {dep} before {n}", n))
        built.add(n)
        fs = lint_spec(spec, job_terms, str(dummy), offline, jobname, n, job_types)
        out += fs
        if not any(f.level == "E" and f.code == "GEN" for f in fs):
            try:   # the on-site wire check (jacq.job_check) uses the same function; forms it doesn't recognize can't be checked, and job_check won't record verified
                wire_diff.spec_sig(spec)
            except Exception as e:  # noqa: BLE001
                out.append(F("W", "WIRE-UNSUP", f"{n}: the wire check doesn't support this spec's form ({type(e).__name__}: {e}) → even if it runs on-site it won't be recorded in verified.json",
                             "Forms like invoke_nodes aren't recognized by jacq/wire_diff.py yet: use another form, or ask the maintainer to add wire_diff support first (it needs an on-site dump as evidence)", n))
        ctl, ind = _names(spec, "controls"), _names(spec, "indicators")
        for i, c in enumerate(v.get("tests") or []):
            for k in (c.get("inputs") or {}):
                if k not in ctl:
                    out.append(F("E", "TEST-KEY", f"{n} tests[{i}].inputs: {k!r} is not a control name", f"Use the names in the spec: {sorted(ctl)}", n))
            if not c.get("expect"):
                out.append(F("E", "TEST-KEY", f"{n} tests[{i}] has no expect", "Write expect: {indicator name: expected value}", n))
            for k in (c.get("expect") or {}):
                if k not in ind:
                    out.append(F("E", "TEST-KEY", f"{n} tests[{i}].expect: {k!r} is not an indicator name", f"Use the names in the spec: {sorted(ind)}", n))
            for part in ("inputs", "expect"):   # r"D:\a" transcribed as "D:\\\\a": LabVIEW normalizes to a single backslash → not equal
                for k, x in (c.get(part) or {}).items():
                    if isinstance(x, str) and "\\\\" in x and not x.startswith("\\\\"):
                        out.append(F("W", "PATHESC", f"{n} tests[{i}].{part}[{k!r}] contains two consecutive backslashes: {x!r}",
                                     'Write paths as r"D:\\dir\\f.txt" (raw string) or "D:\\\\dir" (in Python that is a single backslash); LabVIEW normalizes paths to single backslashes', n))
        R = v.get("runs")
        if R:
            for k in (R.get("inputs") or {}):
                if k not in ctl:
                    out.append(F("E", "RUN-KEY", f"{n} runs.inputs: {k!r} is not a control name", f"{sorted(ctl)}", n))
            for k in (R.get("outputs") or []):
                if k not in ind and k not in ctl:
                    out.append(F("E", "RUN-KEY", f"{n} runs.outputs: {k!r} is not a control/indicator name", f"{sorted(ind)}", n))
            if not R.get("check") and not v.get("tests"):
                out.append(F("W", "RUN-NOCHECK", f"{n}'s runs has no check and there are no tests: on-site it passes as long as there is no error, nothing is asserted (verified.json records it as \"no assertion\")",
                             "Add \"check\": \"check_xxx\", a function returning {\"pass\": bool, ...}; if you only want to confirm it runs, ignore this finding", n))
            if R.get("check") and not callable(getattr(job, R["check"], None)):
                out.append(F("E", "RUN-CHECK", f"{n} runs.check={R['check']!r} not found in the job file", "Define def check_xxx(values) -> dict, returning a dict containing \"pass\": bool", n))
            if _has_while(spec) and not R.get("actions") and not R.get("timeout"):
                out.append(F("I", "RUN-WHILE", f"{n} has a While loop: runs gives no actions/timeout, if it doesn't stop it will wait the full default 120 s", "", n))
            for i, act in enumerate((R.get("actions") or [])):
                if not isinstance(act, dict) or len(act) != 1 or not set(act) <= {"set", "click", "wait", "read"}:
                    out.append(F("E", "ACT", f"{n} runs.actions[{i}]={act!r}",
                                 "One key per step: {\"set\": {control: value}} / {\"click\": button} / {\"wait\": seconds} / {\"read\": [indicator,..]}", n))
                    continue
                k, val = next(iter(act.items()))
                pool = {**ctl, **ind}
                labels = {(c.get("label") or c["name"]): c["name"] for c in pool.values()}
                names = [val] if k == "click" else (list(val) if k in ("set", "read") else [])
                for x in names:
                    if x not in pool and x not in labels:
                        out.append(F("E", "ACT", f"{n} runs.actions[{i}] {k}: {x!r} is not a control/indicator",
                                     f"Use the names in the spec (vi_job converts them to visible labels automatically): {sorted(pool)}", n))
                    elif k == "click" and (pool.get(x) or pool.get(labels.get(x), {})).get("type") != "bool":
                        out.append(F("E", "ACT", f"{n} runs.actions[{i}] click: {x!r} is not a boolean button", "click can only press boolean controls", n))
            if R.get("actions") and R["actions"][-1].get("click") is None and _has_while(spec):
                out.append(F("W", "ACT-STOP", f"{n} the last step of runs.actions is not a click: the While loop may not stop (will wait for the timeout)",
                             "Make the last step press the stop button, e.g. {\"click\": \"stop\"}", n))
        if not (v.get("tests") or v.get("runs")) and not getattr(job, "BUILD_ONLY", False):
            out.append(F("W", "NOTEST", f"{n} has no tests / runs / actions: on the LabVIEW machine it can only be confirmed to build and not be broken, not that it is correct",
                         "Give at least one tests entry (pure computation) or runs (+check / actions, when there are loops or a UI)", n))
    # type-probe batches (BUILD_ONLY, e.g. jobs/typeprobe.py): the VIs exist precisely to see whether they go broken, so type-class predictions are downgraded to I to let them go to the LabVIEW machine; name/structure E's are still blocked
    if getattr(job, "BUILD_ONLY", False):
        for f in out:
            if f.level == "E" and f.code in PREDICTIVE:
                f.level, f.msg = "I", f"[BUILD_ONLY] {f.msg}"
    # deliberately failing job (failtest): LINT_EXPECT = {VI name: [expected E codes]} → downgraded to I so it can go to the LabVIEW machine to rehearse on-site errors; not reporting them is itself an E
    expect = getattr(job, "LINT_EXPECT", {}) or {}
    if not (isinstance(expect, dict) and all(isinstance(c, (list, tuple, set)) for c in expect.values())):
        out.append(F("E", "SHAPE", f"LINT_EXPECT must be {{VI name: [code, ...]}}, but is {expect!r}", 'LINT_EXPECT = {"broken": ["DIMS"]} (see jobs/failtest.py)'))
        expect = {}
    for vname, codes in expect.items():
        for code in codes:
            if code not in PREDICTIVE:   # E's like terminal names, never stopping, hardware I/O may not be downgraded
                out.append(F("E", "EXPECT-BAD", f"{vname}: LINT_EXPECT cannot downgrade {code}",
                             f"Only type-class codes that predict \"will be broken\" may be downgraded for rehearsal: {sorted(PREDICTIVE)}; all other E's require fixing the spec", vname))
                continue
            hit = [f for f in out if f.vi == vname and f.level == "E" and f.code == code]
            if not hit:
                out.append(F("E", "EXPECT-MISS", f"{vname}: LINT_EXPECT says lint should report {code}, but it wasn't reported (lint regression)", "Check recent changes to jacq/lint.py", vname))
            for f in hit:
                f.level, f.msg = "I", f"[EXPECTED] {f.msg}"
    dummy.unlink(missing_ok=True)
    if offline:   # function names this job queued into the probe queue earlier that are no longer used: remove them (after fixing a misspelled/made-up name no garbage stays in the queue)
        C.queue_prune(jobname, _QUEUED)
    return out


def _subvis(d):
    yield from d.get("subvis", [])
    for st in d.get("structures", []):
        yield from _subvis(st)
        for fr in st.get("cases", []) + st.get("events", []):
            yield from _subvis(fr)


def _event_controls(d):
    """Controls registered by event structure cases ({"control": "go"}) also count as "used"."""
    for st in d.get("structures", []):
        for ev in st.get("events", []):
            if ev.get("control"):
                yield ev["control"]
        yield from _event_controls(st)
        for fr in st.get("cases", []) + st.get("events", []):
            yield from _event_controls(fr)


def _has_while(d):
    return any(st["type"].startswith("while") or _has_while(st) or
               any(_has_while(fr) for fr in st.get("cases", []) + st.get("events", []))
               for st in d.get("structures", []))


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    offline = "--offline" in a or os.environ.get("JACQ_OFFLINE") == "1"
    as_json = "--json" in a
    names = [x for x in a if not x.startswith("--")]
    unknown = [x for x in a if x.startswith("--") and x not in ("--offline", "--json")]
    if len(names) != 1 or unknown:   # extra job names or misspelled options must not be silently ignored: the agent would think both passed `lint a b`
        if names or unknown:
            print(f"==== wrong arguments: exactly one job is required, the only options are --offline / --json (got jobs {names}, unknown options {unknown}); not linted")
        print(__doc__); return 2
    try:
        job = load_job(names[0])
    except JobArgError as e:
        fs = [F("E", "JOB-ARG", str(e), "On the command line write the job name (letters, digits, underscores); a path can only be jobs/<job>.py, and job files must live directly under jobs/")]
    except Exception as e:   # the job file itself has a syntax/import error
        fs = [F("E", "IMPORT", f"failed to load the job: {type(e).__name__}: {e}", "First make python -c 'import jobs.<job>' pass")]
    else:
        try:
            fs = lint_job(job, offline)
        except Exception as e:  # noqa: BLE001  a spec form lint didn't anticipate: give a readable E, don't dump a traceback
            import traceback
            tb = traceback.extract_tb(e.__traceback__)[-1]
            fs = [F("E", "LINT-BUG", f"lint itself failed: {type(e).__name__}: {e} ({Path(tb.filename).name}:{tb.lineno})",
                    "Most likely the shape of something in the spec differs from jobs/_template.py / docs/spec_reference.md (something that should be a list is written as something else, a key is missing); "
                    "compare against them. If you can't find it, report this message and the job file to the maintainer -- lint should give a concrete fix rather than crash")]
    nE = sum(f.level == "E" for f in fs)
    if as_json:
        print(json.dumps([asdict(f) for f in fs], ensure_ascii=False, indent=1))
    else:
        vis_ = getattr(job, "VIS", []) if "job" in dir() else []
        by = {v["name"]: [] for v in vis_ if isinstance(v, dict) and isinstance(v.get("name"), str)} if isinstance(vis_, list) else {}
        for f in fs:
            by.setdefault(f.vi, []).append(f)
        for vi, xs in by.items():
            c = Counter(f.level for f in xs)
            cv = COVER.get(vi)
            print(f"== {vi or '(job)'}: {c['E']} E, {c['W']} W, {c['I']} I"
                  + (f" ({cv['wires']} wires, {cv['typed']} with inferred type)" if cv else ""))
            for f in xs:
                print("  " + str(f).replace("\n", "\n  "))
        nq = sum(f.code in ("UNVERIFIED", "TERM?") for f in fs)
        print(f"== Total {nE} E ({'E present, cannot go to the LabVIEW machine' if nE else 'lint passed; this does not mean the VI is correct, only a round on the LabVIEW machine counts'})"
              + (f"; {nq} unverified names queued into catalog/probe_queue.json (probe them on-site: scripts/probe_terms.sh --queue, or the same-machine steps in skill labview-lab-session)" if nq else ""))
    return 1 if nE else 0


if __name__ == "__main__":
    sys.exit(main())
