"""After pulling back: summarize job_report, compare spec with scan dump for each VI via wire_diff (VIs not rebuilt reuse the dump from their last build), compare front-panel styles, list screenshots;
if everything passes, record it in jobs/verified.json.

Usage: python -m jacq.job_check <job> [--dir <result dir>]     reads private/lab_corpus/write2/jobs/<job>/ by default
      python -m jacq.job_check --status <job>               read-only: whether each VI of this job currently counts as verified, and why
Exit codes: 0 = passed (recorded in verified.json; rehearsal jobs with LINT_EXPECT pass but are not recorded); 1 = on-site failures / wire or style mismatches (old record revoked),
        or the LabVIEW machine rejected this job (hardware I/O etc., nothing was built; old record untouched);
        2 = no verdict possible (the report isn't from this job, didn't finish, only partly ran, a dump is missing, a file can't be read or has the wrong shape, the job name doesn't exist ...), verified.json unchanged

Conditions for recording verified (all required):
- The report was produced by the current job: new reports compare the job file hash (job_sha) and each VI's spec_hash; old reports (without these fields) compare against the .spec_hash.json pulled back with the results.
- The report is complete, is not an LVAGENT_FAKE dry run, every VI in VIS is in the report, and there were no on-site failures.
- Every VI was built (exec_state = idle), has a successful scan dump of this version of the spec, the wire check was actually done with 0 mismatches; front-panel styles match.
An old record is only revoked when the report is complete and there is counter-evidence: in a round that didn't finish, the local dump may still be from the previous spec version and can't be used as counter-evidence.
"""
import datetime, json, os, sys
from pathlib import Path

from . import wire_diff
from .config import GUARD, JOBS, job_local_dir, writer_sha
from .lint import load_job_arg

VERIFIED = JOBS / "verified.json"
spec_hash = GUARD.spec_hash   # the same function as the spec_hash that lab/vi_job.py writes into the report / dump (lab/jobguard.py); the value cached on the LabVIEW machine equals it only for VIs without subVIs from this job

# spec style → the control's vi_class in the dump. Only those seen on-site are judged; the rest only get an "unchecked" hint.
STYLE_CLASS = {"Waveform Graph": "WaveformGraph"}          # seen in on-site dumps
STYLE_CLASS_GUESS = {"Waveform Chart": "WaveformChart", "XY Graph": "XYGraph",
                     "Round LED": "Boolean", "Square LED": "Boolean", "Stop Button": "Boolean", "OK Button": "Boolean"}


def fp_diff(spec, dump):
    """Whether the front-panel control styles actually landed on the VI. Returns (mismatch list, unchecked list).
    When the writer fails to apply a style (e.g. a Waveform Graph built as a numeric array), neither the wire check nor tests can tell; only this comparison can."""
    got = {n.get("label"): n.get("vi_class") for n in dump.get("nodes", []) if n.get("class") == "ControlTerminal"}
    bad, unchecked = [], []
    for c in (spec.get("controls") or []) + (spec.get("indicators") or []):
        st = c.get("style")
        if not st:
            continue
        lab = c.get("label") or c["name"]
        want = STYLE_CLASS.get(st) or STYLE_CLASS_GUESS.get(st)
        if lab not in got or want is None:
            unchecked.append((lab, st))
        elif got[lab] != want:
            (bad if st in STYLE_CLASS else unchecked).append((lab, st, got[lab]))
    return bad, unchecked


def _read_json(p):
    """(content, None) or (None, reason it can't be read): a file truncated while pulling back or broken by manual copying gets one sentence, not a traceback."""
    try:
        return json.loads(Path(p).read_text(encoding="utf-8")), None
    except (OSError, ValueError) as e:
        return None, f"{Path(p).name} can't be read ({type(e).__name__}: {str(e)[:120]})"


class VerifiedError(Exception):
    """jobs/verified.json can't be read or has the wrong shape. It is the on-site verification record and is only written by this module: no verdict, don't overwrite it, hand it to the user."""


def _record_ok(x):
    """One VI's record: a dict, spec_hash is a string, evidence is absent or a dict (status_main reads evidence["kind"])."""
    return isinstance(x, dict) and isinstance(x.get("spec_hash"), str) and isinstance(x.get("evidence") or {}, dict)


def _load_verified():
    if not VERIFIED.exists():
        return {}
    try:
        d = json.loads(VERIFIED.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise VerifiedError(f"{VERIFIED.name} can't be read ({type(e).__name__}: {str(e)[:120]})") from None
    if not (isinstance(d, dict) and all(isinstance(r, dict) and all(_record_ok(x) for x in r.values()) for r in d.values())):
        raise VerifiedError(f"{VERIFIED.name} does not have the shape {{job: {{VI: record}}}}")
    return d


def _write_verified(d):
    """Write a temporary file first, then rename: if killed halfway, what remains is the old complete file, not a half-written one."""
    tmp = VERIFIED.with_name(VERIFIED.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, VERIFIED)


_VERIFIED_BAD = ("==== {why}: this file is only written by job_check, do not edit it by hand; it is the on-site verification record, if it is broken tell the user and let the user decide how to restore it (do not delete or rewrite it yourself). "
                 "No verdict this round, it is left unchanged")


def verified_status(jobname, VIS):
    """{vi: True/False/None}: True = verified on-site and the spec hasn't changed; False = verified but the spec has changed; None = never verified"""
    rec = _load_verified().get(jobname, {})
    return {v["name"]: (None if v["name"] not in rec else rec[v["name"]]["spec_hash"] == spec_hash(v)) for v in VIS}


def verified_detail(job):
    """{vi: {"spec": True/False/None, "job_file_same": bool|None, "writer_same": bool|None, "kind": evidence kind|None}}
    job_file_same / writer_same: whether the job file (tests, check, reference implementation all live there) / the writer changed after verification; old records didn't store the hash → None (unknown)."""
    name = job.__name__.rsplit(".", 1)[-1]
    rec = _load_verified().get(name, {})
    jsha, wsha = job.__job_sha__, writer_sha()   # computed over the bytes that load_job exec'd
    out = {}
    for v in job.VIS:
        r = rec.get(v["name"])
        out[v["name"]] = {"spec": None if r is None else r["spec_hash"] == spec_hash(v),
                          "job_file_same": None if not r or not r.get("job_sha") else r["job_sha"] == jsha,
                          "writer_same": None if not r or not r.get("writer_sha") else r["writer_sha"] == wsha,
                          "kind": ((r or {}).get("evidence") or {}).get("kind")}
    return out


def status_main(name):
    name, job = load_job_arg(name)
    if job is None:
        return 2
    word = {True: "unchanged", False: "changed", None: "not stored in the old record, unknown"}
    try:
        detail = verified_detail(job)
    except VerifiedError as e:
        print(_VERIFIED_BAD.format(why=e))
        return 2
    for vi, d in detail.items():
        if d["spec"] is None:
            print(f"  {vi}: never verified")
        elif not d["spec"]:
            print(f"  {vi}: verified, but the spec changed afterwards → doesn't count as verified, must go to the LabVIEW machine again")
        else:
            print(f"  {vi}: verified, spec unchanged; evidence {d['kind'] or '(old record did not store the kind)'}; "
                  f"job file (tests / check) {word[d['job_file_same']]}; writer {word[d['writer_same']]}")
    return 0


def _evidence(r):
    """The kind of on-site evidence for this VI: tests / runs+check / runs(no assertion) / build only."""
    ev = {k: r[k] for k in ("exec_state", "tests") if k in r}
    runs = r.get("runs") or []
    checked = [x for x in runs if isinstance(x.get("pass"), bool)]
    if runs:
        ev["runs"] = f"{sum(1 for x in checked if x['pass'])}/{len(checked)} passed (check)" if checked else f"{len(runs)} runs, no assertion"
    kinds = (["tests"] if "tests" in r else []) + (["runs+check"] if checked else (["runs(no assertion)"] if runs else []))
    ev["kind"] = "+".join(kinds) or "build only"
    return ev


def _bound(job, rep, D):
    """Whether the report was produced by the current job. Returns a list of reasons why not (empty = it was)."""
    why = []
    if rep.get("job_sha"):
        if rep["job_sha"] != job.__job_sha__:
            why.append("the job file was changed after this run on the LabVIEW machine (or the results pulled back are from another version)")
    cp = D / ".spec_hash.json"
    cache = _read_json(cp)[0] if cp.exists() else None
    for v in job.VIS:
        r = rep["vis"].get(v["name"])
        if r is None:
            continue
        h = r.get("spec_hash") or (cache or {}).get(v["name"])
        if h is None:
            why.append(f"{v['name']}: neither the report nor .spec_hash.json has its spec hash, can't confirm which spec version it was built from")
        elif h != spec_hash(v):
            why.append(f"{v['name']}: on-site a different spec version was built (the spec was changed after the run on the LabVIEW machine)")
    return why


def main(name, D=None):
    """See the file header. When the report / dump is valid JSON but has the wrong shape (missing keys, a string where a dict should be ...) give one sentence and exit code 2, no traceback:
    verified.json is only written after everything has been read and judged, so nothing has been written yet at that point."""
    name, job = load_job_arg(name)
    if job is None:
        return 2
    D = Path(D) if D else job_local_dir(name)
    try:
        rc = _judge(name, job, D)
    except VerifiedError as e:
        print(_VERIFIED_BAD.format(why=e))
        return 2
    except (KeyError, AttributeError, TypeError, ValueError, IndexError) as e:
        print(f"==== the content of job_report.json or a dump has the wrong shape ({type(e).__name__}: {str(e)[:200]}): most likely broken in copying or edited by hand. "
              "No verdict, verified.json unchanged; pull back / copy back again (or rerun a round)")
        return 2
    if rc == _BUILD_ONLY:   # type-probe batches go to oracle; kept outside the try: oracle's own bugs must show a traceback, not be taken as "report has the wrong shape"
        from . import oracle
        return oracle.job_main(name, D)
    return rc


_BUILD_ONLY = "build_only"


def _judge(name, job, D):
    rp = D / "job_report.json"
    if not rp.exists():
        print(f"==== no {rp}: not run on the LabVIEW machine yet, or results not pulled back / copied back (remote: scripts/vi_job.sh {name}; same machine: python -m jacq.run {name})")
        return 2
    rep, err = _read_json(rp)
    if err or not isinstance(rep, dict) or not isinstance(rep.get("vis"), dict):
        print(f"==== {err or 'job_report.json is not a job report'}: most likely an incomplete pull-back / copy-back. No verdict; pull back again (or rerun a round)")
        return 2
    if "job" not in rep:
        raise KeyError("job")   # no job field = wrong shape, main says one sentence about it
    if rep["job"] != name:   # --dir points to another job's result directory: without this check A's results would record verified for B, or revoke B's old record
        print(f"==== this report belongs to {rep['job']!r} (the job field in job_report.json), not {name!r}: no verdict, verified.json unchanged. --dir must point to {name}'s own result directory")
        return 2
    if rep.get("fake"):
        print("==== this report comes from an LVAGENT_FAKE=1 dry run (fake LabVIEW), not an on-site result: no verdict, not recording verified")
        return 2
    if rep.get("build_only"):   # type-probe batch: broken is data; only lint prediction × on-site comparison, not recording verified
        return _BUILD_ONLY
    for vi, node, why in rep.get("refused", []):
        print(f"  LabVIEW machine rejected {vi}.{node}: {why}")
    if rep.get("refused"):
        print("==== the LabVIEW machine rejected this job, nothing was built. The reasons are listed line by line above: VI name → lint's JOB-NAME, OUT → JOB-OUT, hardware I/O node / external subVI → HW-IO; fix it according to that entry's \"Fix\"")
        return 1
    complete = rep.get("complete", True)   # old reports don't have this key: back then the report was only written after finishing
    failed = bool(rep.get("failed"))
    print(f"job {rep['job']}: {'failed' if failed else ('passed' if complete else 'did not finish')}; rebuilt this round {rep.get('rebuilt', [])}")
    stale = _bound(job, rep, D)
    if stale:
        for w in stale:
            print(f"  {w}")
        print("==== this report was not produced by the current job: no verdict, verified.json unchanged. Run another round on the LabVIEW machine and check again")
        return 2
    missing, unchecked, wire_bad, fp_bad, evid = [], [], 0, 0, {}
    for v in job.VIS:
        r = rep["vis"].get(v["name"])
        if r is None:
            missing.append(v["name"])
            continue
        evid[v["name"]] = _evidence(r)
        line = f"  {v['name']}: create={r.get('create')} state={r.get('exec_state')}"
        if "tests" in r: line += f" tests={r['tests']}"
        if "runs" in r: line += f" runs pass={sum(1 for x in r['runs'] if x.get('pass', 'error' not in x))}/{len(r['runs'])}"
        if r.get("error"): line += f"\n    error: {r['error'][:600]}"
        for c in r.get("failed_cases", []):
            line += f"\n    not passed: {json.dumps(c, ensure_ascii=False, default=str)[:400]}"
        if r.get("warning"): line += f"\n    writer warning: {r['warning']}"
        print(line)
        state = str(r.get("exec_state", ""))
        if r.get("create") is False or state.startswith("bad"):
            continue   # not built / broken: vi_job already judged this round as failed (rep["failed"]), nothing to check
        if state != "idle":   # this round didn't get to exec_state, or vi_info failed (exec_state is an error text): can't treat it as built
            unchecked.append(f"{v['name']}: build not confirmed (exec_state={state[:80] or 'none'})")
            print("    wires not checked: build not confirmed")
            continue
        dp = D / "dumps" / f"{v['name']}.json"
        dump, derr = _read_json(dp) if dp.exists() else (None, None)
        h = dump.get("spec_hash") if isinstance(dump, dict) else None
        why = None
        if not dp.exists():   # the LabVIEW machine only dumps rebuilt VIs: without a local dump from the previous round (fresh clone, private/ cleared) it can't be checked
            why = f"no scan dump (dumps/{v['name']}.json) -- add --force to rebuild a round and there will be one"
        elif derr or not isinstance(dump, dict):
            why = derr or "the dump file is not a dump"
        elif not complete and r.get("create") is True:   # dumps are made after the whole round finishes: this round rebuilt it but didn't finish, so the local dump is from the previous version
            why = "this round rebuilt it but didn't finish, no dump made yet (the local one is the previous dump, can't be compared)"
        elif h is not None and h != spec_hash(v):
            why = "the local dump was built from a different spec version"
        elif not dump.get("ok", True) or "result" not in dump:
            why = f"on-site scan dump failed ({str(dump.get('error'))[:200]})"
        if why:
            unchecked.append(f"{v['name']}: {why}")
            print("    wires not checked: " + why.split(" (")[0].split(" -- ")[0])
            continue
        if v["name"] not in rep.get("rebuilt", []): print("    (reusing the dump from the last build)")
        fb, fu = fp_diff(v["spec"], dump["result"])
        for x in fb:
            fp_bad += 1
            print(f"    front-panel style mismatch: {x[0]!r} spec style={x[1]!r}, in the VI it is {x[2]}")
        for x in fu:
            print(f"    front-panel style not checked: {x}")
        try:
            want = wire_diff.spec_sig(v["spec"])
            got, bad = wire_diff.dump_sig(dump["result"])
        except Exception as e:   # a form wire_diff doesn't recognize (spec key or dump representation): the check wasn't done → can't record verified, and it isn't counter-evidence either
            unchecked.append(f"{v['name']}: wire_diff doesn't support this spec / dump form ({type(e).__name__}: {e})")
            print(f"    wires not checked (unsupported by wire_diff: {type(e).__name__}: {e})")
            continue
        diff = wire_diff.diff(want, got)
        wire_bad += len(diff)
        evid[v["name"]]["wires"] = f"spec {sum(want.values())} / VI {sum(got.values())}, mismatches {len(diff)}"
        print(f"    wires spec {sum(want.values())} / VI {sum(got.values())}, mismatches {len(diff)}, non-unique drivers {len(bad)}")
        for k, a, b in diff[:10]:
            print(f"      spec={a} VI={b}  {k[0]} → {k[1]}")
    for f in sorted((D / "snap_after_run").glob("*vip.png")) + sorted((D / "snap").glob("*vid.png")):
        print("  screenshot", f)
    d = _load_verified()
    if not complete:   # judged before "counter-evidence": a round that didn't finish revokes no records
        for u in unchecked:
            print(f"  wire check not done -- {u}")
        print("==== the LabVIEW machine didn't finish this round (timeout / hung): above is the part that was done. Not recording verified, old record untouched; rerun a round")
        return 2
    if failed or wire_bad or fp_bad:   # counter-evidence: on-site failure / wire mismatch / style mismatch → revoke the old record
        if name in d:
            del d[name]
            _write_verified(d)
            print(f"==== not passed: in {VERIFIED.name}, revoked the old record of {name}")
        else:
            print("==== not passed (on-site failure / wire mismatch / style mismatch, see above)")
        return 1
    # none of the following are counter-evidence: don't record, and don't revoke old records
    if missing:
        print(f"==== this round only ran some of the VIs (--only?), {missing} not in the report: not recording verified. Only a full round without --only gets recorded")
        return 2
    if unchecked:
        for u in unchecked:
            print(f"  wire check not done -- {u}")
        print("==== tests / runs passed, but the wire check of some VIs wasn't done: not recording verified (reasons above)")
        return 2
    if getattr(job, "LINT_EXPECT", None):
        print("==== passed on-site; this is a rehearsal job with LINT_EXPECT (lint E's were downgraded), not recording verified")
        return 0
    try:
        day = datetime.date.fromisoformat(str(rep.get("time", ""))[:10]).isoformat()   # the day of the run on the LabVIEW machine, not the day job_check is rerun
    except ValueError:
        day = datetime.date.fromtimestamp(rp.stat().st_mtime).isoformat()
    # only record the job_sha / writer_sha present in the report: old-format reports lack them, meaning "unknown which job file / writer version that run used", and the current file hashes can't stand in for them
    d[name] = {v["name"]: {"spec_hash": spec_hash(v), "date": day, "evidence": evid[v["name"]],
                           "job_sha": rep.get("job_sha"), "writer_sha": rep.get("writer_sha")} for v in job.VIS}
    _write_verified(d)
    weak = [n for n, e in evid.items() if e["kind"] in ("build only", "runs(no assertion)")]
    print(f"==== verified on-site, recorded in {VERIFIED.name} (the screenshots still need a human look at the front-panel layout)")
    if weak:
        print(f"     Note: {weak} have no assertions at all (only confirmed they build, aren't broken, run to completion); say so truthfully when delivering")
    return 0


def cli(a):
    """The command line only accepts three forms: `<job>`, `<job> --dir D` (judge and record), `--status <job>` (read-only). Everything else gets the usage and exit code 2:
    `<job> --status`, misspelled options (`--dirr X`, `--Status`), extra arguments must never be silently taken as a judgment that writes the hook-protected verified.json
    (judged with the default directory while letting people think X was judged); the same goes for a `--dir` value that is empty / whitespace / another option."""
    if len(a) == 2 and a[0] == "--status":
        return status_main(a[1])
    if len(a) == 1 and not a[0].startswith("--"):
        return main(a[0], None)
    if len(a) == 3 and a[1] == "--dir" and not a[0].startswith("--") and a[2].strip() and not a[2].startswith("--"):
        return main(a[0], a[2])   # an empty --dir would fall back to the default directory and still judge and write verified.json; same for `--dir --x`
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
