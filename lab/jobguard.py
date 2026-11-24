"""Job hashing and hardware I/O scanning. Standard library only: lab/vi_job.py on the LabVIEW machine imports it directly, the local jacq loads the same file by path
(GUARD in jacq/config.py), so the hashes computed and the nodes blocked on both sides are guaranteed to be identical.

- spec_hash(v)        hash of one VI's spec + desc: shared by job_report, dump, jobs/verified.json (the algorithm must not change). The LabVIEW machine's cache (.spec_hash.json)
                      stores exactly this for VIs without subVIs from this job; for those with subVIs it stores the value assembled by vi_job.cache_key.
- writer_sha(pkg)     hash of the writer; together with job_sha in the report (the __job_sha__ attached by load_job), it ties the on-site report to "which job file, which writer version".
- file_sha(path)      hash of a file's bytes, same algorithm as __job_sha__; not called by production code, tests use it as an independent reference.
- load_job(name)      load jobs/<name>.py from source (no .pyc); __job_sha__ on the module is the hash of exactly the bytes exec'd (the file is read only once).
- JOB_NAME / BAD_VI_NAME / job_out(base, name)   the job-name regex, forbidden VI-name forms, the output directory computed from the job name
                      (shared by vi_job.py on the LabVIEW machine and lint's JOB-NAME / JOB-OUT / HW-IO).
- scan_vis(VIS, out)  things the job pipeline won't build or run (the code gate on the job path for the bottom line "no AO/DO writes, never run existing VIs"):
    subVIs     allowlist: only VIs under this job's output directory, or VIs registered in VILIB_ALLOW located under vi.lib of the LabVIEW installation directory.
    functions  denylist DENY_FUNC: nodes for hardware I/O, networking, external code, dynamically calling other VIs (by palette-name prefix).
    VI Server  property / invoke nodes using "class" (operating on VI or application references, such as the Run VI method of the VI class) are never allowed; those linked to front-panel controls ("control") are fine.
  The function item is a name-based denylist, not LabVIEW's authoritative classification: hardware functions not on the list slip through. The real backstop is the subVI allowlist + lint's UNVERIFIED
  (functions whose terminal names haven't been verified can't get into a job in strict mode). Requirements involving DAQ cards don't go through jobs: see skill labview-vi-write "Involving a DAQ card".
- decode_nonfinite(values, spec)   the writer's _jsonable hands non-finite numbers over as the strings "nan" / "inf" / "-inf" (upstream keeps
                      its results strict JSON); turn them back into floats for numeric controls / indicators, so checks and production see NaN, not str.
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


def load_job(name):
    """Load jobs.<name> from source (finding the file via sys.path as usual), neither reading nor writing .pyc in __pycache__.
    Whether a .pyc is stale is judged by "source mtime (whole seconds) + size": a job file changed within the same second with unchanged length (e.g. Increment changed to Decrement) would be taken as unchanged,
    the old code would actually run, while job_sha and spec_hash in the report are computed from the new file -- the report would then not match the VIs built. Raises ModuleNotFoundError if the file isn't found.
    m.__job_sha__ = sha256 of exactly the bytes exec'd (same algorithm as file_sha): job_sha in the report uses it, without reading the file again."""
    mod = f"jobs.{name}"
    sys.modules.pop(mod, None)   # otherwise find_spec directly returns the __spec__ of the last load (if the file was deleted it becomes FileNotFoundError instead of ModuleNotFoundError)
    importlib.invalidate_caches()   # directory listings are cached too (judged by directory mtime in whole seconds): a job file created within the same second would otherwise not be found
    spec = importlib.util.find_spec(mod)
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        raise ModuleNotFoundError(f"No module named {mod!r}", name=mod)
    src = Path(spec.origin).read_bytes()   # if reading fails, nothing has been put into sys.modules yet
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    m.__job_sha__ = hashlib.sha256(src).hexdigest()
    try:
        exec(compile(src, spec.origin, "exec"), m.__dict__)
    except SystemExit as e:   # sys.exit() while loading the job file: don't let the caller (lint / job_check / hook) quietly end with that exit code; treat it as an ordinary "execution error"
        sys.modules.pop(mod, None)
        raise RuntimeError(f"the job file called sys.exit({e.code!r}) while loading") from None
    except BaseException:
        sys.modules.pop(mod, None)
        raise
    return m


def spec_hash(v) -> str:
    return hashlib.sha256(json.dumps([v["spec"], v.get("desc", "")], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_sha(path):
    """sha256 of a file; None if it doesn't exist. Production code doesn't call it (job_sha in the report uses the __job_sha__ attached by load_job); tests use it as an independent reference."""
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def writer_sha(pkg):
    """Hash of all *.py in the writer package directory (.../labview_mcp) (sorted by file name, file name + content): the same scope that scripts/sync_writer.sh syncs,
    including dfir.py / catalog.py imported by lv.py at run time. Excludes vi_classes.json and the helper VIs in vis/ (sync_writer doesn't sync them either; if changed, deploy manually and --force).
    Returns None if the directory has no gen.py."""
    pkg = Path(pkg)
    if not (pkg / "gen.py").is_file():
        return None
    h = hashlib.sha256()
    for p in sorted(pkg.glob("*.py"), key=lambda p: p.name):   # sort by file-name string: Windows Path comparison is case-insensitive, Linux's isn't
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


# prefixes of function palette names. Must not be directly followed by a letter or digit ("Serial" doesn't match "Serialize", "CAN" doesn't match "Cancel"; localized names like "VISA写入" still match)
DENY_FUNC = re.compile(
    r"^\s*(?:VISA|GPIB|Serial|TCP|UDP|Bluetooth|IrDA|DAQmx|DAQ Assistant|Instrument I/O Assistant|IVI|Modbus|CAN|FPGA|In Port|Out Port"
    r"|DataSocket|Shared Variable|HTTP|SMTP|FTP|Open URL"                                            # networking
    r"|System Exec|Call Library Function|Code Interface|Python Node|Open Python Session|MATLAB|Automation Open|Constructor Node"   # external code
    r"|Open VI Reference|Call By Reference|Start Asynchronous Call|Open Application Reference"       # dynamically calling other VIs
    r")(?![A-Za-z0-9])", re.I)
# vi.lib is only recognized at the default install locations of LabVIEW / NI add-ons (any directory that happens to be called vi.lib doesn't count). Change this if LabVIEW is installed elsewhere.
VILIB_ROOT = re.compile(r"^[a-z]:\\program files(?: \(x86\))?\\(?:national instruments\\labview \d{4}|ni\\lvaddons\\[^\\]+\\\d+)\\vi\.lib\\")
# vi.lib VIs that jobs may call (relative path after vi.lib, lowercase). To add one: add a line here + register the terminals in subvis / vilib_paths of catalog/terminals_confirmed.json
# (tests/test_pipeline.py checks both sides agree; that file is hook-protected and no tool writes these two keys, the user changes them personally). Pure-computation VIs only.
VILIB_ALLOW = {r"analysis\1siggen.llb\uniform white noise.vi"}


# job name (module name of jobs/<job>.py): vi_job.py on the LabVIEW machine uses it to compute the output directory BASE\\write2\\jobs\\<job>, lint's JOB-NAME uses the same regex
JOB_NAME = re.compile(r"[A-Za-z0-9_]+")
# the VI name is the file name under the output directory: Windows-forbidden file-name characters (including path separators \ /, control characters), leading/trailing spaces or dots, empty names, reserved device names (CON / NUL / COM1...) are all disallowed --
# a "../x" with a path could build the VI outside this job's directory, the rest would fail to build on the LabVIEW machine.
# lint's JOB-NAME (the VI-name half) and the refusal of vi_job.py on the LabVIEW machine before starting use this same regex. Names longer than 100 characters are refused too:
# the total Windows path length (MAX_PATH 260) counts BASE and the job name together, beyond that the file can't be created; 100 is a rounded value with ample margin, not a limit measured on-site
BAD_VI_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]|^[ .]|[ .]$|^$|^.{101}|^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$', re.IGNORECASE)


def check_only(only, names):
    """The names in --only must all be VIs of this job: returns one sentence, or None if fine. Shared by vi_job.py (after loading the job) and jacq.lint.check_job_args (used by jacq.run and
    vi_job.sh before going to the LabVIEW machine / syncing the writer)."""
    miss = sorted(only - set(names))
    return f"--only has VIs that don't exist: {miss} (this job's VIS: {list(names)})" if miss else None


USAGE = "Usage: vi_job.py <job> [--force] [--only a,b]"


def parse_job_args(a):
    """Command-line parsing shared by `vi_job.py` / `jacq.run` / `scripts/vi_job.sh`: <job> [--force] [--only a,b] → (job name, force, set of only or None).
    No arguments, a first argument that is an option, unknown options, extra positional arguments, `--only` without a value, `--only` given twice all SystemExit with one sentence;
    callers must call it before any side effect (creating directories, deleting old reports, syncing the writer)."""
    if not a or a[0].startswith("--"):
        raise SystemExit(USAGE)
    name, force, only, i = a[0], False, None, 1
    while i < len(a):
        if a[i] == "--force":
            force = True
        elif a[i] == "--only":
            if i + 1 >= len(a) or a[i + 1].startswith("--"):
                raise SystemExit("--only must be followed by VI names (comma-separated), e.g. --only a,b")
            if only is not None:
                raise SystemExit("--only may only be given once, separate multiple VI names with commas: --only a,b")
            only = set(a[i + 1].split(","))
            bad = sorted(n for n in only if BAD_VI_NAME.search(n) or any(c in n for c in "'$`"))
            if bad:   # VI names that can get into VIS won't match BAD_VI_NAME; ' $ ` are refused separately: vi_job.sh splices the arguments into a remote PowerShell command over SSH, and these three would break its quoting
                raise SystemExit(f"{bad[0]!r} in --only is not a usable VI name (must not be empty, contain \\ / : * ? \" < > | ' $ ` or control characters, or start or end with a space or dot). Separate multiple VI names with commas: --only a,b")
            i += 1
        else:
            raise SystemExit(f"unknown argument {a[i]!r}. {USAGE}")
        i += 1
    return name, force, only


def job_out(base, name):
    """The job's output directory: determined only by BASE and the job name (the OUT written in the job file itself is not trusted)."""
    return base / "write2" / "jobs" / name


def norm_path(p) -> str:
    return str(p).replace("/", "\\").rstrip("\\").lower()


def _walk(d):
    yield d
    for st in d.get("structures") or []:
        if not isinstance(st, dict):
            continue
        yield from _walk(st)
        for fr in (st.get("cases") or []) + (st.get("events") or []):
            if isinstance(fr, dict):
                yield from _walk(fr)


def subvi_ok(path, out) -> bool:
    """subVI allowlist: under this job's output directory, or a registered vi.lib VI."""
    p = norm_path(path)
    if "\\..\\" in p or p.endswith("\\.."):
        return False
    if p.startswith(norm_path(out) + "\\"):
        return True
    m = VILIB_ROOT.match(p)
    return bool(m) and p[m.end():] in VILIB_ALLOW


def scan_spec(spec, out=None) -> list:
    """[(node name, description), ...]. out = the job's output directory (BASE\\write2\\jobs\\<job>); if not given, subVIs aren't checked (can't judge "this job's directory")."""
    bad = []
    for d in _walk(spec):
        for f in d.get("functions") or []:
            if isinstance(f, dict) and DENY_FUNC.match(str(f.get("function", ""))):
                bad.append((str(f.get("name")), f"function {f.get('function')!r} is a hardware I/O, networking, external code or dynamic call node"))
        for key in ("property_nodes", "invoke_nodes"):
            for n in d.get(key) or []:
                if isinstance(n, dict) and n.get("class"):
                    bad.append((str(n.get("name")), f"VI Server nodes in {key} using \"class\": {n.get('class')!r} (operating on VI / application references) don't go through jobs"))
        for s in d.get("subvis") or []:
            if out is not None and isinstance(s, dict) and not subvi_ok(s.get("path", ""), out):
                bad.append((str(s.get("name")), f"subVI {s.get('path')!r} is not under this job's directory and is not a registered vi.lib VI (VILIB_ALLOW in lab/jobguard.py)"))
    return bad


def scan_vis(VIS, out) -> list:
    """[(VI name, node name, description), ...]"""
    return [(v.get("name"), n, why) for v in VIS if isinstance(v, dict) and isinstance(v.get("spec"), dict)
            for n, why in scan_spec(v["spec"], out)]


NONFINITE = {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}
FLOAT_TYPES = {"dbl", "sgl", "double", "float", "float64", "single", "float32"}   # gen.parse_type's float names (a test keeps them in sync); only floats carry NaN


def _decode(v, t, dims=0):
    """v decoded along the spec type t (the forms gen.parse_type accepts); values that don't fit t are left as they are.
    dims: array levels to descend before reaching an element of t ({"array": t, "dims": n}, "dbl[][]")."""
    if dims:
        return [_decode(x, t, dims - 1) for x in v] if isinstance(v, list) else v
    if isinstance(t, dict):
        if "array" in t:
            d = t.get("dims", 1)
            return _decode(v, t["array"], d if isinstance(d, int) and not isinstance(d, bool) and d >= 1 else 1)
        if isinstance(t.get("cluster"), list) and isinstance(v, list) and len(v) == len(t["cluster"]):
            return [_decode(x, f.get("type") if isinstance(f, dict) else None) for x, f in zip(v, t["cluster"])]
        return v                                   # enum / flat / malformed: never numbers with NaN
    if not isinstance(t, str):
        return v
    s, n = t.strip().lower().replace(" ", ""), 0
    while s.endswith("[]"):
        s, n = s[:-2], n + 1
    if s not in FLOAT_TYPES:
        return v
    if n:
        return _decode(v, s, n)
    return NONFINITE.get(v, v) if isinstance(v, str) else v


def decode_nonfinite(values, spec):
    """{spec name: value} with "nan" / "inf" / "-inf" strings of numeric controls / indicators (by their spec type) turned back into floats.
    Names not in the spec (e.g. "_reads", "_elapsed") are left as they are."""
    types = {c.get("name"): c.get("type", "dbl") for c in (spec.get("controls") or []) + (spec.get("indicators") or [])
             if isinstance(c, dict)}
    return {k: (_decode(v, types[k]) if k in types else v) for k, v in values.items()}


def production_lock(out):
    """The lock <job out>/.production.lock, held for the life of the process (the OS releases it when the process dies, also on a reboot); None
    if another process holds it. Taken by lab/production.py for a whole run (fake or real) and by lab/vi_job.py for a round (fake or real: both
    write the VIs in <job out>), so a rebuild can't swap the VIs under a running production and two productions can't run the same steps.
    Give it back with release_lock (Windows wants the byte unlocked before the file is closed)."""
    import os
    Path(out).mkdir(parents=True, exist_ok=True)
    f = open(Path(out) / ".production.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def release_lock(f):
    import os
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    f.close()


STEP_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}")
_STEP_KEYS = {"key", "vi", "inputs", "timeout_s", "check", "allow_nan"}
_STEP_RESERVED = {"heartbeat"}   # heartbeat.json lives next to the <key>.json results


def step_refs(val):
    """An input of a production step that takes earlier results: {"from": "<step>" or ["<step>", ...], "out": "<indicator>"} → (steps, out); else None.
    With a list, the outputs of those steps are concatenated (each must be a list)."""
    if isinstance(val, dict) and set(val) == {"from", "out"}:
        src = val["from"] if isinstance(val["from"], list) else [val["from"]]
        return src, val["out"]
    return None


def production_problems(job) -> list:
    """[(step key, message), ...] for job.PRODUCTION (see lab/production.py); [] when the job has none or it is fine. Never raises on a malformed job.
    Shared by lint (PROD) and lab/production.py (refuses to start), so a typo is caught before an overnight run, not hours into it."""
    P = getattr(job, "PRODUCTION", None)
    if P is None:
        return []
    if not isinstance(P, list) or not P:
        return [("PRODUCTION", "must be a non-empty list of steps {\"key\", \"vi\", \"inputs\", ...}")]
    VIS = getattr(job, "VIS", None)
    vis = {v["name"]: v["spec"] for v in (VIS if isinstance(VIS, list) else [])
           if isinstance(v, dict) and isinstance(v.get("name"), str) and isinstance(v.get("spec"), dict)}

    def names(spec, kind):
        cs = spec.get(kind)
        return {c["name"] for c in cs if isinstance(c, dict) and isinstance(c.get("name"), str)} if isinstance(cs, list) else set()
    bad, seen, folded = [], {}, set()
    for i, s in enumerate(P):
        if not isinstance(s, dict):
            bad.append((f"PRODUCTION[{i}]", "each step is a dict"))
            continue
        k = s.get("key")
        if not isinstance(k, str) or not STEP_KEY.fullmatch(k) or k.startswith("_") or BAD_VI_NAME.search(k) \
                or k.casefold() in _STEP_RESERVED:
            bad.append((f"PRODUCTION[{i}]", f"key {k!r}: letters, digits, _ . - (a file name: not starting with _, not ending with ., "
                                            f"not a Windows device name like CON / NUL, not one of {sorted(_STEP_RESERVED)})"))
            continue
        if k.casefold() in folded:
            bad.append((k, "key is used twice (keys differing only in case are one file on Windows)"))
        folded.add(k.casefold())
        for x in sorted(map(str, set(s) - _STEP_KEYS)):
            bad.append((k, f"unknown field {x!r} (fields: {sorted(_STEP_KEYS)})"))
        vi = s.get("vi")
        if not isinstance(vi, str) or vi not in vis:
            bad.append((k, f"vi {vi!r} is not a VI of this job"))
            seen[k] = None
            continue
        ins = s.get("inputs", {})
        if not isinstance(ins, dict):
            bad.append((k, "inputs is a dict {control name: value or {\"from\": step, \"out\": indicator}}"))
            ins = {}
        ctl = names(vis[vi], "controls")
        unset = sorted(c["name"] for c in vis[vi].get("controls") or [] if isinstance(c, dict) and isinstance(c.get("name"), str)
                       and c.get("default") is None and c["name"] not in ins and not str(c.get("type", "")).strip().lower().startswith("error"))
        if unset:   # vi_run doesn't reset controls: an unset one would keep what an earlier step left in it, so results would depend on the run's history
            bad.append((k, f"controls {unset} of {vi} have no spec default: give them in inputs"))
        for n, val in ins.items():
            if n not in ctl:
                bad.append((k, f"input {n!r} is not a control of {vi} (spec names: {sorted(ctl)})"))
            r = step_refs(val)
            if r is None:
                if isinstance(val, dict) and ("from" in val or "out" in val):
                    bad.append((k, f"input {n!r}: a reference is exactly {{\"from\": step or [steps], \"out\": indicator}}"))
                try:
                    json.dumps(val, sort_keys=True)
                except (TypeError, ValueError):   # e.g. a numpy array: its str() is truncated, so the result record and the signature would be wrong
                    bad.append((k, f"input {n!r}: a plain JSON value (numbers, strings, lists, dicts; numpy: .tolist())"))
                continue
            src, out = r
            if not src or not all(isinstance(f, str) for f in src) or not isinstance(out, str):
                bad.append((k, f"input {n!r}: \"from\" is a step key or a non-empty list of them, \"out\" an indicator name"))
                continue
            for f in src:
                if f not in seen:
                    bad.append((k, f"input {n!r} takes from {f!r}, which is not an earlier step"))
                elif seen[f] is not None and out not in names(vis[seen[f]], "indicators"):
                    bad.append((k, f"input {n!r}: {out!r} is not an indicator of {seen[f]} (step {f!r})"))
        t = s.get("timeout_s", 3600)
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 < t < 1e8:
            bad.append((k, "timeout_s is a positive number of seconds"))
        if "allow_nan" in s and not isinstance(s["allow_nan"], bool):
            bad.append((k, "allow_nan is true / false"))
        if "check" in s and not (isinstance(s["check"], str) and callable(getattr(job, s["check"], None))):
            bad.append((k, f"check {s['check']!r} is not a function in the job file"))
        seen[k] = vi
    return bad
