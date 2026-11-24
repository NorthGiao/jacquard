r"""Long production runs of a verified job on the LabVIEW machine: the steps in the job's PRODUCTION, one VI call each, unattended for hours or days.

    PRODUCTION = [
        {"key": "calib", "vi": "crds_calib", "inputs": {"n_per": 1000}, "timeout_s": 6 * 3600},
        {"key": "series", "vi": "crds_series", "inputs": {"tau0": {"from": "calib", "out": "tau0"}}, "check": "check_series"},
        {"key": "R0", "vi": "norm_opt", "inputs": {"y1": {"from": ["v060", "v070"], "out": "dtau"}}},   # list: outputs concatenated
    ]

Fields: key (file name of the result), vi (a VI of this job), inputs (spec names; the VI's spec defaults fill the rest, and a control without a default
must be given: controls aren't reset between runs), timeout_s (default 3600; the run is aborted after it), check (a job function given the values; must
return {"pass": bool, ...}; a failed check stops the run), allow_nan (take NaN / inf from earlier steps; by default a step stops instead of computing
on them). Split long figures into several steps (one threshold / one frequency each): an interruption only loses the step in progress.

Usage (same machine, from any directory; LVAGENT_BASE / LVAGENT_SANDBOX as for jacq.run; LVAGENT_SHLIM defaults to the repo's writer/):
    python lab/production.py <job> --plan          # the steps and their state, the last heartbeat, whether a run would be refused; changes nothing
    python lab/production.py <job>                 # run every unfinished step in order (rerun after an interruption: it resumes)
    python lab/production.py <job> --only a,b      # only these steps (their inputs from earlier steps must be done)

Refuses to start (exit 2) unless PRODUCTION is valid (lint PROD), every VI of the job is in jobs/verified.json with its current spec hash, the
current writer (and evidence beyond "build only" for the VIs the steps run; their subVIs ran inside those), the VIs on disk were built from those specs by that writer (.spec_hash.json), and nothing else of
this job is running (<job>\.production.lock, also taken by every jacq.run / vi_job round of the job, so a rebuild can't swap VIs under a production;
released by the OS when a process dies). The lock is per job: while a production runs, run no other job on that LabVIEW (one VI namespace in
memory: same-named VIs such as sub / main clash or link to the wrong one, and a dialog of the other round stalls the step). After a writer change every job must be verified again (jacq.run) before production.

Results in BASE\write2\jobs\<job>\production\ (production_fake\ for LVAGENT_FAKE=1 dry runs, never mixed with real ones):
<key>.json (values with NaN as float; Python JSON, NaN / Infinity are written as bare tokens), production.log (every line with time, including the
"NEED RESTART" line of a hung LabVIEW), heartbeat.json (rewritten every minute while running; nothing reads it for you: if its "now" is old and
"exited" is missing, the process is gone). A finished step is reused only while its VI as built (spec, subVIs, writer), the step as written
(timeout_s and check aside) and the results it takes from are unchanged and its check, as it is now, passes on the stored values; otherwise it is run again, and so is every step after it that
takes from it. A step that was running when the process died (reboot, closed window, Ctrl+C, LabVIEW hung) is run again, at most twice in all; a
LabVIEW that can't be reached when the run starts and "VI is already running" (a VI left running by a killed run: abort it in LabVIEW) don't count as attempts. Every
indicator must be read back, or the step fails.
Exit 0 = PRODUCTION COMPLETE, or with --only PRODUCTION ONLY DONE; 1 = stopped (the last line says why, also in production.log); 2 = refused;
4 = LabVIEW hung (the COM call outlived timeout_s + max(600 s, 5 %), so a dialog is only noticed then: restart LabVIEW, rerun).
Nothing restarts it by itself. The user may register a Windows scheduled task (their decision), e.g. every 10 minutes while logged on. Then: a start
while one runs exits 2; after a complete run each start only adds two log lines; a failed step is retried by the next start (the second failure
stops it for good), so Ctrl+C or closing the window doesn't stop production: disable the task to stop it. Two reboots during one step (Windows Update:
set active hours / pause updates) also stop it. The task has to run in the user's session ("Run only when user is logged on") for LabVIEW COM, set
LVAGENT_BASE / LVAGENT_SANDBOX itself if they are not the default, and have "Stop the task if it runs longer than" turned off (by default a task is
stopped after 3 days). Closing LabVIEW doesn't stop it either: a start opens it again over COM, and a COM disconnect during a step reruns that
VI call inside the step's remaining budget (likely exit 4 then). Not tried on-site yet: check the task in Task Scheduler after creating it.
"""
import copy
import hashlib
import json
import math
import os
import sys
import threading
import traceback
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent))  # jobs/: the repo's, unless PYTHONPATH names another (tests)
os.environ.setdefault("LVAGENT_SHLIM", str(HERE.parent / "writer"))   # the writer jacq.run builds with (same writer hash as the cache)

import jobguard                                                                  # noqa: E402
import lablib                                                                    # noqa: E402
from lablib import BASE, FAKE, LV, dump_json, must_runnable, writer_sha         # noqa: E402
from vi_job import cache_key, labels_of                                          # noqa: E402

USAGE = "Usage: production.py <job> [--plan] [--only a,b]"
BEAT_S = 60.0


class Log:
    def __init__(self, path):
        self.path = path

    def __call__(self, *a):
        line = " ".join(str(x) for x in a)
        try:                                 # a full disk must not kill a run that is otherwise fine
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
        except OSError:
            pass
        try:
            print(line, flush=True)
        except (OSError, UnicodeError):
            pass


def parse_args(a):
    """→ (job, plan, only), or one sentence for a usage error"""
    if not a or a[0].startswith("--"):
        return USAGE
    name, plan, only, i = a[0], False, None, 1
    while i < len(a):
        if a[i] == "--plan":
            plan = True
        elif a[i] == "--only" and only is None and i + 1 < len(a) and not a[i + 1].startswith("--"):
            only = {x for x in a[i + 1].split(",") if x}
            if not only:
                return "--only needs step keys, e.g. --only a,b"
            i += 1
        else:
            return f"{a[i]!r}: {USAGE}"
        i += 1
    if not jobguard.JOB_NAME.fullmatch(name):
        return f"job name {name!r} may only contain letters, digits, underscores"
    return name, plan, only


def bad_values(v):
    """missing, NaN or inf somewhere (also a "nan" / "inf" string: types decode_nonfinite doesn't know, e.g. {"flat": ...}, keep the writer's
    string): computing on it would silently poison the step. An empty list is fine. A string indicator that really says "nan": allow_nan."""
    if v is None:
        return True
    if isinstance(v, float):
        return not math.isfinite(v)
    if isinstance(v, str):
        return v in jobguard.NONFINITE
    if isinstance(v, list):
        return any(bad_values(x) for x in v)
    return False


def load(p):
    """a JSON object from p, or None (missing, unreadable, not an object)"""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def not_ready(job, name, out):
    """why the VIs can't be used for production, or None: every VI verified with its current spec by the current writer (those the steps run:
    not build-only), and the files on disk built from that spec by that writer"""
    rec = (load(Path(job.__file__).parent / "verified.json") or {}).get(name) or {}   # jobs/verified.json next to the job file
    bad = [v["name"] for v in job.VIS if (rec.get(v["name"]) or {}).get("spec_hash") != jobguard.spec_hash(v)]
    if bad:
        return f"not verified with the current spec: {bad} (run python -m jacq.run {name} until it is recorded)"
    stepped = {s["vi"] for s in job.PRODUCTION}   # their subVIs ran inside their tests / runs, so build-only evidence is enough for those
    nokind = [n for n in sorted(stepped) if not (rec[n].get("evidence") or {}).get("kind")]
    if nokind:
        return f"the verified record has no evidence kind: {nokind} (recorded by an older job_check: run python -m jacq.run {name} again)"
    weak = [n for n in sorted(stepped) if rec[n]["evidence"]["kind"] == "build only"]
    if weak:
        return f"VIs run by PRODUCTION steps were only verified as 'build only' (never run): {weak}"
    ws = writer_sha()
    if not ws:
        return f"no writer at {lablib.SHLIM} (LVAGENT_SHLIM): can't tell whether the VIs were built by it"
    old = [v["name"] for v in job.VIS if rec[v["name"]].get("writer_sha") != ws]
    if old:
        return f"verified with another writer than the current one: {old} (run python -m jacq.run {name} until it is recorded)"
    cache = load(out / ".spec_hash.json")
    if cache is None:
        return f"{out / '.spec_hash.json'} can't be read: build the job first"
    if cache.get("__writer__") != ws:
        return "the writer changed since these VIs were built: rebuild (python -m jacq.run) first"
    stale = [v["name"] for v in job.VIS
             if cache.get(v["name"]) != cache_key(jobguard.spec_hash(v), v["spec"], cache, out) or not (out / f"{v['name']}.vi").exists()]
    return f"VIs on disk were not built from the current spec: {stale}" if stale else None


def keep_awake(on):
    if os.name == "nt" and not FAKE:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)   # strict: inputs are checked to be JSON (production_problems)


def step_sig(s, vis, cache, sigs):
    """what a result depends on: the VI as built (its spec, the subVIs of this job it calls, the writer: the .spec_hash.json entry), the step
    as written (timeout_s and check aside: the check is evaluated again on a reused result), and the steps it takes from"""
    refs = [f for val in (s.get("inputs") or {}).values() for f in (jobguard.step_refs(val) or ([], None))[0]]
    blob = [jobguard.spec_hash(vis[s["vi"]]), cache.get(s["vi"]), cache.get("__writer__"),
            {k: v for k, v in s.items() if k not in ("timeout_s", "check")}, [sigs.get(f) for f in refs]]
    return hashlib.sha256(canon(blob).encode("utf-8")).hexdigest()


def resolve(s, spec, done):
    """→ (inputs of the step: spec defaults, then its own, with references taken from the done steps; the referenced names), or a str why not"""
    ins = {c["name"]: c["default"] for c in spec.get("controls", []) if c.get("default") is not None}
    refd = []
    for n, val in (s.get("inputs") or {}).items():
        r = jobguard.step_refs(val)
        if r is None:
            ins[n] = val
            continue
        src, o = r
        miss = [f for f in src if f not in done]
        if miss:
            return f"needs {miss} first"
        got = [done[f]["values"].get(o) for f in src]
        if isinstance(val["from"], list):
            if not all(isinstance(g, list) for g in got):
                return f"input {n!r} concatenates {o!r} of {src}, which is not a list in all of them"
            ins[n] = [x for g in got for x in g]
        else:
            ins[n] = got[0]
        refd.append(n)
    return ins, refd


def run_step(lv, out, spec, st, ins):
    p = out / f"{st['vi']}.vi"
    must_runnable(p)
    lab = labels_of(spec)
    back = {v: k for k, v in lab.items()}
    outs = [lab.get(c["name"], c["name"]) for c in spec.get("indicators", [])]
    t = float(st.get("timeout_s", 3600))   # COM budget: getting a reference, the enum export, setting big inputs and reading every output come on top
    res = lv.c(lv.lv.vi_run, str(p), {lab.get(k, k): v for k, v in ins.items()}, outs, t, None, t=t + max(600.0, 0.05 * t)) or {}
    got = res.get("values") or {}
    vals = jobguard.decode_nonfinite({back.get(k, k): v for k, v in got.items()}, spec)
    return vals, res.get("completed"), [back.get(o, o) for o in outs if o not in got], res.get("unreadable")


def save(path, rec, say):
    err = None
    for i in range(3):                       # a result that took hours: retry a transient write error (a reader holding the file, antivirus)
        try:
            dump_json(path, rec)
            return True
        except OSError as e:
            err = e
            time.sleep(5 * (i + 1))
    say(f"cannot write {path} ({err}); the record follows so it isn't lost:")
    say(json.dumps(rec, ensure_ascii=False, default=str))
    return False


def run_check(job, s, values):
    """the step's check on values → {"pass": bool, ...} (a crash or a malformed answer is a failed check); None without a check"""
    if not s.get("check"):
        return None
    try:
        c = getattr(job, s["check"])(copy.deepcopy(values))   # a copy: a check that converts or pops must not change what is saved
    except (Exception, SystemExit) as e:     # noqa: BLE001
        return {"pass": False, "check_error": f"{type(e).__name__}: {e}"}
    if not (isinstance(c, dict) and isinstance(c.get("pass"), bool)):
        return {"pass": False, "check_error": "check must return a dict with a boolean \"pass\""}
    try:
        return json.loads(json.dumps(c, default=str))   # numpy scalars etc. → plain JSON, so logging and saving it can't fail
    except (TypeError, ValueError) as e:     # non-str keys, a cycle
        return {"pass": False, "check_error": f"check's answer is not JSON-like ({type(e).__name__}: {e}); it said pass={c['pass']}"}


def signatures(S, vis, out):
    cache = load(out / ".spec_hash.json") or {}
    sigs = {}
    for s in S:
        sigs[s["key"]] = step_sig(s, vis, cache, sigs)
    return sigs


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # a LabVIEW error text the console code page can't show must not kill the run
    a = parse_args(list(sys.argv[1:] if argv is None else argv))
    if isinstance(a, str):
        print(f"PRODUCTION REFUSED {a}")
        return 2
    name, plan, only = a
    out = jobguard.job_out(BASE, name)
    must_runnable(out / "x.vi")
    try:
        job = jobguard.load_job(name)
    except Exception as e:                   # noqa: BLE001 a syntax error, a missing file: refused, not "stopped"
        print(f"PRODUCTION REFUSED jobs/{name}.py does not load ({type(e).__name__}: {e})")
        return 2
    probs = jobguard.production_problems(job)
    if getattr(job, "PRODUCTION", None) is None:
        probs = [("PRODUCTION", "this job has no PRODUCTION")]
    if probs:
        for k, m in probs:
            print(f"{k}: {m}")
        print("PRODUCTION REFUSED (fix PRODUCTION in the job file; lint reports the same as PROD)")
        return 2
    S = job.PRODUCTION
    keys = [s["key"] for s in S]
    if only and not only <= set(keys):
        print(f"PRODUCTION REFUSED --only has unknown steps {sorted(only - set(keys))}; steps: {keys}")
        return 2
    vis = {v["name"]: v for v in job.VIS}
    sigs = signatures(S, vis, out)
    prod = out / ("production_fake" if FAKE else "production")
    if plan:
        hb = load(prod / "heartbeat.json")    # no lock probe: it would make a start at the same moment be refused
        if hb:
            print(f"heartbeat: {'exited' if hb.get('exited') else 'step ' + str(hb.get('step'))} at {hb.get('now')} (rewritten every minute while running)")
        nr = None if FAKE else not_ready(job, name, out)
        if nr:
            print(f"a run would be refused now: {nr}")
        done, why = finished(job, S, vis, sigs, prod)
        for s in S:
            k = s["key"]
            r = load(prod / f"{k}.json") or {}
            state = "done" if k in done else why.get(k) or (f"failed x{r.get('attempts', 0)}" if r.get("sig") == sigs[k] else "")
            print(f"{k:28s} {s['vi']:20s} timeout {s.get('timeout_s', 3600) / 3600:5.1f} h  {state}")
        return 0
    global _LOCK
    lock = _LOCK = jobguard.production_lock(out)
    if lock is None:
        print("PRODUCTION REFUSED another production of this job, or a jacq.run / vi_job round of it, is running (.production.lock is held)")
        return 2
    prod.mkdir(parents=True, exist_ok=True)
    sigs = signatures(S, vis, out)           # again under the lock: a rebuild that finished just before can't leave old signatures
    say = Log(prod / "production.log")
    lablib.say = say                         # lablib.LV.c says "NEED RESTART" before os._exit(4): that line then lands in production.log too
    say(f"==== production {name} start  BASE={BASE}  pid={os.getpid()}{'  (fake)' if FAKE else ''}")
    why = not_ready(job, name, out)
    if why and not FAKE:
        say(f"PRODUCTION REFUSED {why}")
        return 2
    if why:
        say(f"(fake run: not refusing although {why})")
    cur = prod / "current.txt"
    if cur.exists():                         # the last run died during this step (reboot, closed window, LabVIEW hung): count it as a failed attempt
        k, run, sig = (cur.read_text(encoding="utf-8", errors="replace").strip().split("\t") + ["", ""])[:3]
        r = load(prod / f"{k}.json") or {}
        if k not in keys or sig != sigs[k]:
            pass                             # a step that is gone or was edited since: its crash is not the new version's
        elif r.get("sig") == sigs[k] and run and r.get("run") == run:
            pass                             # died after this attempt's record was saved: it is already counted there
        else:
            if r.get("sig") != sigs[k] or r.get("ok"):   # a record of an older version of the step (or its finished result): a new attempt count
                r = {"key": k}
            r.update(ok=False, sig=sigs[k], run=run, error="interrupted: the process died during this step", attempts=r.get("attempts", 0) + 1)
            for gone in ("check", "values"):
                r.pop(gone, None)            # not a result whose check could pass later (see finished)
            if not save(prod / f"{k}.json", r, say):
                say("PRODUCTION STOPPED the interrupted attempt could not be recorded (current.txt kept)")
                return 1
            say(f"last run was interrupted during step {k!r} (attempt {r['attempts']}): it will be run again")
        drop(cur)
    state = {"step": None, "started": None}
    stop = threading.Event()

    def beat():
        while not stop.is_set():
            try:
                dump_json(prod / "heartbeat.json", dict(state, pid=os.getpid(), now=time.strftime("%Y-%m-%d %H:%M:%S")))
            except OSError:                  # a reader holding the file: skip this beat, keep beating
                pass
            stop.wait(BEAT_S)
    th = threading.Thread(target=beat, daemon=True)
    th.start()
    keep_awake(True)
    try:
        return _run(job, S, vis, sigs, out, prod, cur, only, state, say)
    except KeyboardInterrupt:                # like closing the window: exit now (a normal exit would wait for the COM thread, i.e. for the VI)
        say("PRODUCTION STOPPED by Ctrl+C: a step in progress counts as an interrupted attempt; its VI may still run in LabVIEW "
            "(abort it there before rerunning)")
        os._exit(1)
    finally:
        keep_awake(False)
        stop.set()
        th.join(5)
        try:
            dump_json(prod / "heartbeat.json", dict(step=None, started=None, pid=os.getpid(),
                                                    now=time.strftime("%Y-%m-%d %H:%M:%S"), exited=True))
        except OSError:
            pass


def finished(job, S, vis, sigs, prod):
    """→ ({key: record} of the steps whose result can be reused, {key: why a finished result can't})
    reused: ok (or completed and failed only its check), same signature, computed from the current results of the steps it takes from (one of
    them rerun → this one too), and its check (as it is now) passes on the stored values"""
    done, why = {}, {}
    for s in S:
        k = s["key"]
        r = load(prod / f"{k}.json")
        if not (r and (r.get("ok") or "check" in r)):   # a "check" entry: the VI completed, only the check decided (an edited check may pass)
            continue
        if r.get("sig") != sigs[k]:
            why[k] = "done, but its VI, the step or a step it takes from changed since: will run again"
            continue
        got = resolve(s, vis[s["vi"]]["spec"], done)
        if isinstance(got, str) or canon(got[0]) != canon(r.get("inputs")):
            why[k] = "done, but a step it takes from will run again (or was rerun since): will run again"
            continue
        c = run_check(job, s, r.get("values") or {})
        if c is not None and not c["pass"]:
            if r.get("ok"):
                why[k] = f"done, but {s['check']} (as it is now) fails on its values: will run again ({canon(c)[:300]})"
            continue
        if not r.get("ok"):
            why[k] = f"its VI completed and {s['check']} (as it is now) passes on the stored values: counted as done"
            r = {**r, "ok": True, "check": c, "error": None, "rechecked": time.strftime("%Y-%m-%d %H:%M:%S")}
        done[k] = r
    return done, why


def _run(job, S, vis, sigs, out, prod, cur, only, state, say):
    lv = None
    done, why = finished(job, S, vis, sigs, prod)
    for k, m in why.items():
        say(f"{k}: {m}")
    for k, r in done.items():
        if r.get("rechecked") and not save(prod / f"{k}.json", r, say):
            say(f"PRODUCTION STOPPED the rechecked result of step {k} could not be written")
            return 1
    for s in S:
        k = s["key"]
        if k in done or (only and k not in only):
            continue
        prev = load(prod / f"{k}.json") or {}
        if prev.get("sig") != sigs[k] or prev.get("ok") or prev.get("timeout_s", 3600) != s.get("timeout_s", 3600):
            prev = {}                        # attempts of an older version of the step (timeout_s too), or a finished result redone: a new count
        if prev.get("attempts", 0) >= 2:
            say(f"PRODUCTION STOPPED step {k} failed twice already ({prev.get('error')}); delete {prod / (k + '.json')} to try again")
            return 1
        spec = vis[s["vi"]]["spec"]
        got = resolve(s, spec, done)
        if isinstance(got, str):
            say(f"PRODUCTION STOPPED step {k}: {got}")
            return 1
        ins, refd = got
        nan_in = [n for n in refd if bad_values(ins[n])]
        if nan_in and not s.get("allow_nan"):
            say(f"PRODUCTION STOPPED step {k}: input(s) {nan_in} taken from earlier steps are missing or contain NaN / inf "
                "(set \"allow_nan\": True on the step if that is intended)")
            return 1
        if lv is None:                       # outside the attempt: a LabVIEW that isn't up yet (just after logon) is not the step's failure
            try:
                lv = LV()
                lv.status()                  # LV() alone doesn't connect; a hang here exits 4 before current.txt exists, so no attempt either
            except Exception as e:           # noqa: BLE001
                say(f"PRODUCTION STOPPED cannot reach LabVIEW ({type(e).__name__}: {e}); no attempt counted, rerun when it is up")
                return 1
        rec = {"key": k, "vi": s["vi"], "sig": sigs[k], "inputs": ins, "timeout_s": s.get("timeout_s", 3600),
               "started": time.strftime("%Y-%m-%d %H:%M:%S"), "fake": FAKE, "attempts": prev.get("attempts", 0), "run": f"{os.getpid()}-{time.time():.6f}"}
        say(f"== {k}: {s['vi']}  timeout {rec['timeout_s'] / 3600:.1f} h")
        state.update(step=k, started=rec["started"])
        cur.write_text(f"{k}\t{rec['run']}\t{sigs[k]}", encoding="utf-8")
        t0 = time.time()
        count = True
        try:
            vals, completed, missing, unreadable = run_step(lv, out, spec, s, ins)
            rec.update(values=vals, completed=completed, ok=completed is not False and not missing)
            if unreadable:
                rec["unreadable"] = unreadable
            if completed is False:
                rec["error"] = f"aborted after timeout_s={rec['timeout_s']} s (raise timeout_s if the step legitimately needs longer)"
            elif missing:
                rec["error"] = f"indicators not read back: {missing}"
        except Exception as e:               # noqa: BLE001 record and stop: a failing production step needs a person
            rec.update(ok=False, error=f"{type(e).__name__}: {str(e)[:1500]}")
            count = "already running" not in str(e)   # left running by an earlier process: not this step's failure
        rec["sec"] = round(time.time() - t0, 2)
        if rec["ok"] and s.get("check"):
            rec["check"] = run_check(job, s, rec["values"])
            if not rec["check"]["pass"]:
                rec.update(ok=False, error=f"{s['check']} failed: " + canon(rec["check"])[:1500])
        rec["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rec["attempts"] += 0 if rec["ok"] or not count else 1
        saved = save(prod / f"{k}.json", rec, say)
        if saved:                            # not saved: current.txt stays, so the next start counts the attempt instead of looping on it
            drop(cur)
        state.update(step=None, started=None)
        if not saved:
            say(f"PRODUCTION STOPPED the result of step {k} could not be written (see the record above)")
            return 1
        if not rec["ok"]:
            say(f"PRODUCTION STOPPED step {k} failed: {rec.get('error')}"
                + ("" if count else " (no attempt counted: abort the VI in LabVIEW or wait for it, then rerun)"))
            return 1
        done[k] = rec
        say(f"   done {k} in {rec['sec']:.0f} s")
    left = [s["key"] for s in S if s["key"] not in done]
    if not left:
        say("PRODUCTION COMPLETE")
        return 0
    if only and only <= set(done):
        say(f"PRODUCTION ONLY DONE {sorted(only)} (not done in this job: {left})")
        return 0
    say(f"PRODUCTION STOPPED steps left: {left}")
    return 1


def drop(cur):
    """current.txt away; one left behind (a scanner holding it) is harmless: its run id matches the saved record, so it isn't counted twice"""
    try:
        cur.unlink(missing_ok=True)
    except OSError:
        pass


_LOCK = None


def entry():
    try:
        return main()
    except SystemExit as e:                  # must_runnable refuses with a message (wrong sandbox)
        if e.code is None or isinstance(e.code, int):
            raise
        lablib.say(f"PRODUCTION REFUSED {e.code}")
        return 2
    except KeyboardInterrupt:                # before or after the steps
        lablib.say("PRODUCTION STOPPED by Ctrl+C")
        return 1
    except Exception:                        # noqa: BLE001 anything else: into production.log too (lablib.say is the log once it is open)
        lablib.say(traceback.format_exc())
        lablib.say("PRODUCTION STOPPED unexpected error (traceback above)")
        return 1
    finally:
        if _LOCK:                            # unlock before closing: Windows frees an unreleased byte lock only after a while
            jobguard.release_lock(_LOCK)


if __name__ == "__main__":
    sys.exit(entry())
