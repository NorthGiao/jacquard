"""Common library on the LabVIEW machine (runs under Python 3.13 + pywin32 on the LabVIEW machine).
- All writes happen only under BASE (default D:\\agent_sandbox\\lv_agentic).
- Resumable: one result JSON per entry; current.txt is written before processing starts, and if the last run died on it (killed by the outer timeout),
  this run records it as hung and skips it.
- Dry run: with the environment variable LVAGENT_FAKE=1, FakeLV replaces LabVIEW, so the pipeline can be checked on Linux.
"""
import hashlib, json, os, sys, time, traceback
from pathlib import Path

FAKE = os.environ.get("LVAGENT_FAKE") == "1"
BASE = Path(os.environ.get("LVAGENT_BASE", r"D:\agent_sandbox\lv_agentic"))
SHLIM = Path(os.environ.get("LVAGENT_SHLIM", r"D:\agent_sandbox\shlim_pkg"))
SANDBOX_ROOT = Path(os.environ.get("LVAGENT_SANDBOX", r"D:\agent_sandbox"))


# only VIs in these directories may be run (all created by the agent); lab_copy (copies of existing VIs) and probes are never run
RUN_DIRS = ("write2", "save4ch", "acq")


def must_runnable(p):
    p = Path(p)
    if not under_sandbox(p):   # when LVAGENT_BASE is set outside the sandbox, RUN_DIRS don't count either
        raise SystemExit(f"Refusing to run: {p} is not inside the sandbox {SANDBOX_ROOT}")
    for d in RUN_DIRS:
        try:
            p.resolve().relative_to((BASE / d).resolve())
            return p
        except ValueError:
            pass
    raise SystemExit(f"Refusing to run: {p} is not inside the runnable directories {[str(BASE / d) for d in RUN_DIRS]}")


def under_sandbox(p) -> bool:
    try:
        Path(p).resolve().relative_to(SANDBOX_ROOT.resolve())
        return True
    except ValueError:
        return False


def must_sandbox(p):
    if not under_sandbox(p):
        raise SystemExit(f"Refused: {p} is not inside the sandbox {SANDBOX_ROOT}")
    return Path(p)


def writer_sha():
    """Hash of the writer used this round (jobguard.writer_sha: labview_mcp/*.py); the same function as writer_sha in the local jacq/config.py."""
    from jobguard import writer_sha as _ws
    return _ws(SHLIM / "labview_mcp")


def sha256(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def dump_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)


def say(*a):
    print(*a)
    sys.stdout.flush()


class Resume:
    """Resume entry by entry. done(key) tells whether a result exists already; begin/end maintain current.txt."""

    def __init__(self, outdir):
        self.out = must_sandbox(outdir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.cur = self.out / "current.txt"
        self.hung_file = self.out / "hung.json"
        self.hung = json.loads(self.hung_file.read_text(encoding="utf-8")) if self.hung_file.exists() else []
        if self.cur.exists():
            k = self.cur.read_text(encoding="utf-8").strip()
            if k and k not in self.hung:
                self.hung.append(k)
                dump_json(self.hung_file, self.hung)
                say(f"last run hung on {k}: recorded as hung, skipped this time")
            self.cur.unlink()

    def skip(self, key, result_path) -> bool:
        """Already recorded as hung, or already has a "done" result, or already failed MAX_TRIES attempts → skip."""
        if key in self.hung:
            return True
        r = self.prev(result_path)
        return bool(r) and (r.get("complete") or r.get("attempts", 0) >= self.MAX_TRIES)

    MAX_TRIES = 2

    @staticmethod
    def prev(result_path):
        p = Path(result_path)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def finish(self, result_path, r, complete):
        old = self.prev(result_path) or {}
        r["complete"] = bool(complete)
        r["attempts"] = old.get("attempts", 0) + (0 if complete else 1)
        dump_json(result_path, r)
        self.end()

    def begin(self, key):
        self.cur.write_text(key, encoding="utf-8")

    def end(self):
        if self.cur.exists():
            self.cur.unlink()


def timed(fn, *a):
    t0 = time.time()
    try:
        return {"ok": True, "result": fn(*a), "sec": round(time.time() - t0, 2)}
    except BaseException as e:  # noqa: BLE001 record every failure and keep going
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:1500]}",
                "sec": round(time.time() - t0, 2)}


# ---------------------------------------------------------------- LabVIEW backend
class LV:
    """Wraps shlim-t's LabVIEW object with synchronous calls."""

    def __init__(self):
        if FAKE:
            from concurrent.futures import ThreadPoolExecutor
            self.lv = FakeLV()
            pool = ThreadPoolExecutor(1)
            self._submit = lambda fn, *a: pool.submit(fn, *a)
        else:
            sys.path.insert(0, str(SHLIM))
            from labview_mcp.lv import LabVIEW  # noqa: E402
            self.lv = LabVIEW()
            self._submit = self.lv.call

    def c(self, fn, *a, t=120):
        """COM call. A timeout means LabVIEW is hung (most likely an invisible dialog): shlim-t's only COM thread
        is occupied, and all subsequent calls will time out as well. So exit the process immediately, keeping current.txt;
        the outer lab_run.sh stops this session-0 instance and reruns with a new instance, and the hung entry is recorded as hung."""
        from concurrent.futures import wait
        t = float(os.environ.get("LVAGENT_FAKE_TIMEOUT", t)) if FAKE else t
        fut, end = self._submit(fn, *a), time.monotonic() + t
        while not fut.done() and time.monotonic() < end:   # short waits: on Windows a long one can't be interrupted by Ctrl+C
            wait([fut], timeout=max(0.0, min(1.0, end - time.monotonic())))
        if fut.done():
            return fut.result()      # the call's own exception (a TimeoutError of its own too) is raised as it is
        say(f"COM call {getattr(fn, '__name__', fn)} exceeded {t}s: LabVIEW judged hung, exiting to switch instances")
        say("NEED RESTART")
        try:
            sys.stdout.flush()
        except Exception:                    # noqa: BLE001 no console (pythonw, a task without one): exit anyway
            pass
        os._exit(4)

    def status(self):                  return self.c(self.lv.status)
    def info(self, p):                 return self.c(self.lv.vi_info, str(p))
    def dump(self, p, method):         return self.c(self.lv.vi_dump, str(p), method, t=float(os.environ.get("LVAGENT_DUMP_TIMEOUT", 300)))
    def hierarchy(self, p, depth=1):   return self.c(self.lv.vi_hierarchy, str(p), depth, True)
    def functions(self, q):            return self.c(self.lv.lv_functions, q)
    def terminals(self, name, size=None): return self.c(self.lv.function_terminals, name, size)

    def create(self, p, spec, desc=""):
        must_sandbox(p)
        return self.c(self.lv.vi_create, str(p), spec, True, desc, t=300)

    def run(self, p, inputs, outputs=None, timeout=30.0, actions=None):
        must_runnable(p)  # only run VIs created by the agent
        return self.c(self.lv.vi_run, str(p), inputs, outputs, timeout, actions, t=timeout + 60)

    def test(self, p, cases, timeout=30.0, tol=1e-9):
        must_runnable(p)
        return self.c(self.lv.vi_test, str(p), cases, timeout, tol, t=timeout * max(1, len(cases)) + 60)


class FakeLV:
    """For dry runs: returns fake results with the same structure as shlim-t."""
    def status(self): return {"connected": True, "version": "24.1f0 (fake)", "app_dir": "C:/fake"}
    def _hang(self, p):
        if "hang" in str(p):
            time.sleep(3600)
    def vi_info(self, p): self._hang(p); return {"name": Path(p).name, "path": str(p), "saved_in_lv": "24.0", "exec_state": "idle", "callees": []}
    def vi_dump(self, p, m):
        if m == "dfir" and "broken" in str(p):
            raise RuntimeError("could not compile")
        return {"vi": p, "source": m, "nodes": [{"id": "add_1", "uid": 97, "kind": "function", "function": "Add"}], "wires": []}
    def vi_hierarchy(self, p, d, v): return {"root": p, "calls": {p: []}}
    def lv_functions(self, q): return {"query": q, "functions": [q]}
    def function_terminals(self, n, s=None): return {"function": n, "inputs": ["x", "y"], "outputs": ["x+y"], "terminals": ["x", "y", "x+y"]}
    def vi_create(self, p, spec, ov, d):
        Path(p).parent.mkdir(parents=True, exist_ok=True); Path(p).write_bytes(b"RSRC fake")
        return {"path": p, "exec_state": "idle"}
    def vi_run(self, p, i, o, t, a): return {"completed": True, "values": {**{n: 0.0 for n in o or []}, **dict(i)}}   # every requested output is read back
    def vi_test(self, p, cases, t, tol): return {"summary": f"{len(cases)}/{len(cases)} cases passed (fake)", "cases": []}


def session_info():
    """The current process's Windows session number, user, LabVIEW process list (tasklist, read-only)."""
    out = {"pid": os.getpid(), "user": os.environ.get("USERNAME"), "python": sys.version}
    if FAKE or os.name != "nt":
        return out
    import ctypes, subprocess, csv, io
    sid = ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    out["session_id"] = sid.value
    r = subprocess.run(["tasklist", "/FO", "CSV", "/FI", "IMAGENAME eq LabVIEW.exe"],
                       capture_output=True, text=True, encoding="mbcs", errors="replace")
    rows = list(csv.reader(io.StringIO(r.stdout)))
    out["labview_procs"] = [{"pid": int(x[1]), "session": int(x[3])} for x in rows[1:] if len(x) > 3 and x[1].isdigit()]
    return out


def daqmx_devices():
    """Read-only: list DAQmx devices, models, whether simulated, via nicaiu.dll. Creates no tasks."""
    if FAKE or os.name != "nt":
        return {"devices": [{"name": "SimDev", "product": "USB-6259 (fake)", "simulated": True}]}
    import ctypes
    try:
        d = ctypes.windll.LoadLibrary("nicaiu.dll")
    except OSError as e:
        return {"error": f"nicaiu.dll could not be loaded: {e}"}
    buf = ctypes.create_string_buffer(4096)
    rc = d.DAQmxGetSysDevNames(buf, 4096)
    if rc < 0:
        return {"error": f"DAQmxGetSysDevNames rc={rc}"}
    names = [n.strip() for n in buf.value.decode("mbcs").split(",") if n.strip()]
    devs = []
    for n in names:
        pt = ctypes.create_string_buffer(256); sim = ctypes.c_uint32()
        d.DAQmxGetDevProductType(n.encode(), pt, 256)
        d.DAQmxGetDevIsSimulated(n.encode(), ctypes.byref(sim))
        ai = ctypes.create_string_buffer(8192)
        d.DAQmxGetDevAIPhysicalChans(n.encode(), ai, 8192)
        devs.append({"name": n, "product": pt.value.decode("mbcs"), "simulated": bool(sim.value),
                     "ai": ai.value.decode("mbcs")[:300]})
    return {"devices": devs}


def main_guard(fn):
    try:
        fn()
        say("ALL DONE")
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
