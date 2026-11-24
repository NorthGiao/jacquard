"""Run one job (lab/jobs/<job>.py) in one round: in dependency order build → exec_state → vi_test → run + check → front-panel screenshot after running
→ block-diagram screenshot → scan dump. VIs whose spec hasn't changed, and whose called subVIs from this job haven't changed spec either, are reused as-is (.spec_hash.json: each VI records "its own spec hash + the cache values of the subVIs it uses";
if a subVI was rebuilt with a changed spec in some round -- even if that round was --only, or stopped midway, and the caller's turn never came -- the VIs calling it are rebuilt in the next round too), only what changed is redone; if the writer (labview_mcp/*.py, jobguard.writer_sha) changed, everything is rebuilt.
Any failing step stops immediately (fail-fast), writing the error and the completed part into job_report.json (written to disk after each VI, complete=false; true only after finishing).
Checked once before starting (VI names by jobguard.BAD_VI_NAME, the rest by jobguard.scan_vis): if a VI name contains a path / drive letter, there are hardware I/O / external process nodes, subVIs outside this job's directory and
vi.lib are called, or the job's OUT is not BASE\\write2\\jobs\\<job>, the whole job is refused (nothing built or run).
job_report.json records job_sha (job file), writer_sha, and each VI's spec_hash: the local jacq.job_check uses them to confirm "this report was produced by the current job".
inputs/outputs/expect in a job always use spec names; here they are converted to front-panel labels automatically (Chinese labels too).
runs may carry actions (UI interaction: set/click/wait/read, names converted to visible labels automatically); check receives values["_reads"], ["_read_t"], ["_click_t"], ["_elapsed"], ["_completed"].
With BUILD_ONLY = True in the job (type-probe batch jobs/typeprobe.py): only build + exec_state + dump, no running; a VI failing to build or being broken doesn't stop,
the next one is built, and each VI's outcome is recorded in job_report.json (locally python -m jacq.oracle <job> compares it against lint's predictions).
Usage: python vi_job.py <job> [--force] [--only name1,name2]"""
import hashlib, json, shutil, sys, time
from pathlib import Path, PureWindowsPath
import jobguard
from lablib import BASE, FAKE, LV, timed, dump_json, say, main_guard, must_runnable, writer_sha


def labels_of(spec):
    return {c["name"]: c.get("label") or c["name"] for c in spec.get("controls", []) + spec.get("indicators", [])}


def cache_key(h, spec, cache, out):
    """Cache value: without subVIs from this job it is just the spec hash h (same as spec_hash in the report); otherwise the current cache values of those subVIs are appended.
    If a subVI's spec changed (its cache value changed, or it was invalidated and not yet rebuilt), the cache value of the VI calling it won't match, and it is rebuilt next time.
    Rebuilding a subVI from the identical spec (--force, the .vi got lost) doesn't change the cache value, and callers keep reusing: the connector is unchanged.
    Only old reports (without the spec_hash field) have job_check compare the cache value as the spec hash, and back then the cache value was the spec hash."""
    deps = sorted({PureWindowsPath(str(x)).stem for x in subvi_paths(spec)   # Windows paths and the local FAKE's / paths are both split on \\
                   if jobguard.norm_path(PureWindowsPath(str(x)).parent) == jobguard.norm_path(out)})
    if not deps:
        return h
    return hashlib.sha256(json.dumps([h, [[d, cache.get(d)] for d in deps]], ensure_ascii=False).encode("utf-8")).hexdigest()


def subvi_paths(d):
    for s in d.get("subvis", []):
        yield s["path"]
    for st in d.get("structures", []):
        yield from subvi_paths(st)
        for fr in st.get("cases", []) + st.get("events", []):
            yield from subvi_paths(fr)


def _act_to_labels(a, lab):
    """actions use spec names; LabVIEW clicks/sets/reads by visible label."""
    k, v = next(iter(a.items()))
    if k == "click":
        return {k: lab.get(v, v)}
    if k == "set":
        return {k: {lab.get(n, n): x for n, x in v.items()}}
    if k == "read":
        return {k: [lab.get(n, n) for n in v]}
    return a


def main():
    name, force, only = jobguard.parse_job_args(sys.argv[1:])
    if not jobguard.JOB_NAME.fullmatch(name):   # the same regex as lint's JOB-NAME
        raise SystemExit(f"job name {name!r} may only contain letters, digits, underscores (the module name of jobs/<job>.py)")
    # the output directory is computed from the job name, the OUT written in the job file itself isn't trusted: the "this job's directory" of the subVI allowlist is anchored on it
    OUT = jobguard.job_out(BASE, name)
    must_runnable(OUT / "x.vi")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "job_report.json").unlink(missing_ok=True)   # deleted before importing the job: whichever later step crashes, the previous round's report won't be pulled back as this round's
    global _PROD_LOCK                # held until the process ends: no lab/production.py of this job can start meanwhile
    _PROD_LOCK = jobguard.production_lock(OUT)
    if _PROD_LOCK:
        import atexit
        atexit.register(jobguard.release_lock, _PROD_LOCK)   # unlock before closing (Windows frees an unreleased lock only after a while)
    if _PROD_LOCK is None:           # (after the report was deleted: a refused round leaves no report, i.e. no verdict)
        raise SystemExit(f"a production of {name} is running (lab/production.py holds .production.lock): rebuilding would swap its VIs; "
                         "wait for it or stop it first")
    job = jobguard.load_job(name)   # load from source, never a possibly stale .pyc
    msg = jobguard.check_only(only, [v["name"] for v in job.VIS]) if only else None   # misspelled names in --only: must not "build nothing and still JOB COMPLETE OK"
    if msg:
        raise SystemExit(msg)
    wsha = writer_sha()
    rep = {"job": name, "vis": {}, "fake": FAKE, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "job_sha": job.__job_sha__, "writer_sha": wsha}   # computed by load_job over exactly the bytes exec'd
    build_only = bool(getattr(job, "BUILD_ONLY", False))
    rep["build_only"] = build_only
    rep["complete"] = False
    refused = [(v["name"], "name", f"VI name {v['name']!r} cannot be used as a file name (empty, contains \\ / : * ? \" < > | or control characters, starts or ends with a space / dot, is a Windows reserved name like CON / NUL / COM1, or is longer than 100 characters): it would be built outside this job's directory or fail to build")
               for v in job.VIS if jobguard.BAD_VI_NAME.search(v["name"])]   # the same regex as lint's JOB-NAME
    refused += jobguard.scan_vis(job.VIS, OUT)
    if jobguard.norm_path(getattr(job, "OUT", "")) != jobguard.norm_path(OUT):   # the same criterion as lint's JOB-OUT; running vi_job.py directly bypasses lint, so block it here once more
        refused.insert(0, (name, "OUT", f"the job's OUT={getattr(job, 'OUT', None)} is not {OUT}"))
    if refused:   # lint's JOB-NAME / HW-IO / JOB-OUT use the same jobguard and the same OUT computed from the job name; running vi_job.py directly bypasses lint, so this is where it's blocked
        rep.update(refused=[list(x) for x in refused], failed=True, complete=True, rebuilt=[])
        dump_json(OUT / "job_report.json", rep)
        for vi, node, why in refused:
            say(f"refused {vi}.{node}: {why}")
        say("JOB COMPLETE REFUSED (invalid VI name / OUT; the job pipeline doesn't build or run hardware I/O nodes or subVIs outside this job's directory / vi.lib. Reasons line by line above, corresponding to lint's JOB-NAME / JOB-OUT / HW-IO)")
        return
    lv = LV()
    hp = OUT / ".spec_hash.json"
    try:
        cache = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    except (OSError, ValueError):
        cache = None
    if not isinstance(cache, dict):   # the cache can't be read (truncated / broken by hand): treat as no cache, rebuild everything. Sandbox files can only be deleted by the user, it must not block this job forever
        say(".spec_hash.json can't be read: treating as no cache, rebuilding everything")
        cache = {}
    if wsha and cache and cache.get("__writer__") != wsha:
        say("the writer differs from when these VIs were last built (or the cache didn't record the writer): cache invalidated, rebuilding everything")
        cache = {}
    rebuilt, failed = set(), False
    for v in job.VIS:
        rep["rebuilt"] = sorted(Path(x).stem for x in rebuilt)
        dump_json(OUT / "job_report.json", rep)   # even when hung/timed out midway, the completed part is left behind (complete=false); VIs rebuilt by then don't have a dump yet
        n, spec = v["name"], v["spec"]
        if only and n not in only:
            continue
        p = OUT / f"{n}.vi"
        lab = labels_of(spec)
        h = jobguard.spec_hash(v)
        r = rep["vis"][n] = {"spec_hash": h}
        dep = any(str(Path(x)) in rebuilt for x in subvi_paths(spec))
        key = cache_key(h, spec, cache, OUT)
        if force or dep or cache.get(n) != key or not p.exists():
            if n in cache:   # the VI on disk is about to be overwritten: first invalidate its cache entry and write it to disk. If this round is killed midway, the next round won't take the newly built VI as one from the old spec
                del cache[n]
                dump_json(hp, cache)
            c = timed(lv.create, p, spec, v.get("desc", ""))
            r["create"] = c["ok"]
            if not c["ok"]:
                r["error"] = c["error"]
                say(n, "build failed:", c["error"][:800])
                failed = True
                if build_only:
                    continue
                break
            if (c["result"] or {}).get("warning"):
                r["warning"] = c["result"]["warning"]
                say(n, "warning:", r["warning"])
            cache[n] = cache_key(h, spec, cache, OUT)
            rebuilt.add(str(p))
        else:
            r["create"] = "cached"
        i = timed(lv.info, p)
        r["exec_state"] = (i.get("result") or {}).get("exec_state") if i["ok"] else i.get("error")
        say(n, "create", r["create"], "exec_state", r["exec_state"])
        if str(r["exec_state"]).startswith("bad"):
            r["error"] = "VI broken (most likely types: look at lint's W/I, or check with vi_dump)"
            failed = True
            if build_only:
                rebuilt.add(str(p))      # broken ones are dumped too (scan can read broken VIs), to see in the comparison whether wires were dropped
                continue
            break
        if build_only:
            continue
        if v.get("tests"):
            cases = [dict(c, inputs={lab.get(k, k): x for k, x in c.get("inputs", {}).items()},
                          expect={lab.get(k, k): x for k, x in c["expect"].items()}) for c in v["tests"]]
            t = timed(lv.test, p, cases, 30.0)
            res = t.get("result") or {}
            r["tests"] = res.get("summary") if t["ok"] else t.get("error")
            bad = [c for c in res.get("cases", []) if not c.get("passed")]
            say(n, "tests", r["tests"])
            if not t["ok"] or bad:
                r["failed_cases"] = bad[:5]
                failed = True
                break
        if v.get("runs"):
            R = v["runs"]
            inv = {lab.get(k, k): k for k in R.get("outputs", [])}
            chk = getattr(job, R["check"]) if R.get("check") else None
            r["runs"] = []
            back = {v_: k_ for k_, v_ in lab.items()}
            acts = [_act_to_labels(a_, lab) for a_ in R.get("actions", [])] or None
            for k in range(R.get("repeat", 1)):
                x = timed(lv.run, p, {lab.get(k_, k_): val for k_, val in R.get("inputs", {}).items()},
                          (list(inv) or None) if not acts else None, R.get("timeout", 120.0), acts)
                res_ = x.get("result") or {}
                vals = jobguard.decode_nonfinite({back.get(k_, k_): val for k_, val in (res_.get("values") or {}).items()}, spec)
                if acts:   # the values read by each read step are given to check in order: values["_reads"] = [{name: value}, ...]
                    vals["_reads"] = [jobguard.decode_nonfinite({back.get(k_, k_): v_ for k_, v_ in a_["read"].items()}, spec)
                                      for a_ in res_.get("actions", []) if isinstance(a_.get("read"), dict)]
                    vals["_completed"] = res_.get("completed")
                    # timing (seconds, counted from Run): the moment of each read / click step, and the duration of the whole run
                    vals["_read_t"] = [a_.get("t") for a_ in res_.get("actions", []) if isinstance(a_.get("read"), dict)]
                    vals["_click_t"] = [(back.get(a_["clicked"], a_["clicked"]), a_.get("t"))
                                        for a_ in res_.get("actions", []) if "clicked" in a_]   # in order, the same button may appear several times
                    vals["_elapsed"] = res_.get("elapsed_s")
                one = {"sec": x["sec"]}
                if not x["ok"]:
                    one["error"] = x["error"]
                elif chk:
                    try:          # a wrongly written check (e.g. comparing None by size) must not crash the whole vi_job round
                        got = chk(vals)
                        one.update(got if isinstance(got, dict) else {})
                        if not isinstance(got, dict) or not isinstance(got.get("pass"), bool):   # no assertion result can't count as passing
                            one.update({"pass": False, "check_error": "check must return a dict with a boolean \"pass\""})
                    except Exception as e_:
                        one.update({"pass": False, "check_error": f"{type(e_).__name__}: {e_}"})
                else:
                    one["values"] = {k_: (val if not isinstance(val, list) or len(val) <= 16 else
                                          {"len": len(val), "head": val[:5]}) for k_, val in vals.items()}
                r["runs"].append(one)
                say(n, "run", k, json.dumps(one, ensure_ascii=False, default=str)[:400])
                if "error" in one or one.get("pass") is False:
                    failed = True
            if failed:
                break
        if v.get("runsnap"):   # front-panel screenshot after running: keep holding the same reference, otherwise the VI gets unloaded between the two calls
            ins = {lab.get(k, k): val for k, val in v["runsnap"].get("inputs", {}).items()}

            must_runnable(p)   # the writer's vi_run is called directly below, bypassing the check in lablib.LV.run

            def run_and_snap(L, p=str(p), ins=ins):
                keep = L._vi(p)
                keep.FPWinOpen = True
                try:
                    L.vi_run(p, ins, None, 120.0)
                    return L.vi_snapshot(p, True, False, False, False)[1]
                finally:
                    keep.FPWinOpen = False
            s = timed(lambda: lv.c(run_and_snap, lv.lv, t=300))
            sd = OUT / "snap_after_run"
            sd.mkdir(exist_ok=True)
            for f in (s["result"] if s["ok"] else []):
                shutil.copy2(f, sd / f"{n}__{Path(f).name}")
            r["runsnap"] = s["ok"] or s.get("error")
    for n in [n for n in rep["vis"] if str(OUT / f"{n}.vi") in rebuilt]:   # only rebuilt VIs get screenshots and dumps
        p = str(OUT / f"{n}.vi")
        if not build_only:   # probe batches take no screenshots (saves time; only the dump is needed for the wire check)
            s = timed(lambda: lv.c(lv.lv.vi_snapshot, p, True, True, True, False, t=300))
            sd = OUT / "snap"
            sd.mkdir(exist_ok=True)
            for f in (s["result"][1] if s["ok"] else []):
                shutil.copy2(f, sd / f"{n}__{Path(f).name}")
        (OUT / "dumps").mkdir(exist_ok=True)
        # include this VI's spec hash in the dump: the local job_check uses it to confirm "this dump was built from this version of the spec" (in a round that didn't finish, the local one is the previous version's dump)
        dump_json(OUT / "dumps" / f"{n}.json", dict(timed(lv.dump, p, "scan"), spec_hash=rep["vis"][n]["spec_hash"]))
    if wsha:
        cache["__writer__"] = wsha
    dump_json(hp, cache)
    rep["failed"] = failed if not build_only else None   # probe batch: broken is data, not failure
    rep["complete"] = True
    rep["rebuilt"] = sorted(Path(x).stem for x in rebuilt)
    dump_json(OUT / "job_report.json", rep)
    say("JOB COMPLETE", "FAILED" if failed else "OK")


if __name__ == "__main__":
    main_guard(main)
