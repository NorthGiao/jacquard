"""lint's predictions vs. on-site outcomes (LabVIEW as the judge): measure lint's misses/false alarms and tell you which rule to change.

  python -m jacq.oracle <job>        compare the pulled-back job_report.json (a BUILD_ONLY probe batch or an ordinary job) against lint's current predictions
  python -m jacq.oracle --probes     compare the single-function probes in private/lab_corpus/probes/ (2026-11, all inputs wired to dbl)

Prediction (per VI): broken = has a type-class E (TYPE/DIMS/AUTOIDX, including those downgraded by BUILD_ONLY/EXPECTED);
                unsure = has a W among TYPE?/DIMS?/REQ?/CASE-*?; ok = none of these.
On-site: create-fail (build failed) / broken / dropped (builds and isn't broken, but the writer reports "wire dropped" or the wire check mismatches) / idle (clean)."""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

from . import catalog as C, wire_diff
from .config import LAB_CORPUS, PRIVATE, job_local_dir
from .job_check import _read_json
from .lint import PREDICTIVE, lint_job, lint_spec, load_job_arg

UNSURE = {"TYPE?", "DIMS?", "REQ?", "CASE-DUP?", "CASE-DEFAULT?", "CASE-VAL?"}

ADVICE = {
    ("broken", "broken"): "Correct (this VI is the on-site source for the rule)",
    ("broken", "idle"): "False alarm: change the rule (this category pair can actually be wired)",
    ("unsure", "broken"): "Upgrade: raise this category pair / this required input to E (this VI is the source)",
    ("unsure", "idle"): "Downgrade: this category pair is actually compatible, remove it from W",
    ("ok", "broken"): "Miss: add a rule (this VI is the source)",
    ("ok", "idle"): "Correct",
    ("broken", "create-fail"): "Right direction (predicted a problem, on-site it couldn't even be built): look at why the build failed, it may deserve its own rule",
    ("unsure", "create-fail"): "Upgrade: a build failure is also a failure (this VI is the source)",
    ("ok", "create-fail"): "Miss: build failure (look at the original error text, add a rule)",
    ("broken", "dropped"): "Right direction (wire dropped rather than broken): check which wire it is",
    ("unsure", "dropped"): "Upgrade: a wire was silently dropped (this VI is the source)",
    ("ok", "dropped"): "Miss: builds and isn't broken, but a wire is dropped / wires mismatch (the most dangerous kind)",
}


def predict(findings):
    by = {}
    for f in findings:
        raw_e = f.level == "E" or (f.level == "I" and f.msg.startswith(("[BUILD_ONLY]", "[EXPECTED]")))
        d = by.setdefault(f.vi, {"pred": "ok", "why": []})
        if raw_e and f.code in PREDICTIVE:
            d["pred"] = "broken"; d["why"].append(f"{f.code}: {f.msg[:120]}")
        elif f.level == "W" and f.code in UNSURE:
            if d["pred"] == "ok":
                d["pred"] = "unsure"
            d["why"].append(f"{f.code}: {f.msg[:120]}")
    return by


def lab_verdict(r, wire_bad=0):
    """create-fail / broken / dropped (builds and isn't broken, but the writer reports a dropped wire or the wire check mismatches) / idle (clean)"""
    if r is None:
        return None
    if r.get("create") is False:
        return "create-fail"
    st = str(r.get("exec_state", ""))
    if st.startswith("bad"):
        return "broken"
    if st != "idle":
        return st or None
    return "dropped" if (r.get("warning") or wire_bad) else "idle"


def summary(cm, n):
    agree = sum(k for (p, v), k in cm.items() if (p, v) in (("broken", "broken"), ("ok", "idle")))
    missed = sum(k for (p, v), k in cm.items() if p == "ok" and v in ("broken", "create-fail", "dropped"))
    false_alarm = sum(k for (p, v), k in cm.items() if p == "broken" and v == "idle")
    print("== confusion table (lint prediction × on-site):", {f"{p}×{v}": k for (p, v), k in sorted(cm.items(), key=str)})
    print(f"== verdicts agree {agree}/{n}; lint said fine but on-site there was a problem (misses) {missed}; lint reported E but on-site clean (false alarms) {false_alarm}; "
          f"unsure (W) {sum(k for (p, _), k in cm.items() if p == 'unsure')}")


def table(rows, show_summary=True):
    cm = Counter((p, v) for _, p, v, _, _ in rows)
    for name, p, v, why, extra in rows:
        adv = ADVICE.get((p, v), "")
        flag = "  " if adv.startswith("Correct") else "!!"
        print(f"{flag} {name:28s} lint={p:7s} on-site={v or '—':11s} {adv}{('  ' + extra) if extra else ''}")
        for w in why[:3]:
            print(f"       {w}")
    if show_summary:
        summary(cm, len(rows))
    return cm


def job_main(arg, D=None):
    """arg = job name or jobs/<job>.py (normalized into a job name by lint.load_job_arg; on error say one sentence, exit code 2).
    D = result directory (default private/lab_corpus/write2/jobs/<job>; passed in by jacq.run --dest / job_check --dir)."""
    name, job = load_job_arg(arg)
    if job is None:
        return 2
    D = Path(D) if D else job_local_dir(name)
    rp = D / "job_report.json"
    if not rp.exists():
        print(f"no {rp}: first run a round on the LabVIEW machine (remote: scripts/vi_job.sh {name}; same machine: python -m jacq.run {name})")
        return 2
    rep, err = _read_json(rp)   # truncated pull-backs do happen: give one sentence, no traceback (the same reader as job_check)
    if err or not isinstance(rep, dict) or not isinstance(rep.get("vis"), dict):
        print(f"{err or 'job_report.json is not a job report'}: most likely an incomplete pull-back / copy-back, not comparing; pull back again (or rerun a round)")
        return 2
    if rep.get("job") != name:
        print(f"this report belongs to {rep.get('job')!r} (the job field in job_report.json), not {name!r}: not comparing")
        return 2
    if not rep.get("complete", True):
        print(f"Note: this report is incomplete (stopped midway on-site), only {len(rep['vis'])} VIs compared")
    pr = predict(lint_job(job, offline=True))
    rows = []
    for v in job.VIS:
        r = rep["vis"].get(v["name"])
        wire_bad = 0
        dp = D / "dumps" / f"{v['name']}.json"
        if r and dp.exists():
            try:
                got, _ = wire_diff.dump_sig(json.loads(dp.read_text(encoding="utf-8"))["result"])
                wire_bad = len(wire_diff.diff(wire_diff.spec_sig(v["spec"]), got))
            except Exception as e:  # noqa: BLE001  a form wire_diff doesn't recognize: note it, counts as neither clean nor mismatched
                r = dict(r, warning=(r.get("warning") or "") + f" [wire check skipped: {type(e).__name__}]")
        p = pr.get(v["name"], {"pred": "ok", "why": []})
        extra = ("writer warning: " + r["warning"][:100]) if r and r.get("warning") else ""
        if r and r.get("create") is False:
            extra = "build failed: " + str(r.get("error", ""))[:140]
        if wire_bad:
            extra = (extra + "  " if extra else "") + f"wire check: {wire_bad} mismatches"
        rows.append((v["name"], p["pred"], lab_verdict(r, wire_bad), p["why"], extra))
    table(rows)
    return 0


def probes_main():
    files = sorted(glob.glob(str(LAB_CORPUS / "probes" / "*.json")))
    if not files:
        print("no private/lab_corpus/probes/ (only runs on a machine with the private corpus)")
        return 2
    saved, C.queue_add = C.queue_add, (lambda *a, **k: None)          # don't write the probe queue while comparing
    dummy = PRIVATE / ".oracle_dummy.vi"
    dummy.write_bytes(b"")
    rows = []
    try:
        for f in files:
            d = json.load(open(f, encoding="utf-8", errors="replace"))
            cr = d.get("create") or {}
            if not cr.get("ok"):
                continue
            vi = Path(f).stem
            p = predict(lint_spec(d["spec"], {}, str(dummy), offline=True, job="probe", vi=vi)).get(vi, {"pred": "ok", "why": []})
            rows.append((vi, p["pred"], lab_verdict({"exec_state": cr["result"]["exec_state"]}), p["why"], ""))
    finally:
        dummy.unlink(missing_ok=True)
        C.queue_add = saved
    quiet = [r for r in rows if not (r[1] == "ok" and r[2] == "broken")]
    print(f"({sum(1 for r in rows if r[1] == 'ok' and r[2] == 'broken')} misses not listed individually: mostly probes with no wires at all, or functions without a type rule)")
    table(quiet, show_summary=False)
    allc = Counter((r[1], r[2]) for r in rows)
    summary(allc, len(rows))
    return 1 if allc[("broken", "idle")] else 0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 1:   # no arguments or extra arguments both get the usage
        print(__doc__); return 2
    return probes_main() if a[0] == "--probes" else job_main(a[0])


if __name__ == "__main__":
    sys.exit(main())
