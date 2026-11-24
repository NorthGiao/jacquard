"""The on-site result → verdict → record verified stage (jacq.job_check, lab/vi_job.py, lab/jobguard.py, jacq.run).
Fully offline: job_check uses synthetic reports and dumps, lab/vi_job.py uses the fake LabVIEW of LVAGENT_FAKE=1; everything is written in tmp_path, jobs/verified.json and private/ are not touched."""
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from jacq import job_check as J
from jacq import lint as L
from jacq.config import GUARD, REPO, writer_sha

SPEC = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "functions": [{"name": "a", "function": "Increment"}], "wires": [["x", "a.x"], ["a.x+1", "y"]]}
DUMP = {"nodes": [{"id": "x_1", "kind": "control", "label": "x"}, {"id": "y_2", "kind": "indicator", "label": "y"},
                  {"id": "increment_3", "kind": "function", "function": "Increment"}],
        "wires": [{"from": "x_1", "to": ["increment_3.x"]}, {"from": "increment_3.x+1", "to": ["y_2"]}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A synthetic two-VI job (a, b with the same spec) + an "all passed" report in the new format + dumps; verified.json redirected to tmp."""
    jf = tmp_path / "zzjob.py"
    jf.write_text("# synthetic job\n", encoding="utf-8")
    job = types.ModuleType("jobs.zzjob")
    job.__file__ = str(jf)
    job.OUT = r"D:\agent_sandbox\lv_agentic\write2\jobs\zzjob"
    job.VIS = [{"name": "a", "spec": json.loads(json.dumps(SPEC)), "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]},
               {"name": "b", "spec": json.loads(json.dumps(SPEC)), "runs": {"inputs": {"x": 1}, "outputs": ["y"]}}]
    D = tmp_path / "result"
    (D / "dumps").mkdir(parents=True)
    rep = {"job": "zzjob", "fake": False, "complete": True, "failed": False, "build_only": False, "rebuilt": ["a", "b"],
           "time": "2026-11-13 10:00:00", "job_sha": GUARD.file_sha(jf), "writer_sha": "w" * 64,
           "vis": {"a": {"spec_hash": GUARD.spec_hash(job.VIS[0]), "create": True, "exec_state": "idle", "tests": "1/1 cases passed"},
                   "b": {"spec_hash": GUARD.spec_hash(job.VIS[1]), "create": True, "exec_state": "idle", "runs": [{"sec": 0.1, "values": {"y": 2}}]}}}
    for n in ("a", "b"):
        (D / "dumps" / f"{n}.json").write_text(json.dumps({"ok": True, "result": DUMP}), encoding="utf-8")
    V = tmp_path / "verified.json"
    monkeypatch.setattr(J, "VERIFIED", V)
    def load(name):   # same as jobguard.load_job: __job_sha__ computed from the current file bytes at each load
        assert name == "zzjob", name   # job_check has already normalized the command-line argument into a job name (jobs/<job>.py works too)
        job.__job_sha__ = GUARD.file_sha(jf)
        return job
    monkeypatch.setattr(L, "load_job", load)   # load_job_arg resolves load_job in the lint module only at call time

    class E:
        pass
    e = E()
    e.job, e.D, e.V, e.jf = job, D, V, jf
    e.write = lambda r=rep: (D / "job_report.json").write_text(json.dumps(r), encoding="utf-8")
    e.rep = rep
    e.run = lambda: J.main("zzjob", D)
    e.recorded = lambda: json.loads(V.read_text(encoding="utf-8")) if V.exists() else {}
    e.write()
    return e


def test_pass_records_evidence_kind_and_report_date(env, capsys):
    """Records the evidence kind, job file and writer hashes; the date is the day of the LabVIEW machine run, not the day job_check was run."""
    assert env.run() == 0
    r = env.recorded()["zzjob"]
    assert r["a"]["evidence"]["kind"] == "tests" and r["b"]["evidence"]["kind"] == "runs(no assertion)"
    assert r["a"]["date"] == "2026-11-13" and r["a"]["job_sha"] == GUARD.file_sha(env.jf) and r["a"]["writer_sha"] == "w" * 64
    assert "['b'] have no assertions at all" in capsys.readouterr().out
    d = J.verified_detail(L.load_job("zzjob"))
    assert d["a"] == {"spec": True, "job_file_same": True, "writer_same": False, "kind": "tests"}
    env.jf.write_text("# tests changed after verification\n", encoding="utf-8")
    assert J.verified_detail(L.load_job("zzjob"))["a"]["job_file_same"] is False and J.verified_status("zzjob", env.job.VIS)["a"] is True


def test_stale_report_new_format_is_refused(env, capsys):
    """If the spec (or job file) was changed after the LabVIEW machine run, running job_check again must not record the old report under the new spec."""
    env.job.VIS[0]["spec"]["functions"][0]["function"] = "Decrement"
    assert env.run() == 2 and not env.V.exists()
    assert "was not produced by the current job" in capsys.readouterr().out
    env.job.VIS[0]["spec"]["functions"][0]["function"] = "Increment"
    env.jf.write_text("# only tests changed\n", encoding="utf-8")          # spec unchanged, job file changed: refused just the same
    assert env.run() == 2 and not env.V.exists()


def test_stale_report_legacy_format_uses_pulled_cache(env, capsys):
    """Old reports have no job_sha / spec_hash, judge by the .spec_hash.json pulled back with the results; if even that is missing, refuse."""
    legacy = {"job": "zzjob", "failed": False, "rebuilt": ["a", "b"],
              "vis": {n: {k: v for k, v in r.items() if k != "spec_hash"} for n, r in env.rep["vis"].items()}}
    env.write(legacy)
    assert env.run() == 2 and "can't confirm which spec version" in capsys.readouterr().out and not env.V.exists()
    cache = {v["name"]: GUARD.spec_hash(v) for v in env.job.VIS}
    (env.D / ".spec_hash.json").write_text(json.dumps(cache), encoding="utf-8")
    assert env.run() == 0 and set(env.recorded()["zzjob"]) == {"a", "b"}
    env.V.unlink()
    env.job.VIS[1]["spec"]["constants"] = [{"name": "k", "type": "i32", "value": 12345}]   # a change the wire check can't see
    assert env.run() == 2 and "a different spec version" in capsys.readouterr().out and not env.V.exists()


@pytest.mark.parametrize("how", ["no_dump", "failed_dump", "wire_unsupported"])
def test_wire_check_not_done_means_not_verified(env, capsys, monkeypatch, how):
    """Missing dump, failed dump, unsupported by wire_diff: none may be recorded as verified, and none may crash."""
    if how == "no_dump":
        (env.D / "dumps" / "b.json").unlink()
    elif how == "failed_dump":
        (env.D / "dumps" / "b.json").write_text(json.dumps({"ok": False, "error": "RuntimeError: scan", "sec": 3}), encoding="utf-8")
    else:
        env.job.VIS[1]["spec"]["invoke_nodes"] = [{"name": "iv", "control": "x", "method": "Reinitialize To Default"}]
        env.job.VIS[1]["spec"]["wires"].append(["x", "iv.reference"])
        env.rep["vis"]["b"]["spec_hash"] = GUARD.spec_hash(env.job.VIS[1])
        env.write()
    assert env.run() == 2 and not env.V.exists()
    out = capsys.readouterr().out
    assert "wire check not done" in out and "b:" in out and "not recording verified" in out


def test_incomplete_round_with_stale_local_dump_keeps_old_record(env, capsys):
    """a changed wires and was rebuilt, the round timed out on b → the partial report has a, the local dumps/a.json is still the previous version.
    Must not compare the new spec with the old dump, report "wire mismatch", and then delete the whole job's old record."""
    assert env.run() == 0
    capsys.readouterr()
    stale = json.loads(json.dumps(DUMP))
    stale["wires"][1]["to"] = []                                   # the dump of the previous spec version: doesn't match the current spec
    (env.D / "dumps" / "a.json").write_text(json.dumps({"ok": True, "result": stale}), encoding="utf-8")
    part = dict(env.rep, complete=False, rebuilt=["a"], vis={"a": env.rep["vis"]["a"]})
    part.pop("failed")
    env.write(part)
    assert env.run() == 2 and "zzjob" in env.recorded()
    out = capsys.readouterr().out
    assert "didn't finish" in out and "mismatches 1" not in out and "revoked" not in out and "reusing the dump" not in out
    env.write()                                                    # a complete round, but the local dump is marked with another spec hash: likewise not compared
    (env.D / "dumps" / "a.json").write_text(json.dumps({"ok": True, "result": stale, "spec_hash": "0" * 64}), encoding="utf-8")
    assert env.run() == 2 and "zzjob" in env.recorded() and "a different spec version" in capsys.readouterr().out
    (env.D / "dumps" / "a.json").write_text(json.dumps({"ok": True, "result": stale, "spec_hash": GUARD.spec_hash(env.job.VIS[0])}), encoding="utf-8")
    assert env.run() == 1 and "zzjob" not in env.recorded()        # the dump of this spec version really mismatches: only that is counter-evidence


def test_legacy_report_does_not_vouch_for_the_job_file(env, capsys):
    """Old-format reports have no job_sha -- it's unknown which version of the job file that run used, so the current file hash must not be recorded."""
    legacy = {"job": "zzjob", "failed": False, "rebuilt": ["a", "b"],
              "vis": {n: {k: v for k, v in r.items() if k != "spec_hash"} for n, r in env.rep["vis"].items()}}
    env.write(legacy)
    (env.D / ".spec_hash.json").write_text(json.dumps({v["name"]: GUARD.spec_hash(v) for v in env.job.VIS}), encoding="utf-8")
    env.jf.write_text("# after the LabVIEW machine run, check was changed to always true\n", encoding="utf-8")
    assert env.run() == 0
    r = env.recorded()["zzjob"]["a"]
    assert r["job_sha"] is None and r["writer_sha"] is None
    assert J.verified_detail(L.load_job("zzjob"))["a"]["job_file_same"] is None
    J.status_main("zzjob")
    assert "not stored in the old record, unknown" in capsys.readouterr().out


@pytest.mark.parametrize("arg", ["jobs/zzjob.py", "zzjob.py", "./jobs/zzjob.py", str(REPO / "jobs" / "zzjob.py")])
def test_job_check_takes_a_job_path_like_lint_does(env, capsys, arg):
    """`job_check jobs/mcpi.py --dir X` must not use the raw string as key (a passing report would be written under the garbage key "jobs/mcpi.py", verified.json is hook-protected and
    the agent can't clean it up; a failing report couldn't revoke mcpi's old record either). The argument is first normalized into a job name; recording / revoking / --status all go by job name."""
    assert J.main(arg, env.D) == 0
    assert list(env.recorded()) == ["zzjob"]
    capsys.readouterr()
    assert J.status_main(arg) == 0 and "a: verified" in capsys.readouterr().out
    bad = json.loads(json.dumps(DUMP))
    bad["wires"][1]["to"] = []
    (env.D / "dumps" / "b.json").write_text(json.dumps({"ok": True, "result": bad}), encoding="utf-8")
    assert J.main(arg, env.D) == 1 and env.recorded() == {} and "in verified.json, revoked the old record of zzjob" in capsys.readouterr().out


@pytest.mark.parametrize("arg", ["/abs/elsewhere/jobs/zzjob.py", "other/zzjob.py", "../zzjob.py"])
def test_job_check_refuses_a_job_path_outside_jobs(env, capsys, arg):
    assert J.main(arg, env.D) == 2 and J.status_main(arg) == 2 and not env.V.exists()
    assert "directly under jobs/" in capsys.readouterr().out


@pytest.mark.parametrize("arg", ["zzjob.py.py", "jobs/zzjob.py.py", "jobs/zzjob .py", "old.zzjob", "jobs/zzjob", "zzjob.PY"])
def test_job_check_refuses_a_name_that_is_not_a_job_name(env, capsys, arg):
    """`job_check zzjob.py.py` first extracts "zzjob.py", load_job extracts once more and loads jobs/zzjob.py, and a passing report gets recorded under
    the garbage key "zzjob.py" (hook-protected, the agent can't clean it up); likewise `old.zzjob` without .py (jobs/old/zzjob.py). The name must be a valid job name."""
    assert J.main(arg, env.D) == 2 and J.status_main(arg) == 2 and not env.V.exists()
    assert "is invalid" in capsys.readouterr().out


def test_not_confirmed_built_or_unreadable_files_are_not_verified(env, capsys):
    """exec_state is an error text (vi_info errored) but wasn't counted as failure; dump / report is not valid JSON. All must give exit code 2, one sentence, not recorded."""
    env.rep["vis"]["b"]["exec_state"] = "TimeoutError: vi_info"
    (env.D / "dumps" / "b.json").unlink()
    env.write()
    assert env.run() == 2 and "build not confirmed" in capsys.readouterr().out and not env.V.exists()
    env.rep["vis"]["b"]["exec_state"] = "idle"
    env.write()
    (env.D / "dumps" / "b.json").write_text("garbage{", encoding="utf-8")
    assert env.run() == 2 and "b.json can't be read" in capsys.readouterr().out and not env.V.exists()
    (env.D / "job_report.json").write_text('{"job": "zzjob", "vis": {"a"', encoding="utf-8")
    assert env.run() == 2 and "job_report.json can't be read" in capsys.readouterr().out and not env.V.exists()


def test_wire_check_gap_does_not_revoke_old_record(env):
    """A check that wasn't done is not counter-evidence, the old record stays; only a wire mismatch revokes."""
    assert env.run() == 0
    (env.D / "dumps" / "b.json").unlink()
    assert env.run() == 2 and "zzjob" in env.recorded()
    bad = json.loads(json.dumps(DUMP))
    bad["wires"][1]["to"] = []
    (env.D / "dumps" / "b.json").write_text(json.dumps({"ok": True, "result": bad}), encoding="utf-8")
    assert env.run() == 1 and "zzjob" not in env.recorded()


def test_incomplete_round_with_all_vis_cached_is_not_recorded(env, capsys):
    """Every VI is cached (dumps complete, wires all correct) but this round didn't finish -- must not be recorded as verified.
    This guards the "didn't finish" gate in job_check itself: remove it and this turns red (the words "did not finish" are also printed in the summary line, so the last line and verified.json are asserted)."""
    r = json.loads(json.dumps(env.rep))
    r.update(complete=False, rebuilt=[])
    for x in r["vis"].values():
        x["create"] = "cached"
    env.write(r)
    assert env.run() == 2 and not env.V.exists()
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("==== the LabVIEW machine didn't finish this round")


@pytest.mark.parametrize("how", ["no_job_key", "vis_value_str", "refused_row_short", "run_is_str", "dump_result_null", "dump_nodes_null"])
def test_well_formed_json_with_wrong_shape_says_why(env, capsys, how):
    """Valid JSON but the wrong shape: one sentence, exit code 2 (not 1 = "on-site failure"), verified.json not written."""
    r = json.loads(json.dumps(env.rep))
    if how == "no_job_key":
        del r["job"]
    elif how == "vis_value_str":
        r["vis"]["a"] = "ok"
    elif how == "refused_row_short":
        r["refused"] = [["a", "w"]]
    elif how == "run_is_str":
        r["vis"]["b"]["runs"] = ["ok"]
    else:
        d = {"ok": True, "result": None} if how == "dump_result_null" else {"ok": True, "result": dict(DUMP, nodes=None)}
        (env.D / "dumps" / "a.json").write_text(json.dumps(d), encoding="utf-8")
    env.write(r)
    assert env.run() == 2 and not env.V.exists()
    assert "wrong shape" in capsys.readouterr().out


def test_job_file_that_crashes_on_load_says_why(tmp_path):
    """The job file has a syntax error / raises when executed: job_check and --status both give one sentence, exit code 2, no traceback."""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "__init__.py").write_text("", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(REPO)]), "PYTHONIOENCODING": "utf-8"}
    for src in ("VIS = [\n", "raise RuntimeError('broken')\n", "import numpyzz_missing\n", "import sys\nsys.exit(3)\n"):   # the last two: the job imports a module that can't be found (not "no such job"), sys.exit
        (jobs / "zzbad.py").write_text(src, encoding="utf-8")
        for args in (["zzbad", "--dir", str(tmp_path)], ["--status", "zzbad"]):
            r = subprocess.run([sys.executable, "-m", "jacq.job_check", *args], env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
            assert r.returncode == 2 and "failed to execute" in r.stdout and "no job" not in r.stdout and "Traceback" not in r.stdout + r.stderr, (src, args, r.stdout, r.stderr)
        r = subprocess.run([sys.executable, "-m", "jacq.lint", "zzbad", "--offline"], env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 1 and "IMPORT" in r.stdout and "Traceback" not in r.stdout + r.stderr, (src, r.stdout, r.stderr)


def test_job_check_cli_mistakes_say_why(tmp_path):
    """--dir without a value, a nonexistent job name (with or without --status): all give one sentence, exit code 2, no traceback."""
    run = lambda *a: subprocess.run([sys.executable, "-m", "jacq.job_check", *a], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    for args in (("mcpi", "--dir"), ("no_such_job_zz", "--dir", str(tmp_path)), ("--status", "no_such_job_zz")):
        r = run(*args)
        assert r.returncode == 2 and "Traceback" not in r.stderr, (args, r.stdout, r.stderr)
        assert ("no job" in r.stdout) == ("no_such_job_zz" in args), (args, r.stdout)   # no such file ≠ file broken (that one says "failed to execute")


def test_build_only_report_is_read_from_given_dir(env, capsys, monkeypatch, tmp_path):
    """BUILD_ONLY (probe batch) reports are also read from the directory given by --dir / jacq.run --dest, without silently switching back to the one in private/lab_corpus.
    Put a report with a different marker in each of the two directories, and see which one appears in the table."""
    from jacq import oracle
    monkeypatch.setattr(oracle, "load_job_arg", lambda arg: (arg, env.job))
    monkeypatch.setattr(oracle, "lint_job", lambda job, offline=False: [])   # only looks at which report is read; lint isn't run (so the probe queue isn't touched)
    mark = lambda m: dict(env.rep, build_only=True, vis=dict(env.rep["vis"], b={"create": False, "error": m}))
    default = tmp_path / "default_dir"
    default.mkdir()
    (default / "job_report.json").write_text(json.dumps(mark("MARK_DEFAULT_DIR")), encoding="utf-8")
    monkeypatch.setattr(oracle, "job_local_dir", lambda name: default)
    env.write(mark("MARK_GIVEN_DIR"))
    assert env.run() == 0 and not env.V.exists()
    out = capsys.readouterr().out
    assert "MARK_GIVEN_DIR" in out and "MARK_DEFAULT_DIR" not in out, out


def test_oracle_bug_is_not_reported_as_bad_report_shape(env, monkeypatch):
    """The step handing BUILD_ONLY over to oracle is not inside the "report has the wrong shape" except -- oracle's own KeyError must surface, not be passed off as a broken copy."""
    from jacq import oracle

    def boom(name, D=None):
        raise KeyError("oracle internal bug")
    monkeypatch.setattr(oracle, "job_main", boom)
    env.write(dict(env.rep, build_only=True))
    with pytest.raises(KeyError):
        env.run()


def test_partial_incomplete_missing_and_fake_reports_say_why(env, capsys):
    """--only, didn't finish, no report, FAKE dry run: each gets one human-readable sentence, none is recorded as verified."""
    r = json.loads(json.dumps(env.rep))
    del r["vis"]["b"]
    env.write(r)
    assert env.run() == 2 and "only ran some of the VIs" in capsys.readouterr().out
    env.write(dict(r, complete=False))
    assert env.run() == 2 and "didn't finish" in capsys.readouterr().out
    env.write(dict(env.rep, fake=True))
    assert env.run() == 2 and "LVAGENT_FAKE" in capsys.readouterr().out
    env.write(dict(env.rep, refused=[["a", "w", "function 'VISA Write' is a hardware I/O or external-process node"]], failed=True))
    assert env.run() == 1 and "rejected" in capsys.readouterr().out
    (env.D / "job_report.json").unlink()
    assert env.run() == 2 and "==== no " in capsys.readouterr().out
    assert not env.V.exists()


def test_lint_expect_job_is_not_recorded(env, capsys):
    """A rehearsal job that downgraded lint's E's doesn't go into verified.json even when it passes on-site."""
    env.job.LINT_EXPECT = {"a": ["TYPE"]}
    assert env.run() == 0 and not env.V.exists() and "LINT_EXPECT" in capsys.readouterr().out
    assert "rehearsal jobs with LINT_EXPECT pass but are not recorded" in J.__doc__          # this meaning of exit code 0 is written in the usage


# ---------------------------------------------------------------- lab/jobguard.py
VISA = {"controls": [{"name": "cmd", "type": "string"}], "constants": [{"name": "port", "type": "string", "value": "COM3"}],
        "structures": [{"name": "C", "type": "case", "cases": [{"value": True, "functions": [{"name": "w", "function": "VISA Write"}]}]}],
        "wires": [["port", "w.VISA resource name"], ["cmd", "w.write buffer"]]}


def test_guard_scan_hardware_and_foreign_subvis():
    """Hardware I/O / network / external code / dynamic-call functions (nested in structures too); subVIs are a whitelist -- this job's directory, or registered VIs under vi.lib of the LabVIEW installation directory."""
    out = r"D:\agent_sandbox\lv_agentic\write2\jobs\j"
    assert [n for n, _ in GUARD.scan_spec(VISA, out)] == ["w"]
    sub = lambda p: {"subvis": [{"name": "s", "path": p}]}
    ok = [out + r"\sub.vi", "D:/agent_sandbox/lv_agentic/write2/jobs/J/Sub.vi", out + r"\parse_serial_frame.vi", out + r"\visa_like\deserialize.vi",
          r"C:\Program Files\National Instruments\LabVIEW 2024\vi.lib\Analysis\1siggen.llb\Uniform White Noise.vi",
          r"C:\Program Files (x86)\National Instruments\LabVIEW 2020\vi.lib\analysis\1siggen.llb\uniform white noise.vi"]
    bad = [r"D:\agent_sandbox\lv_agentic\lab_copy\grp\prog.vi", out + r"\..\..\..\lab_copy\grp\prog.vi", out + "2\\sub.vi",
           r"D:\some_group_dir\vi.lib\inc.vi",                                                    # any directory that happens to be called vi.lib
           r"D:\grp\vi.lib\Analysis\1siggen.llb\Uniform White Noise.vi",                          # the relative path is in the whitelist, but not under the installation directory
           r"C:\Program Files\National Instruments\LabVIEW 2024\vi.lib\Utility\tcp.llb\TCP Listen.vi",   # real vi.lib, but not registered
           r"C:\Program Files\National Instruments\LabVIEW 2024\vi.lib\Utility\file.llb\Open URL in Default Browser.vi",
           r"C:\Program Files\NI\LVAddons\nidaqmx\1\vi.lib\DAQmx\write.llb\DAQmx Write.vi",
           r"C:\Program Files\National Instruments\LabVIEW 2024\vi.lib\Instr\_visa.llb\VISA Configure Serial Port (Instr).vi",
           r"C:\Program Files\National Instruments\LabVIEW 2024\vi.lib\Platform\system.llb\System Exec.vi"]
    assert all(not GUARD.scan_spec(sub(p), out) for p in ok), [p for p in ok if GUARD.scan_spec(sub(p), out)]
    assert all(GUARD.scan_spec(sub(p), out) for p in bad), [p for p in bad if not GUARD.scan_spec(sub(p), out)]
    fn = lambda f: GUARD.scan_spec({"functions": [{"name": "f", "function": f}]}, out)
    for f in ("Search 1D Array", "Cancel", "Serialize", "Select", "In Range and Coerce", "Write Delimited Spreadsheet", "Open/Create/Replace File"):
        assert not fn(f), f                                                                         # must not hit innocent ones
    for f in ("VISA Write", "visa read", "VISA写入", "TCP打开连接", "DAQmx Write", "DAQ Assistant", "System Exec", "Call Library Function Node",
              "Python Node", "Open VI Reference", "Call By Reference", "Start Asynchronous Call", "Automation Open", "Open URL in Default Browser"):
        assert fn(f), f
    # VI Server class nodes (Run VI of the VI class etc.) don't go through jobs; property / invoke nodes linked to front-panel controls are fine
    assert GUARD.scan_spec({"invoke_nodes": [{"name": "m", "class": "VI", "method": "Run VI"}]}, out)
    assert GUARD.scan_spec({"property_nodes": [{"name": "p", "class": "Application", "properties": ["App.Version"]}]}, out)
    assert not GUARD.scan_spec({"property_nodes": [{"name": "p", "control": "x", "properties": ["Value"]}],
                                "invoke_nodes": [{"name": "m", "control": "x", "method": "Reinitialize To Default"}]}, out)


def test_vilib_allowlist_matches_catalog():
    """vi.lib subVIs with terminals registered in the catalog (lint accepts them) must also be in jobguard's whitelist (the LabVIEW machine accepts them), and vice versa: the two lists must not disagree."""
    conf = json.loads((REPO / "catalog" / "terminals_confirmed.json").read_text(encoding="utf-8"))
    rel = set()
    for name, path in conf["vilib_paths"].items():
        assert GUARD.subvi_ok(path, r"D:\x\write2\jobs\j"), path
        rel.add(path.replace("/", "\\").lower().split("\\vi.lib\\", 1)[1])
    assert rel == GUARD.VILIB_ALLOW and set(conf["vilib_paths"]) == set(conf["subvis"])


def test_spec_hash_is_the_field_algorithm():
    """spec_hash is the key of the LabVIEW machine's cache and of verified.json: if the algorithm changes, all on-site records become invalid.
    Pinned to the values recorded on-site for jobs/mcpi.py (copied here because jobs/verified.json is local, not in the repo)."""
    from jacq.lint import load_job
    pinned = {"randu": "ed8f3629c8d69c93474aac1ff3a917535c3e7dd90b941ab3046de34365e7172d",
              "hits": "0a2315edbcebc2a03d7df5728afdfc0b75c6ddffbb787083da0b2555f7319215",
              "mc_pi": "236c8ed641bcf02e8c3b82af6c7c404245659433a2e1bbe7affa3f5bc75b6b62"}
    assert {v["name"]: GUARD.spec_hash(v) for v in load_job("mcpi").VIS} == pinned


def test_three_copies_of_the_sandbox_default_agree():
    """The default sandbox path is in three places (jacq/config.py, lab/lablib.py, scripts/_env.sh), and they must agree."""
    from jacq.config import DEFAULT_BASE
    assert f'"LVAGENT_BASE", r"{DEFAULT_BASE}"' in (REPO / "lab" / "lablib.py").read_text(encoding="utf-8")
    assert f"REMOTE_BASE='{DEFAULT_BASE}'" in (REPO / "scripts" / "_env.sh").read_text(encoding="utf-8")


# ---------------------------------------------------------------- lab/vi_job.py (fake LabVIEW)
JOBSRC = '''from lablib import BASE
OUT = BASE / "write2" / "jobs" / "zzfake"
SPEC = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "functions": [{"name": "a", "function": "Increment"}], "wires": [["x", "a.x"], ["a.x+1", "y"]]}
def chk_ok(v): return {"pass": v.get("x") == 1}
def chk_nopass(v): return {"looks": "fine"}
VIS = [{"name": "t", "spec": SPEC, "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]},
       {"name": "r", "spec": dict(SPEC), "runs": {"inputs": {"x": 1}, "check": "%s"}}]
%s
'''


def fake_vi_job(tmp_path, check="chk_ok", extra="", args=(), writer=None):
    (tmp_path / "jobs").mkdir(exist_ok=True)
    (tmp_path / "jobs" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "jobs" / "zzfake.py").write_text(JOBSRC % (check, extra), encoding="utf-8")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    if writer is not None:
        env["LVAGENT_SHLIM"] = str(writer)
    r = subprocess.run([sys.executable, str(REPO / "lab" / "vi_job.py"), "zzfake", *args], env=env, cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    out = tmp_path / "sb" / "lv" / "write2" / "jobs" / "zzfake"
    rep = json.loads((out / "job_report.json").read_text(encoding="utf-8")) if (out / "job_report.json").exists() else None
    return r, rep, out


def test_vi_job_report_binds_to_job_and_marks_fake(tmp_path):
    """The report has the job file hash, each VI's spec hash, the fake marker, the time."""
    r, rep, out = fake_vi_job(tmp_path)
    assert "JOB COMPLETE OK" in r.stdout, r.stdout + r.stderr
    assert rep["fake"] is True and rep["complete"] is True and rep["failed"] is False and rep["time"][:2] == "20"
    assert rep["job_sha"] == GUARD.file_sha(tmp_path / "jobs" / "zzfake.py")
    sys.path.insert(0, str(tmp_path))
    try:
        cache = json.loads((out / ".spec_hash.json").read_text(encoding="utf-8"))
        assert rep["vis"]["t"]["spec_hash"] == cache["t"] and len(cache["t"]) == 64
    finally:
        sys.path.remove(str(tmp_path))


def test_vi_job_check_without_boolean_pass_fails(tmp_path):
    """If check's return value has no boolean pass, it can't count as passed."""
    r, rep, _ = fake_vi_job(tmp_path, check="chk_nopass")
    assert "JOB COMPLETE FAILED" in r.stdout and rep["failed"] is True
    assert rep["vis"]["r"]["runs"][0]["pass"] is False and "pass" in rep["vis"]["r"]["runs"][0]["check_error"]


def test_vi_job_refuses_hardware_job_before_building(tmp_path):
    """The LabVIEW machine scans again itself: for a job containing hardware I/O not a single VI is built."""
    extra = 'VIS.append({"name": "hw", "spec": {"controls": [{"name": "c", "type": "string"}], ' \
            '"functions": [{"name": "w", "function": "VISA Write"}], "wires": [["c", "w.write buffer"]]}})'
    r, rep, out = fake_vi_job(tmp_path, extra=extra)
    assert "JOB COMPLETE REFUSED" in r.stdout and rep["refused"] == [["hw", "w", rep["refused"][0][2]]] and rep["failed"] is True
    assert not list(out.glob("*.vi")) and rep["vis"] == {}


def test_vi_job_anchors_out_on_job_name_not_job_file(tmp_path):
    """The job writes OUT as write2 itself, with subVIs pointing to other directories under write2 (which may hold copies of existing VIs) --
    running vi_job.py directly bypasses lint, the LabVIEW machine must refuse too, and not build a single VI."""
    extra = ('OUT = BASE / "write2"\n'
             'VIS.append({"name": "g", "spec": {"controls": [{"name": "c", "type": "i32"}], '
             '"subvis": [{"name": "s", "path": str(BASE / "write2" / "visa" / "scratch" / "grp.vi")}], "wires": [["c", "s.x"]]}})')
    r, rep, out = fake_vi_job(tmp_path, extra=extra)
    assert "JOB COMPLETE REFUSED" in r.stdout, r.stdout + r.stderr
    nodes = {(x[0], x[1]) for x in rep["refused"]}
    assert ("zzfake", "OUT") in nodes and ("g", "s") in nodes
    assert not list((tmp_path / "sb" / "lv").rglob("*.vi")) and rep["vis"] == {}


def test_vi_job_refuses_bad_job_names_before_touching_disk(tmp_path):
    """When the job name has hyphens / path separators, the LabVIEW machine stops before creating directories and loading the job (same regex as lint's JOB-NAME)."""
    fake_vi_job(tmp_path)   # first a normal run, to create jobs/ and the sandbox
    jobs = tmp_path / "jobs"
    (jobs / "gal-x.py").write_text((jobs / "zzfake.py").read_text(encoding="utf-8"), encoding="utf-8")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    for bad in ("gal-x", "..\\zzfake", "a/b", "zz fake"):
        r = subprocess.run([sys.executable, str(REPO / "lab" / "vi_job.py"), bad], env=env, cwd=tmp_path,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert r.returncode != 0 and "may only contain letters, digits, underscores" in r.stderr, (bad, r.stdout, r.stderr)
    assert sorted(p.name for p in (tmp_path / "sb" / "lv" / "write2" / "jobs").iterdir()) == ["zzfake"]


DEPJOB = ('''SUB = dict(SPEC, functions=[{"name": "a", "function": "%s"}])
def chk_bad(v): return {"pass": False}
VIS[:] = [{"name": "sub", "spec": SUB, "runs": {"inputs": {"x": 1}, "check": "%s"}},
          {"name": "main", "spec": {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
                                    "subvis": [{"name": "s", "path": str(OUT / "sub.vi")}], "wires": [["x", "s.x"], ["s.y", "y"]]},
           "runs": {"inputs": {"x": 1}, "check": "chk_ok"}}]''')


@pytest.mark.parametrize("round2", ["only_sub", "sub_check_fails"])
def test_caller_is_rebuilt_after_its_subvi_was_rebuilt_in_an_earlier_round(tmp_path, round2):
    """In some round the subVI changed spec and was rebuilt, but main which calls it wasn't reached in that round (--only sub, or sub's check failed and fail-fast stopped);
    in the next full round, main must be rebuilt (the subVI's connector may have changed), not reported as cached."""
    r, rep, out = fake_vi_job(tmp_path, extra=DEPJOB % ("Increment", "chk_ok"))
    assert rep["vis"]["main"]["create"] is True, r.stdout + r.stderr
    if round2 == "only_sub":
        r, rep, out = fake_vi_job(tmp_path, extra=DEPJOB % ("Decrement", "chk_ok"), args=("--only", "sub"))
    else:
        r, rep, out = fake_vi_job(tmp_path, extra=DEPJOB % ("Decrement", "chk_bad"))
    assert rep["vis"]["sub"]["create"] is True and "main" not in rep["vis"], rep
    r, rep, out = fake_vi_job(tmp_path, extra=DEPJOB % ("Decrement", "chk_ok"))
    assert rep["vis"]["sub"]["create"] == "cached" and rep["vis"]["main"]["create"] is True, rep
    r, rep, out = fake_vi_job(tmp_path, extra=DEPJOB % ("Decrement", "chk_ok"))   # one more round: nothing changed, everything reused
    assert rep["vis"]["sub"]["create"] == "cached" and rep["vis"]["main"]["create"] == "cached", rep


def test_job_edited_within_the_same_second_is_not_run_from_stale_pyc(tmp_path, monkeypatch):
    """The job file was changed within the same second with unchanged length (Increment → Decrement): the old .pyc, judged by "mtime in whole seconds + size", would be taken as fresh.
    Both the LabVIEW machine and lint load from source (jobguard.load_job), so what runs and what the report records is the new spec. The worst case is constructed here: the old .pyc is present, and the mtime is set back exactly."""
    import py_compile
    r, rep, out = fake_vi_job(tmp_path, extra=DEPJOB % ("Increment", "chk_ok"))
    jf = tmp_path / "jobs" / "zzfake.py"
    py_compile.compile(str(jf), doraise=True)   # .pyc of the old source (invalidated by timestamp by default)
    st = jf.stat()
    old_hash = rep["vis"]["sub"]["spec_hash"]
    jf.write_text(jf.read_text(encoding="utf-8").replace("Increment", "Decrement"), encoding="utf-8")
    assert jf.stat().st_size == st.st_size
    os.utime(jf, ns=(st.st_atime_ns, st.st_mtime_ns))
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(REPO / "lab" / "vi_job.py"), "zzfake"], env=env, cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    rep = json.loads((out / "job_report.json").read_text(encoding="utf-8"))
    assert rep["vis"]["sub"]["spec_hash"] != old_hash and rep["vis"]["sub"]["create"] is True, r.stdout + r.stderr
    assert rep["job_sha"] == GUARD.file_sha(jf)
    # local lint uses the same loading function: load once in this process with the tmp jobs package swapped in (monkeypatch restores sys.path / sys.modules afterwards, not affecting other cases)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(sys.modules, "lablib", types.SimpleNamespace(BASE=Path("x")))
    for k in [k for k in sys.modules if k == "jobs" or k.startswith("jobs.")]:
        monkeypatch.delitem(sys.modules, k)
    try:
        from jacq.lint import load_job
        assert load_job("zzfake").VIS[0]["spec"]["functions"][0]["function"] == "Decrement"
    finally:
        for k in [k for k in sys.modules if k == "jobs" or k.startswith("jobs.")]:
            del sys.modules[k]


def test_load_job_reports_missing_and_broken_job_files_cleanly(tmp_path, monkeypatch):
    """The two sys.modules cleanups of jobguard.load_job --
    (1) loaded once, file deleted afterwards: loading again must give ModuleNotFoundError (from which job_check gives "no job", exit code 2), not FileNotFoundError from reading the file via the last __spec__;
    (2) the job file raises halfway through execution: no half-module is left in sys.modules;
    (3) a job file newly created in the same process within the same second is still found (invalidate_caches); (4) sys.exit() while loading a job file becomes RuntimeError;
    also __job_sha__ is exactly the hash of those bytes."""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "__init__.py").write_text("", encoding="utf-8")
    jf = jobs / "zzload.py"
    monkeypatch.syspath_prepend(str(tmp_path))
    for k in [k for k in sys.modules if k == "jobs" or k.startswith("jobs.")]:
        monkeypatch.delitem(sys.modules, k)
    try:
        jf.write_text("X = 1\n", encoding="utf-8")
        m = GUARD.load_job("zzload")
        assert m.X == 1 and m.__job_sha__ == GUARD.file_sha(jf)
        st = jobs.stat()   # (3) job file newly created within the same second: directory mtime set back to its original value, Python's directory listing cache would think nothing changed
        (jobs / "zznew.py").write_text("X = 3\n", encoding="utf-8")
        os.utime(jobs, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert GUARD.load_job("zznew").X == 3
        jf.unlink()
        with pytest.raises(ModuleNotFoundError):
            GUARD.load_job("zzload")
        jf.write_text("X = 2\nraise RuntimeError('broken')\n", encoding="utf-8")
        with pytest.raises(RuntimeError):
            GUARD.load_job("zzload")
        assert "jobs.zzload" not in sys.modules
        jf.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")   # (4) sys.exit while loading: becomes an ordinary exception, the entry points don't quietly end with that exit code
        with pytest.raises(RuntimeError, match=r"sys.exit\(3\)"):
            GUARD.load_job("zzload")
        assert "jobs.zzload" not in sys.modules
        jf.write_text("raise KeyboardInterrupt\n", encoding="utf-8")   # only SystemExit is converted: Ctrl-C propagates as usual, sys.modules is cleaned all the same
        with pytest.raises(KeyboardInterrupt):
            GUARD.load_job("zzload")
        assert "jobs.zzload" not in sys.modules
    finally:
        for k in [k for k in sys.modules if k == "jobs" or k.startswith("jobs.")]:
            del sys.modules[k]


@pytest.mark.parametrize("bad", ['{"t": "ab', "[]", '"x"', "null"])
def test_truncated_cache_means_rebuild_all_not_crash(tmp_path, bad):
    """.spec_hash.json truncated / broken by hand (can't be read, or what's read isn't a dict) → say so, treat as no cache and rebuild everything, finish as usual
    (sandbox files can only be deleted by the user, it must not block this job forever)."""
    r, rep, out = fake_vi_job(tmp_path)
    (out / ".spec_hash.json").write_text(bad, encoding="utf-8")
    r, rep, out = fake_vi_job(tmp_path)
    assert "JOB COMPLETE OK" in r.stdout and "can't be read" in r.stdout, r.stdout + r.stderr
    assert {n: x["create"] for n, x in rep["vis"].items()} == {"t": True, "r": True}
    json.loads((out / ".spec_hash.json").read_text(encoding="utf-8"))
    assert not list(out.glob("*.part"))


def test_vi_job_deletes_old_report_before_loading_job(tmp_path):
    """When the job file crashes as soon as it's loaded, the previous round's report must not remain on the LabVIEW machine (the remote mode would pull it back and judge it)."""
    r, rep, out = fake_vi_job(tmp_path)
    assert rep["complete"] is True
    r, rep, out = fake_vi_job(tmp_path, extra='raise RuntimeError("job file broken")')
    assert r.returncode != 0 and rep is None and not (out / "job_report.json").exists()


def test_vi_job_killed_after_create_does_not_leave_stale_cache(tmp_path):
    """spec A built → changed to B, killed right after building (exit code 4) → changed back to A: the VI on disk was built from B, must be rebuilt, not reported as cached."""
    r, rep, out = fake_vi_job(tmp_path)
    assert rep["vis"]["t"]["create"] is True
    (tmp_path / "jobs" / "zzfake.py").write_text(JOBSRC % ("chk_ok", 'VIS[0]["spec"] = dict(SPEC, functions=[{"name": "a", "function": "Decrement"}])'), encoding="utf-8")
    drv = tmp_path / "kill_after_create.py"
    drv.write_text("import os, runpy, sys, lablib\n"
                   "orig = lablib.FakeLV.vi_create\n"
                   "def vc(self, *a):\n    orig(self, *a); os._exit(4)\n"
                   "lablib.FakeLV.vi_create = vc\n"
                   f"sys.argv = ['vi_job.py', 'zzfake']\nrunpy.run_path({str(REPO / 'lab' / 'vi_job.py')!r}, run_name='__main__')\n", encoding="utf-8")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": os.pathsep.join([str(tmp_path), str(REPO / "lab")]), "PYTHONIOENCODING": "utf-8"}
    k = subprocess.run([sys.executable, str(drv)], env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert k.returncode == 4, k.stdout + k.stderr
    r, rep, out = fake_vi_job(tmp_path)                       # changed back to A
    assert rep["vis"]["t"]["create"] is True and "t" in rep["rebuilt"], rep


def test_vi_job_writes_partial_report_and_rebuilds_on_writer_change(tmp_path):
    """A report is left even on failure midway; M4: the writer changed, cache invalidated, everything rebuilt."""
    w = tmp_path / "writer"
    (w / "labview_mcp").mkdir(parents=True)
    for f in ("gen.py", "lv.py", "dfir.py"):
        (w / "labview_mcp" / f).write_text("# v1\n", encoding="utf-8")
    r, rep, out = fake_vi_job(tmp_path, writer=w)
    assert rep["writer_sha"] == GUARD.writer_sha(w / "labview_mcp") and rep["rebuilt"] == ["r", "t"]
    r, rep, _ = fake_vi_job(tmp_path, writer=w)
    assert rep["rebuilt"] == [] and rep["vis"]["t"]["create"] == "cached"
    (w / "labview_mcp" / "dfir.py").write_text("# v2\n", encoding="utf-8")   # a change in a module imported by lv.py at runtime also counts as a writer change
    r, rep, _ = fake_vi_job(tmp_path, writer=w, args=("--only", "t"))
    assert "cache invalidated" in r.stdout and rep["rebuilt"] == ["t"]
    r, rep, _ = fake_vi_job(tmp_path, writer=w)          # the previous round only rebuilt t: r's cache was invalidated, it must be rebuilt this round
    assert rep["rebuilt"] == ["r"] and rep["vis"]["t"]["create"] == "cached"


def test_vi_job_dump_carries_spec_hash_and_partial_report_lists_rebuilt(tmp_path):
    """The dump carries this spec version's hash (so job_check won't take the previous version's dump as counter-evidence); the partial report written to disk per VI contains rebuilt."""
    r, rep, out = fake_vi_job(tmp_path)
    for n in ("t", "r"):
        d = json.loads((out / "dumps" / f"{n}.json").read_text(encoding="utf-8"))
        assert d["spec_hash"] == rep["vis"][n]["spec_hash"] and d["ok"] is True
    src = (REPO / "lab" / "vi_job.py").read_text(encoding="utf-8")
    assert src.index('rep["rebuilt"] = sorted(Path(x).stem for x in rebuilt)') < src.index('dump_json(OUT / "job_report.json", rep)   # even when hung')


def test_vi_job_refuses_vi_names_that_leave_the_job_dir(tmp_path):
    """When a VI name contains a path ("../../other/esc"), running vi_job.py directly also refuses everything (same regex as lint's JOB-NAME),
    and not a single .vi is built in the sandbox outside this job's directory."""
    for bad in ("../../other/esc", "..\\..\\other\\esc", "a:b", "trail.", "", "x\ty", "CON", "nul", "Com1.x", "v" * 101):   # the last one: an overlong name (would cause OSError on the LabVIEW machine)
        r, rep, out = fake_vi_job(tmp_path, extra=f'VIS.append({{"name": {bad!r}, "spec": SPEC, "tests": [{{"inputs": {{"x": 1}}, "expect": {{"y": 2}}}}]}})')
        assert "JOB COMPLETE REFUSED" in r.stdout and (bad, "name") in {(x[0], x[1]) for x in rep["refused"]}, (bad, r.stdout + r.stderr)
        assert rep["vis"] == {}
    sb = tmp_path / "sb" / "lv"
    assert [p for p in sb.rglob("*.vi") if out not in p.parents] == []


def test_job_sha_is_the_hash_of_the_bytes_that_were_executed(tmp_path, monkeypatch):
    """The job_sha in the report is the hash of the bytes that were exec'd, not of the file read again afterwards. The job file appends a line to itself while executing:
    the hash from rereading afterwards then doesn't match what was executed. Both the LabVIEW machine's report (vi_job.py) and the local comparison (job_check's _bound / verified_detail) use the __job_sha__ attached by load_job."""
    import hashlib
    extra = 'open(__file__, "a", encoding="utf-8").write("\\n# appended during execution\\n")'
    r, rep, out = fake_vi_job(tmp_path, extra=extra)
    jf = tmp_path / "jobs" / "zzfake.py"
    executed = hashlib.sha256((JOBSRC % ("chk_ok", extra)).encode("utf-8")).hexdigest()
    assert "JOB COMPLETE OK" in r.stdout, r.stdout + r.stderr
    assert rep["job_sha"] == executed != GUARD.file_sha(jf)
    jf.write_text(JOBSRC % ("chk_ok", extra), encoding="utf-8")   # restore the bytes from before execution, then load once more with the local loading function
    monkeypatch.syspath_prepend(str(tmp_path))
    for k in [k for k in sys.modules if k == "jobs" or k.startswith("jobs.")]:
        monkeypatch.delitem(sys.modules, k)
    try:
        from jacq.lint import load_job
        job = load_job("zzfake")
        assert job.__job_sha__ == executed != GUARD.file_sha(jf)       # loading appended another line
        monkeypatch.setattr(J, "_load_verified", lambda: {"zzfake": {"t": {"spec_hash": GUARD.spec_hash(job.VIS[0]), "job_sha": executed}}})
        assert J.verified_detail(job)["t"]["job_file_same"] is True   # compared with the bytes at load time, not with those reread afterwards
        assert J._bound(job, {"job_sha": executed, "vis": {}}, tmp_path) == []
    finally:
        for k in [k for k in sys.modules if k == "jobs" or k.startswith("jobs.")]:
            del sys.modules[k]


def test_vi_job_only_without_a_value_says_so_before_touching_anything(tmp_path):
    """`--only` without a value: one sentence, non-zero, before creating directories and deleting the old report."""
    r, rep, out = fake_vi_job(tmp_path, args=["--only"])
    assert r.returncode != 0 and "--only must be followed by VI names" in r.stderr + r.stdout and "Traceback" not in r.stderr, (r.stdout, r.stderr)
    assert rep is None and not out.exists()


def test_vi_job_refuses_an_only_name_that_is_not_in_the_job(tmp_path):
    """`--only` with a wrong name: say clearly what VIS has, exit non-zero, leave no report."""
    for only in ("nosuch", "t,nosuch"):   # no match at all, partial match (must not let it through as long as one name is right while silently dropping the other)
        r, rep, out = fake_vi_job(tmp_path, extra="", args=["--only", only])
        assert r.returncode != 0 and "--only has VIs that don't exist" in r.stderr + r.stdout and "JOB COMPLETE" not in r.stdout and rep is None, (only, r.stdout, r.stderr)


def test_vi_job_refuses_unknown_or_missing_arguments_before_touching_anything(tmp_path):
    """No arguments, `--onyl t`, extra positional arguments: must not be silently ignored with everything built as usual.
    Refused in one sentence before creating directories and deleting the old report; correct usage works as usual."""
    for args in (["--onyl", "t"], ["extra"], ["--force", "--bogus"], ["--only", "--force"], ["--only", "--only", "t"], ["--only", "--foo"],
                 ["--only", "t", "extra"],
                 ["--only", "t", "--only", "r"]):   # the last one: `--only x --only y` must not silently take only the last
        r, rep, out = fake_vi_job(tmp_path, args=args)
        say = r.stderr + r.stdout
        assert r.returncode != 0 and ("unknown argument" in say or "--only must be followed by" in say or "--only may only be given once" in say), (args, r.stdout, r.stderr)
        assert "Traceback" not in r.stderr and "JOB COMPLETE" not in r.stdout and rep is None and not out.exists(), (args, r.stdout, r.stderr)
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb2" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb2"),
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(REPO / "lab" / "vi_job.py")], env=env, cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)   # no arguments at all
    assert r.returncode != 0 and "Usage" in r.stderr + r.stdout and "Traceback" not in r.stderr and not (tmp_path / "sb2").exists(), (r.stdout, r.stderr)
    r, rep, out = fake_vi_job(tmp_path, args=["--force", "--only", "t"])
    assert "JOB COMPLETE OK" in r.stdout and rep is not None, (r.stdout, r.stderr)


def test_only_values_must_be_usable_vi_names_and_survive_ssh_quoting():
    """The value of `--only` goes as-is into the remote PowerShell command built by vi_job.sh → lab_run.sh (each argument only wrapped in double quotes): quotes, `$`, backticks would break it;
    spellings that can't be VI names (empty, path characters, control characters, leading/trailing spaces or dots) couldn't produce files anyway. The local machine refuses in one sentence before the LabVIEW machine run; valid names work as usual."""
    for v in ("", "a,,b", "a'b", "a$b", "a`b", 'a"b', "a/b", "a\\b", "a b ", " a", "a.", "a\nb", "con", "a;b" + "x" * 101):
        with pytest.raises(SystemExit) as e:
            GUARD.parse_job_args(["j", "--only", v])
        assert "not a usable VI name" in str(e.value), (v, str(e.value))
    assert GUARD.parse_job_args(["j", "--only", "a,B_1,c d.e", "--force"]) == ("j", True, {"a", "B_1", "c d.e"})


def test_vi_job_force_rebuilds_and_only_takes_several_names(tmp_path):
    """The success path of parse_job_args: `--force` really triggers a full rebuild (vi_job.sh relies on appending it to rebuild when the writer changed), `--only a,b` does these two and not the third, regardless of order."""
    extra = 'VIS.append({"name": "u", "spec": dict(SPEC), "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]})'
    seen = []
    for args in ([], [], ["--force"], ["--force", "--only", "t,u"], ["--only", "u,t"]):
        r, rep, _ = fake_vi_job(tmp_path, extra=extra, args=args)   # same sandbox, cache kept
        assert "JOB COMPLETE OK" in r.stdout, (args, r.stdout, r.stderr)
        seen.append((sorted(rep["rebuilt"]), sorted(rep["vis"])))
    assert seen == [(["r", "t", "u"], ["r", "t", "u"]),   # first time: all three built
                    ([], ["r", "t", "u"]),               # unchanged: all from cache
                    (["r", "t", "u"], ["r", "t", "u"]),  # --force: rebuilt even with a cache
                    (["t", "u"], ["t", "u"]),            # --force --only t,u: only these two
                    ([], ["t", "u"])], seen              # --only u,t: cache hits, only these two appear


def test_vi_job_runsnap_goes_through_must_runnable(tmp_path):
    """runsnap calls the writer's vi_run directly, bypassing lablib.LV.run -- a VI name carrying a path that escapes the runnable directories must be stopped here.
    VI names are now refused before work starts (the previous case); here that gate is switched off to test runsnap, the second one, on its own."""
    (tmp_path / "jobs").mkdir(exist_ok=True)
    (tmp_path / "jobs" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "jobs" / "zzfake.py").write_text(JOBSRC % ("chk_ok", 'VIS[:] = [{"name": "../../../esc", "spec": SPEC, "runsnap": {"inputs": {"x": 1}}}]'),
                                                  encoding="utf-8")
    drv = tmp_path / "no_name_gate.py"
    drv.write_text("import re, runpy, sys, jobguard\n"
                   "jobguard.BAD_VI_NAME = re.compile(r'(?!)')\n"
                   f"sys.argv = ['vi_job.py', 'zzfake']\nrunpy.run_path({str(REPO / 'lab' / 'vi_job.py')!r}, run_name='__main__')\n", encoding="utf-8")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": os.pathsep.join([str(tmp_path), str(REPO / "lab")]), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(drv)], env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode != 0 and "Refusing to run" in r.stderr and "JOB COMPLETE" not in r.stdout, r.stdout + r.stderr


def test_must_runnable_needs_sandbox_too(tmp_path):
    """When LVAGENT_BASE is set outside the sandbox, VIs under write2 may not run either."""
    code = ("import lablib\n"
            "lablib.must_runnable(lablib.BASE / 'write2' / 'jobs' / 'j' / 'x.vi')\n")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_SANDBOX": str(tmp_path / "sb"), "PYTHONPATH": str(REPO / "lab")}
    ok = subprocess.run([sys.executable, "-c", code], env={**env, "LVAGENT_BASE": str(tmp_path / "sb" / "lv")}, capture_output=True, text=True)
    bad = subprocess.run([sys.executable, "-c", code], env={**env, "LVAGENT_BASE": str(tmp_path / "elsewhere")}, capture_output=True, text=True)
    assert ok.returncode == 0 and bad.returncode != 0 and "not inside the sandbox" in bad.stderr


# ---------------------------------------------------------------- jacq.run (the one command of the same-machine mode)
def run_local(tmp_path, *args, **envx):
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"), **envx}
    return subprocess.run([sys.executable, "-m", "jacq.run", *args], env=env, cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=180)


def test_jacq_run_fake_never_touches_real_results(tmp_path):
    """One command lint → vi_job → copy back → job_check; results from the fake LabVIEW may not enter private/lab_corpus, and are not recorded as verified."""
    live = REPO / "jobs" / "verified.json"   # local record (gitignored): may not exist in a fresh clone, and then must not be created
    before = live.read_bytes() if live.exists() else None
    r = run_local(tmp_path, "gallery")
    assert r.returncode == 2 and "--dest" in r.stdout
    r = run_local(tmp_path, "gallery", "--dest", str(REPO / "private" / "lab_corpus" / "x"))
    assert r.returncode == 2 and "--dest" in r.stdout
    dest = tmp_path / "res"
    r = run_local(tmp_path, "gallery", "--dest", str(dest))
    assert "JOB COMPLETE" in r.stdout and (dest / "job_report.json").exists() and (dest / ".spec_hash.json").exists()
    assert r.returncode == 2 and "comes from an LVAGENT_FAKE=1 dry run" in r.stdout
    assert (live.read_bytes() if live.exists() else None) == before


def _stub_run(monkeypatch, tmp_path, lint_rc=0, stdout="JOB COMPLETE OK\n", rc=0, write_report=True):
    """Runs jacq.run.main in-process, with lint / the lab subprocess / job_check replaced by stubs, recording whether each was called."""
    from jacq import run as RUN
    calls = {"lint_offline_env": None, "sub": 0, "check": 0}
    src = tmp_path / "sb" / "lv" / "write2" / "jobs" / "zz"
    monkeypatch.setenv("LVAGENT_BASE", str(tmp_path / "sb" / "lv"))
    monkeypatch.delenv("LVAGENT_FAKE", raising=False)

    def fake_lint(argv):
        calls["lint_offline_env"] = os.environ.get("JACQ_OFFLINE")
        calls["lint_argv"] = argv
        return lint_rc

    def fake_sub(cmd, **kw):
        calls["sub"] += 1
        calls["sub_cmd"] = cmd
        if write_report:
            src.mkdir(parents=True, exist_ok=True)
            (src / "job_report.json").write_text("{}", encoding="utf-8")
        return types.SimpleNamespace(stdout=stdout, stderr="", returncode=rc)

    def fake_check(job, dest):
        calls["check"] += 1
        calls["check_job"] = job
        return 0
    monkeypatch.setattr(RUN.lint, "main", fake_lint)
    monkeypatch.setattr(RUN.subprocess, "run", fake_sub)
    monkeypatch.setattr(RUN.job_check, "main", fake_check)
    return RUN, calls, src


def test_jacq_run_stops_at_lint_errors(tmp_path, monkeypatch, capsys):
    """The same-machine mode also has the "E means no LabVIEW machine run" gate; this lint pass is always in strict mode, JACQ_OFFLINE=1 in the environment has no effect."""
    monkeypatch.setenv("JACQ_OFFLINE", "1")
    RUN, calls, _ = _stub_run(monkeypatch, tmp_path, lint_rc=1)
    assert RUN.main(["zz", "--dest", str(tmp_path / "res")]) == 1 and "not run on the LabVIEW machine" in capsys.readouterr().out
    assert {k: calls[k] for k in ("lint_offline_env", "sub", "check")} == {"lint_offline_env": None, "sub": 0, "check": 0}


_JOB_ARGS = ("jobs/zz.py", "zz.py", "zz", "./jobs/zz.py", str(REPO / "jobs" / "zz.py"))   # valid spellings: all are the job name zz


def test_job_given_as_a_path_reaches_the_lab_as_a_job_name(tmp_path, monkeypatch):
    """lint accepts jobs/<job>.py; jacq.run / vi_job.sh must not pass it as-is to the LabVIEW machine (lint 0 E yet refused to run).
    jacq.run uses lint's job_name, so lint, the LabVIEW machine and job_check receive the same name."""
    from jacq.lint import job_name
    assert [job_name(a) for a in _JOB_ARGS] == ["zz"] * 5
    for arg in _JOB_ARGS:
        RUN, calls, _ = _stub_run(monkeypatch, tmp_path)
        RUN.main([arg, "--dest", str(tmp_path / "res")])
        want = job_name(arg)
        assert calls["lint_argv"] == [want] and calls["sub_cmd"][2] == want and calls.get("check_job", want) == want, (arg, calls)


_BAD_JOB_ARGS = ("/abs/elsewhere/jobs/zz.py", "other/zz.py", "../zz.py", "jobs/old/zz.py",   # .py paths not directly under jobs/
                 "zz.py.py", "jobs/zz .py",                                                   # the name extracted from the .py path is invalid
                 "jobs/zz", "zz.py/", "zz.PY", "old.zz")   # not ending in .py: bash takes it as-is as the job name, the name has dots or slashes, only lint can refuse it


def test_job_path_outside_jobs_is_refused_by_every_entry(tmp_path, monkeypatch, capsys):
    """`elsewhere/gallery.py` must not be silently linted as jobs/gallery.py: lint (E JOB-ARG), jacq.run (not run on the LabVIEW machine),
    vi_job.sh (the pass handed to lint carries the path as-is) all refuse."""
    for arg in _BAD_JOB_ARGS:   # first go through the real lint for all of them: _stub_run replaces jacq.lint.main with a stub
        with pytest.raises(L.JobArgError):
            L.job_name(arg)
        assert L.main([arg, "--offline"]) == 1 and "JOB-ARG" in capsys.readouterr().out, arg
    for arg in _BAD_JOB_ARGS:
        RUN, calls, _ = _stub_run(monkeypatch, tmp_path)
        assert RUN.main([arg, "--dest", str(tmp_path / "res")]) == 1 and calls["sub"] == 0 and calls["check"] == 0, arg
        assert "not run on the LabVIEW machine" in capsys.readouterr().out


def test_vi_job_sh_hands_the_original_argument_to_lint(tmp_path):
    """vi_job.sh (remote mode): really runs the "extract job name → lint" lines of the script (syncing the writer and the LabVIEW machine run afterwards are not run; can't be put together with the cases that stub subprocess.run).
    lint receives the original argument, not the file name extracted by the script itself; when lint refuses, the script doesn't go on."""
    for arg in _BAD_JOB_ARGS:
        r = _vi_job_sh_until_lint(arg, sys.executable)
        assert r.returncode == 1 and "JOB-ARG" in r.stdout and "not run on the LabVIEW machine" in r.stdout and "REACHED_SYNC" not in r.stdout, (arg, r.stdout, r.stderr)
    stub = tmp_path / "pystub.sh"   # started with `sh stub`, not relying on the executable bit (the temp directory may be noexec)
    stub.write_text('printf "%s\\n" "$@" > "$REC"\nexit 1\n', encoding="utf-8")
    for arg in _JOB_ARGS + _BAD_JOB_ARGS:   # lint receives the original argument, not the file name extracted by the script itself
        r = _vi_job_sh_until_lint(arg, f"sh {stub}", rec=tmp_path / "argv.txt")
        assert (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines() == ["-m", "jacq.lint", arg], (arg, r.stdout, r.stderr)


def _vi_job_sh_until_lint(arg, py, rec="", end="-m jacq.lint", extra=()):
    """Runs scripts/vi_job.sh from ARG="$1" to the line containing `end` (lint by default, can also be the command-line argument check); on passing, REACHED_SYNC is printed next
    (the real script syncs the writer and runs on the LabVIEW machine after that, not run here). extra = the arguments after the job name on the command line."""
    lines = (REPO / "scripts" / "vi_job.sh").read_text(encoding="utf-8").splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith('ARG="$1"'))
    j = next(k for k, l in enumerate(lines) if end in l)
    code = "set -u\nT0=0\n" + "\n".join(lines[i:j + 1]) + "\necho REACHED_SYNC $JOB\n"
    return subprocess.run(["bash", "-c", code, "x", arg, *extra], cwd=REPO, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "PY": py, "REC": str(rec), "REPO": str(REPO)})


def test_job_path_relative_to_the_repo_root_is_accepted_from_any_directory(tmp_path, monkeypatch):
    """job_name's relative paths are "resolved against the current directory or the repo root": jobs/zz.py written from another directory is accepted too (otherwise calling jacq.lint / the hook elsewhere would be wrongly refused)."""
    from jacq.lint import job_name
    monkeypatch.chdir(tmp_path)
    assert job_name("jobs/zz.py") == "zz" and job_name("./jobs/zz.py") == "zz"


def test_every_command_line_entry_that_reads_a_job_normalises_the_argument(tmp_path, capsys):
    """When jacq.oracle or jacq.wire_diff receive jobs/typeprobe.py or x.py from elsewhere, they must not use it as-is to compute directories, nor throw a JobArgError traceback.
    Command-line entry points that read jobs all go through lint.load_job_arg (jacq.lint turns the error into an E itself, jacq.run see the previous case)."""
    from jacq import oracle
    D = tmp_path / "nores"
    for arg in ("typeprobe", "jobs/typeprobe.py", "./jobs/typeprobe.py"):
        assert oracle.job_main(arg, D) == 2
        out = capsys.readouterr().out
        assert "scripts/vi_job.sh typeprobe;" in out and "python -m jacq.run typeprobe)\n" in out and "jobs/typeprobe.py" not in out, (arg, out)
    for arg in ("other/typeprobe.py", "typeprobe.py.py", "old.typeprobe"):
        assert oracle.job_main(arg, D) == 2 and "====" in capsys.readouterr().out, arg
        assert oracle.job_main(arg) == 2 and "====" in capsys.readouterr().out, arg   # no result directory given: must not reach job_local_dir(None)
    r = subprocess.run([sys.executable, "-m", "jacq.oracle", "other/typeprobe.py"], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 2 and "is not in" in r.stdout and "Traceback" not in r.stderr, (r.stdout, r.stderr)
    wd = lambda *a: subprocess.run([sys.executable, "-m", "jacq.wire_diff", *a], cwd=REPO, capture_output=True, text=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    for a, say in ((("other/gallery.py", "x"), "is not in"), (("gallery.py.py", "x"), "is invalid"), (("jobs/gallery.py", "nosuch"), "gallery has no VI"),
                   (("gallery",), "usage"), (("gallery", "x", "extra"), "usage")):   # missing argument, extra argument
        r = wd(*a)
        assert r.returncode == 2 and say in r.stdout and "Traceback" not in r.stderr, (a, r.stdout, r.stderr)


def test_no_entry_loads_a_job_without_going_through_load_job_arg():
    """Command-line entry points that read jobs all go through lint.load_job_arg (normalize into a job name + state the error clearly): anything in jacq/, hooks/, readers/ other than lint.py that calls
    `load_job(…)` directly is an entry point that missed the funnel -- enumerate them, so a new entry point that misses the funnel turns red here.
    Scope: calls to `load_job` matched by name (attribute forms like `jobguard.load_job(…)` count too); the LabVIEW machine's lab/ goes through jobguard and is not included;
    workarounds like `import … as alias` or loading jobs.<name> directly via importlib can't be caught by name -- this doesn't pretend to catch them."""
    import ast
    bad = []
    for p in sorted(f for d in ("jacq", "hooks", "readers") for f in (REPO / d).rglob("*.py")):
        if p.name == "lint.py" and p.parent.name == "jacq":
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "load_job":
                bad.append(f"{p.relative_to(REPO)}:{n.lineno}")
    assert not bad, f"these places call load_job directly, use lint.load_job_arg instead: {bad}"


def test_status_flag_must_come_first_or_the_cli_refuses(env, capsys):
    """`job_check zzjob --status` must not be taken as a verdict that writes the hook-protected verified.json (--status is read-only)."""
    assert J.cli(["zzjob", "--status", "--dir", str(env.D)]) == 2 and not env.V.exists()
    assert "Usage" in capsys.readouterr().out
    assert J.cli(["--status", "zzjob"]) == 0 and not env.V.exists()


@pytest.mark.parametrize("a", [["zzjob", "--Status"], ["zzjob", "--dirr", "X"], ["zzjob", "extra"], ["zzjob", "--dir"], ["--dir", "X", "zzjob"],
                               ["zzjob", "--status"], ["--status"], ["--status", "zzjob", "x"], ["zzjob", "--dir", "X", "extra"], []])
def test_job_check_cli_accepts_only_its_three_forms(env, capsys, a):
    """`job_check zzjob --dirr X`, `extra`, `--Status` must not silently judge and record from the default directory
    (what's judged isn't the X the user thinks). The command line only accepts `<job>`, `<job> --dir D`, `--status <job>`; everything else gets the usage, exit code 2, nothing recorded."""
    assert J.cli(a) == 2 and not env.V.exists()
    assert "Usage" in capsys.readouterr().out


def test_job_check_cli_still_takes_the_three_forms(env, tmp_path, monkeypatch):
    assert J.cli(["--status", "zzjob"]) == 0 and not env.V.exists()
    monkeypatch.setattr(J, "job_local_dir", lambda name: env.D)   # there's a good report in the default directory: an empty --dir falling back to it would judge and write verified.json
    shutil.copytree(env.D, tmp_path / "--x")                      # there really is a directory called --x in the current directory: taking --x as the value of --dir would judge it
    monkeypatch.chdir(tmp_path)
    for bad in ("", " ", "--x"):   # an empty --dir must not fall back to the default directory and judge and write verified.json as usual
        assert J.cli(["zzjob", "--dir", bad]) == 2 and not env.V.exists(), bad
    assert J.cli(["zzjob", "--dir", str(env.D)]) == 0 and env.V.exists()


def test_job_without_a_usable_vis_is_one_sentence_not_a_traceback(monkeypatch, capsys):
    """When `jobs/x.py` is an empty file / only contains `X = 1` (or jobs/__init__.py), `job_check --status`, `wire_diff`, `oracle`
    must not throw an AttributeError traceback; lint gives E JOB for them. Entry points that read jobs all iterate over VIS, load_job_arg checks this uniformly after loading."""
    from jacq import oracle
    for bad in (types.SimpleNamespace(X=1), types.SimpleNamespace(VIS=3), types.SimpleNamespace(VIS=[{"name": "a"}]), types.SimpleNamespace(VIS=[{"spec": {}}]),
                types.SimpleNamespace(VIS=[1])):   # missing spec / missing name / not a dict
        monkeypatch.setattr(L, "load_job", lambda n, bad=bad: bad)
        assert L.load_job_arg("zzjob") == (None, None) and "has no usable VIS" in capsys.readouterr().out
        assert J.cli(["--status", "zzjob"]) == 2 and oracle.job_main("zzjob") == 2
        assert capsys.readouterr().out.count("has no usable VIS") == 2
    monkeypatch.setattr(L, "load_job", lambda n: types.SimpleNamespace(VIS=[]))
    assert L.load_job_arg("zzjob")[0] == "zzjob"
    for argv in (["-m", "jacq.job_check", "--status", "__init__"], ["-m", "jacq.wire_diff", "__init__", "x"]):   # the real jobs/__init__.py: loadable, no VIS
        r = subprocess.run([sys.executable, *argv], cwd=REPO, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        assert r.returncode == 2 and "has no usable VIS" in r.stdout and "Traceback" not in r.stderr, (argv, r.stdout, r.stderr)


def test_results_dir_is_computed_from_the_normalised_job_name(tmp_path, monkeypatch, capsys):
    """After normalizing into a job name, the normalized name must be used to compute the result directory: `jobs/typeprobe.py` must not be spliced into …/jobs/jobs/typeprobe.py/.
    (the failure path only shows "an error was reported", not which directory it was about; here no D is given, and what's passed to job_local_dir is checked directly.)"""
    import runpy
    from jacq import config, oracle
    seen = []
    monkeypatch.setattr(oracle, "job_local_dir", lambda job: seen.append(job) or tmp_path / job)
    for arg in ("typeprobe", "jobs/typeprobe.py", "./jobs/typeprobe.py"):
        assert oracle.job_main(arg) == 2
        assert str(tmp_path / "typeprobe" / "job_report.json") in capsys.readouterr().out, arg
    assert seen == ["typeprobe"] * 3
    seen.clear()
    vi = L.load_job("gallery").VIS[0]["name"]
    monkeypatch.setattr(config, "job_local_dir", lambda job: seen.append(job) or tmp_path / job)
    monkeypatch.delitem(sys.modules, "jacq.wire_diff", raising=False)
    for arg in ("gallery", "jobs/gallery.py"):
        monkeypatch.setattr(sys, "argv", ["wire_diff", arg, vi])
        with pytest.raises(SystemExit) as e:
            runpy.run_module("jacq.wire_diff", run_name="__main__")
        assert e.value.code == 2 and str(tmp_path / "gallery" / "dumps") in capsys.readouterr().out, arg
    assert seen == ["gallery"] * 2


def test_command_lines_with_extra_or_misspelled_arguments_are_refused(tmp_path, monkeypatch, capsys):
    """The remaining entry points with the same kind of hole: jacq.lint (the gate before the LabVIEW machine run, `lint a b` must not lint only a), jacq.run's --dest (missing value,
    `--dest --force` must not take --force as the directory), jacq.oracle's extra arguments, jacq.catalog's missing arguments. All give one sentence / the usage + exit code 2, no side effects."""
    from jacq import catalog as C, oracle
    from jacq import run as RUN
    monkeypatch.delenv("JACQ_OFFLINE", raising=False)
    monkeypatch.chdir(tmp_path)
    for a in (["mcpi", "nosuchjob"], ["mcpi", "--offlien"], ["--offline"], ["--json", "--bogus", "mcpi"], []):
        assert L.main(a) == 2, a
    assert "wrong arguments" in capsys.readouterr().out
    assert L.main(["gallery", "--json"]) == 0   # correct usage works as usual (strict mode, the probe queue isn't written)
    capsys.readouterr()
    monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: pytest.fail("a command not run on the LabVIEW machine should not reach starting lab/vi_job.py"))
    from jacq.config import LAB_CORPUS, job_local_dir
    for a in (["gallery", "--dest"], ["gallery", "--dest", "--force"], ["--dest"], ["gallery", "--dest", ""],   # an empty string would resolve to the current directory, with results copied into the repo root
              ["gallery", "--dest", " "], ["gallery", "--dest", "--only"],   # pure whitespace; the value is an option other than --force (after removing it the remaining arguments are still valid, only this check can catch it)
              ["gallery", "--foo"], ["gallery", "extra"], ["gallery", "--only", "a", "--only", "b"],   # a wrong option is reported locally, not waiting for the LabVIEW machine to refuse
              ["gallery", "--only", "nosuchvi"], ["gallery", "--only", "nosuchvi,"],   # name not in VIS: the LabVIEW machine would only refuse after deleting the old report
              ["gallery", "--dest", str(LAB_CORPUS / "write2" / "jobs" / "failtest")],   # another job's result directory: files would get mixed in
              ["gallery", "--dest", str(LAB_CORPUS)], ["gallery", "--dest", str(LAB_CORPUS / "write2" / "jobs")]):   # lab_corpus itself, the parent of its own directory
        assert RUN.main(a) == 2 and "not run on the LabVIEW machine" in capsys.readouterr().out and not (tmp_path / "--force").exists(), a
    monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("REACHED")))
    with pytest.raises(RuntimeError, match="REACHED"):   # its own result directory is let through as usual (stops only on reaching the start of lab/vi_job.py)
        RUN.main(["gallery", "--dest", str(job_local_dir("gallery")), "--force"])
    vi0 = L.load_job("gallery").VIS[0]["name"]
    with pytest.raises(RuntimeError, match="REACHED"):   # a correct --only is let through as usual
        RUN.main(["gallery", "--only", vi0, "--dest", str(job_local_dir("gallery"))])
    for a in (["typeprobe", "extra"], ["a", "b"], ["--probes", "x"]):
        assert oracle.main(a) == 2 and "jacq.oracle" in capsys.readouterr().out, a
    assert C.main(["terms"]) == 2
    calls = []   # queue add writes catalog/probe_queue.json: not a single wrong form may reach queue_add
    monkeypatch.setattr(C, "queue_add", lambda *x: calls.append(x))
    for a in ([], [""], ["--term", "x"], ["Foo", "--term"], ["Foo", "--term", "--job"], ["Foo", "--term", "--job", "x"], ["Foo", "--job", ""], ["Foo", "--job", " "],
              ["Foo", "--trem", "x"], ["Foo", "extra"], ["Foo", "xxterm", "t"], ["Foo", "--job", "a", "--job", "b"], ["Foo", "--why"], ["Foo", "x", "--job", "j"]):
        assert C.main(["queue", "add", *a]) == 2 and not calls, a
    capsys.readouterr()
    assert C.main(["queue", "add", "Foo Bar"]) == 0 and C.main(["queue", "add", "Foo", "--why", "w", "--term", "t", "--job", "j"]) == 0
    assert calls == [("Foo Bar", None, None, ""), ("Foo", "t", "j", "w")], calls
    removed = []   # queue remove takes exactly one name (`remove a b` must not silently remove only a); nothing may follow list / --names
    monkeypatch.setattr(C, "queue_remove", lambda n: removed.append(n) or True)
    for a in (["remove"], ["remove", ""], ["remove", " "], ["remove", "a", "b"], ["list", "x"], ["--names", "x"], ["bogus"], ["remove", "a", "--force"]):
        assert C.main(["queue", *a]) == 2 and not removed, a
    capsys.readouterr()
    assert C.main(["queue", "remove", "a"]) == 0 and removed == ["a"]
    assert C.main(["search"]) == 2 and C.main(["search", ""]) == 2 and C.main(["search", " "]) == 2 and C.main(["record", "extra"]) == 2   # no word given (an empty string / pure whitespace would match everything) / record takes no arguments: usage + 2


def test_catalog_record_with_an_unreadable_line_writes_nothing(tmp_path, monkeypatch, capsys, tmp_queue):
    """A line in the output of probe_terms.sh can't be read (truncated, mixed with other output): no JSONDecodeError traceback,
    the whole batch isn't recorded, one sentence explains, both files (confirmed / queue) unchanged byte for byte."""
    import io
    from jacq import catalog as C
    conf = tmp_path / "confirmed.json"
    shutil.copy(C.CONFIRMED, conf)
    monkeypatch.setattr(C, "CONFIRMED", conf)
    before = (conf.read_bytes(), C.QUEUE.read_bytes() if C.QUEUE.exists() else None)
    good = json.dumps({"function": "ZZ Fn", "ok": True, "result": {"inputs": ["a"], "outputs": ["b"]}})
    res = {"terminals": ["t"]}
    for bad in ("{truncated", "[1]", json.dumps({"ok": True}),   # not JSON / not an object / no function
                json.dumps({"function": "ZZ Bad", "ok": True, "result": {"terminals": "xy"}}),   # terminals not a list: "xy" would be split into x, y
                json.dumps({"function": "ZZ Bad", "ok": True, "result": {"inputs": "xy"}}),
                json.dumps({"function": 123, "ok": True, "result": res}), json.dumps({"function": None, "ok": True, "result": res}),
                json.dumps({"function": "ZZ Bad", "ok": "false", "result": res}),   # the string "false" is truthy
                json.dumps({"function": "ZZ'Bad", "ok": True, "result": res})):   # quotes in the function name
        monkeypatch.setattr(sys, "stdin", io.StringIO(good + "\n" + bad + "\n"))
        assert C.main(["record"]) == 2, bad
        assert "nothing recorded" in capsys.readouterr().out
        assert (conf.read_bytes(), C.QUEUE.read_bytes() if C.QUEUE.exists() else None) == before, bad


def test_queue_refuses_function_names_that_would_break_the_remote_command(tmp_queue, capsys):
    """Function names in the queue get spliced by scripts/probe_terms.sh --queue into a PowerShell command sent over SSH to the LabVIEW machine (lab_run.sh wraps each argument in single quotes):
    quotes / $ / backticks / control characters in a name would break it. Both `queue add` and the queue writing entry point queue_add refuse (for the path where offline lint queues, see test_lint_pitfalls)."""
    from jacq import catalog as C
    for bad in ("x'); calc; ('", 'a"b', "a$b", "a`b", "a\nb", "a\x00b", " ", "", None, 5):
        with pytest.raises(ValueError):
            C.queue_add(bad, None, "j", "w")
    for bad in ("x'); calc; ('", 'a"b', "a$b", "a`b", "a\nb"):
        assert C.main(["queue", "add", bad, "--job", "j"]) == 2 and "not queued" in capsys.readouterr().out, bad
    assert not tmp_queue.exists()   # not a single one written
    assert C.main(["queue", "add", "Wait (ms)", "--term", "x'", "--job", "j"]) == 0   # valid names work as usual (terminal names may contain ', there are some in the catalog)
    assert list(C.queue_load()["items"]) == ["Wait (ms)"]


def test_under_and_the_dest_guard_work_when_the_corpus_directory_does_not_exist(tmp_path, monkeypatch, capsys):
    """A freshly cloned repo has no private/lab_corpus: samefile has nothing to compare with, and under() must not therefore always return False, disabling the guard for --dest under it
    (FAKE results would get written into it). When root doesn't exist, compare path component by component (ignoring case); if it can't be compared, count it as "inside".
    The same test pins down the branches of the FAKE guard and the real-run guard: under FAKE, going into lab_corpus (including the job's own directory) gives the FAKE sentence."""
    from jacq import config
    from jacq import run as RUN
    root = tmp_path / "private" / "lab_corpus"
    assert not root.exists()
    assert config.under(root, root) and config.under(root / "write2" / "jobs" / "x", root) and config.under(tmp_path / "PRIVATE" / "LAB_CORPUS" / "x", root)
    assert not config.under(tmp_path / "private", root) and not config.under(tmp_path / "private" / "lab_corpus_x", root) and not config.under(tmp_path / "elsewhere", root)
    real = tmp_path / "real"
    real.mkdir()
    with monkeypatch.context() as m:   # can't compare (permissions etc.): count it as "inside"
        m.setattr(config.os.path, "samefile", lambda a, b: (_ for _ in ()).throw(PermissionError("no")))
        assert config.under(real / "x", real) is True
    own = lambda j: root / "write2" / "jobs" / j   # noqa: E731
    monkeypatch.setattr(RUN, "LAB_CORPUS", root)
    monkeypatch.setattr(RUN, "job_local_dir", own)
    monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: pytest.fail("a refused --dest should not reach starting lab/vi_job.py"))
    monkeypatch.setenv("LVAGENT_FAKE", "1")
    for dest in (root, own("gallery"), own("mcpi"), tmp_path / "PRIVATE" / "LAB_CORPUS" / "x"):   # root, its own directory (never allowed under FAKE), another job, different case
        assert RUN.main(["gallery", "--dest", str(dest)]) == 2 and "LVAGENT_FAKE" in capsys.readouterr().out, dest
    monkeypatch.delenv("LVAGENT_FAKE")
    for dest in (root, own("mcpi")):   # real run: only its own directory allowed
        assert RUN.main(["gallery", "--dest", str(dest)]) == 2 and "may only be this job's own result directory" in capsys.readouterr().out, dest
    monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("REACHED")))
    with pytest.raises(RuntimeError, match="REACHED"):   # its own directory not existing yet is let through too (stops only on reaching the subprocess start)
        RUN.main(["gallery", "--dest", str(own("gallery"))])
    monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: pytest.fail("a refused --dest should not reach starting lab/vi_job.py"))
    (tmp_path / "c2").mkdir()
    monkeypatch.setattr(RUN, "LAB_CORPUS", tmp_path / "c2")   # the corpus directory exists, this job's own directory doesn't yet: one sentence, not samefile's FileNotFoundError
    monkeypatch.setattr(RUN, "job_local_dir", lambda j: tmp_path / "c2" / "write2" / "jobs" / j)
    assert RUN.main(["gallery", "--dest", str(tmp_path / "c2")]) == 2 and "may only be this job's own result directory" in capsys.readouterr().out


def test_check_job_args_is_the_one_local_check_for_everything_after_the_job_name(capsys):
    """The local check shared by jacq.run and vi_job.sh before the LabVIEW machine run / syncing the writer: all refusals of parse_job_args + the names in --only must be in this job's VIS.
    The job name may also be written as jobs/<job>.py; without --only the job doesn't need to be loaded."""
    names = [v["name"] for v in L.load_job("gallery").VIS]
    for arg, rest in (("gallery", []), ("gallery", ["--force"]), ("jobs/gallery.py", ["--only", ",".join(names), "--force"]), ("gallery", ["--only", names[0]])):
        assert L.check_job_args(arg, rest) is None, (arg, rest)
    for arg, rest, why in (("gallery", ["--onyl", "x"], "unknown"), ("gallery", ["--only"], "must be followed by"), ("gallery", ["--only", "a", "--only", "b"], "may only be given once"),
                           ("gallery", ["--only", "nosuchvi"], "VIs that don't exist"), ("gallery", ["--only", f"{names[0]},nosuchvi"], "'nosuchvi'"),
                           ("gallery", ["--only", "a$b"], "not a usable VI name"), ("old.x", [], "")):
        msg = L.check_job_args(arg, rest)
        assert msg and why in msg and "Traceback" not in msg, (arg, rest, msg)


def test_dest_guard_compares_files_not_spellings(tmp_path, monkeypatch, capsys):
    """The jacq.run --dest guards (FAKE may not enter private/lab_corpus; a real run only the job's own directory) compare by file system (samefile), not by path spelling: on case-insensitive file systems (Windows, macOS,
    some mounted volumes) `private/LAB_CORPUS` and `private/lab_corpus` are the same directory; comparing spellings would let it through, letting a fake report overwrite the on-site report."""
    from jacq import run as RUN
    from jacq.config import LAB_CORPUS, job_local_dir, under
    root = tmp_path / "Corpus"
    (root / "w").mkdir(parents=True)
    os.symlink(root, tmp_path / "alias")   # an alias not going through resolve(): like a case alias, a different path spelling for the same directory
    assert under(tmp_path / "alias", root) and under(tmp_path / "alias" / "w" / "new" / "x", root) and under(root, root)
    assert not under(tmp_path, root) and not under(tmp_path / "other" / "x", root)
    up = LAB_CORPUS.parent / LAB_CORPUS.name.upper()
    if not up.exists():
        pytest.skip("this file system is case-sensitive: there's no 'same directory with a different spelling' to test (the alias assertion above already tested the samefile half)")
    monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: pytest.fail("a refused --dest should not reach starting lab/vi_job.py"))
    for env_fake, dest in (("1", up), ("1", up / "write2" / "jobs" / "mcpi"), (None, up), (None, up / "write2" / "jobs" / "failtest")):
        if env_fake:
            monkeypatch.setenv("LVAGENT_FAKE", "1")
        else:
            monkeypatch.delenv("LVAGENT_FAKE", raising=False)
        assert RUN.main(["gallery", "--dest", str(dest)]) == 2, (env_fake, dest)
        assert "lab_corpus" in capsys.readouterr().out   # says why (the messages of both guards name this directory)
    if job_local_dir("gallery").exists():   # its own directory written in different case is let through as usual in a real run (stops only on reaching the subprocess start)
        monkeypatch.delenv("LVAGENT_FAKE", raising=False)
        monkeypatch.setattr(RUN.subprocess, "run", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("REACHED")))
        with pytest.raises(RuntimeError, match="REACHED"):
            RUN.main(["gallery", "--dest", str(up / "write2" / "jobs" / "gallery")])


def test_job_check_refuses_another_jobs_report(env, capsys):
    """`job_check <job> --dir <another job's result directory>`: when the job field in the report and the job on the command line differ, it must not judge anyway (that would record verified for a job never run,
    or revoke this job's old record with someone else's failure): refuse in one sentence, exit code 2, verified.json untouched."""
    other = dict(env.rep, job="otherjob")
    env.write(other)
    assert env.run() == 2 and not env.V.exists()   # reproduces B: would otherwise be recorded as "verified on-site"
    assert "otherjob" in capsys.readouterr().out
    env.write()
    assert env.run() == 0
    before = env.V.read_bytes()
    env.write(dict(other, failed=True))
    assert env.run() == 2 and env.V.read_bytes() == before   # reproduces A: would otherwise revoke zzjob's old record
    assert "not 'zzjob'" in capsys.readouterr().out


def test_job_check_stops_on_a_broken_verified_json(env, capsys):
    """verified.json is the on-site verification record: when it can't be read / has the wrong shape, one sentence, exit code 2, not overwritten, no traceback, and no blaming job_report.json."""
    for content in ("{bad", "", "[]", '{"zzjob": "x"}', '{"zzjob": {"a": 1}}', '{"zzjob": {"a": {}}}',   # the last one: no spec_hash in the record
                    '{"zzjob": {"a": {"spec_hash": 5}}}', '{"zzjob": {"a": {"spec_hash": "x", "evidence": 5}}}'):   # spec_hash not a string; evidence not a dict (--status would raise AttributeError)
        env.V.write_text(content, encoding="utf-8")
        assert env.run() == 2 and env.V.read_text(encoding="utf-8") == content, content
        out = capsys.readouterr().out
        assert "verified.json" in out and "do not edit it by hand" in out and "job_report.json or a dump" not in out, (content, out)
        assert J.status_main("zzjob") == 2 and "do not edit it by hand" in capsys.readouterr().out, content


def test_verified_json_is_replaced_atomically(env, monkeypatch):
    """When killed halfway through writing (simulated here by os.replace raising), what's left must be the old complete file; after a successful write no .tmp is left."""
    assert env.run() == 0 and not list(env.V.parent.glob("verified.json.tmp"))
    before = env.V.read_bytes()
    env.write(dict(env.rep, time="2026-11-14 10:00:00"))   # the date changed: what this round writes differs from the old content
    monkeypatch.setattr(J.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        env.run()
    assert env.V.read_bytes() == before


def test_verified_json_bytes_are_unchanged_by_the_atomic_write(env):
    """Atomic writing only changed how it's written: the same data gives the same bytes (indent=1, Chinese kept, no trailing newline); the on-site verified.json read in and written out again is unchanged,
    so the next job_check write won't reformat the whole file."""
    J._write_verified({"j": {"v": {"spec_hash": "x", "note": "中文"}}})
    assert env.V.read_bytes() == '{\n "j": {\n  "v": {\n   "spec_hash": "x",\n   "note": "中文"\n  }\n }\n}'.encode("utf-8")
    vf = REPO / "jobs" / "verified.json"   # local record (gitignored); absent in a fresh clone
    if vf.exists():
        live = vf.read_text(encoding="utf-8")
        assert json.dumps(json.loads(live), ensure_ascii=False, indent=1) == live


def test_oracle_corrupt_or_foreign_report_is_one_sentence(env, monkeypatch, capsys):
    """When jacq.oracle reads a job_report.json truncated by scp / with wrong content: one sentence like job_check, no traceback."""
    from jacq import oracle
    monkeypatch.setattr(oracle, "load_job_arg", lambda arg: (arg, env.job))
    for content in ("{bad", "{}", "[]", "null", '{"vis": null}', ""):
        (env.D / "job_report.json").write_text(content, encoding="utf-8")
        assert oracle.job_main("zzjob", env.D) == 2 and "not comparing" in capsys.readouterr().out, content
    env.write(dict(env.rep, vis=None))   # job field right, vis not a dict: one sentence like the bad contents above, no traceback
    assert oracle.job_main("zzjob", env.D) == 2 and "not comparing" in capsys.readouterr().out
    env.write(dict(env.rep, job="otherjob"))
    assert oracle.job_main("zzjob", env.D) == 2 and "not 'zzjob'" in capsys.readouterr().out


def test_wire_diff_wrong_spec_shape_is_one_sentence(monkeypatch, capsys):
    """Each item of VIS has name and spec, but spec is not a dict / is missing wires: lint gives SHAPE, wire_diff also gives one sentence, no AttributeError / KeyError traceback."""
    import runpy
    import types
    monkeypatch.delitem(sys.modules, "jacq.wire_diff", raising=False)
    for spec in ("notdict", {}, {"controls": None, "wires": []}, {"wires": ["abc"]}):   # AttributeError / KeyError / TypeError / ValueError
        monkeypatch.setattr(L, "load_job", lambda n, s=spec: types.SimpleNamespace(VIS=[{"name": "a", "spec": s}]))
        monkeypatch.setattr(sys, "argv", ["wire_diff", "zzj", "a"])
        with pytest.raises(SystemExit) as e:
            runpy.run_module("jacq.wire_diff", run_name="__main__")
        assert e.value.code == 2 and "has the wrong shape" in capsys.readouterr().out, spec


def test_vi_job_sh_checks_its_options_before_syncing_the_writer():
    """vi_job.sh: after lint and before syncing the writer (writing to the LabVIEW machine over SSH), the local machine first checks the arguments after the job name with the same parsing as lab/vi_job.py.
    A misspelled `--onyl` needn't wait until the writer is synced and the LabVIEW machine refuses to be noticed. Here the "extract job name → lint → argument check" lines of the script are really run."""
    key = "lint.check_job_args"
    lines = (REPO / "scripts" / "vi_job.sh").read_text(encoding="utf-8").splitlines()
    at = lambda t: next(k for k, l in enumerate(lines) if t in l and not l.lstrip().startswith("#"))   # noqa: E731
    assert at("-m jacq.lint") < at(key) < at("sync_writer.sh"), "order: lint → local argument check → sync writer (the lines really run below only go as far as the argument check, they can't test the order)"
    n0, n1 = [v["name"] for v in L.load_job("gallery").VIS][:2]
    for extra in (["--onyl", "t"], ["--only"], ["--only", "a", "--only", "b"], ["extra"], ["--Force"], ["--only", "--force"], ["--only", "a$b"], ["--only", "a,'b"],
                  ["--only", "nosuchvi"], ["--only", f"{n0},nosuchvi"]):   # the last two: names not in this job's VIS (not waiting for the LabVIEW machine to refuse, by then the writer is already synced)
        r = _vi_job_sh_until_lint("gallery", sys.executable, end=key, extra=extra)
        assert r.returncode == 1 and "wrong arguments" in r.stdout and "REACHED_SYNC" not in r.stdout, (extra, r.stdout, r.stderr)
        assert "Traceback" not in r.stderr, (extra, r.stderr)
    for extra in ([], ["--force"], ["--only", f"{n1},{n0}", "--force"]):
        r = _vi_job_sh_until_lint("gallery", sys.executable, end=key, extra=extra)
        assert r.returncode == 0 and "REACHED_SYNC gallery" in r.stdout, (extra, r.stdout, r.stderr)


def test_wire_diff_missing_or_broken_dump_is_one_sentence(tmp_path, monkeypatch, capsys):
    """A real job with lint 0 E, not yet run on the LabVIEW machine / incompletely pulled back: `wire_diff <job> <VI>` threw a FileNotFoundError traceback."""
    import runpy
    from jacq import config
    vi = L.load_job("gallery").VIS[0]["name"]
    monkeypatch.setattr(config, "job_local_dir", lambda job: tmp_path / job)
    monkeypatch.setattr(sys, "argv", ["wire_diff", "gallery", vi])
    monkeypatch.delitem(sys.modules, "jacq.wire_diff", raising=False)   # runpy re-executes it as __main__; leaving it would give a RuntimeWarning, monkeypatch restores it after the test
    d = tmp_path / "gallery" / "dumps"
    for content in (None, "{not json", "{}", "[]"):   # no file / not JSON / no result key / not an object
        if content is not None:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{vi}.json").write_text(content, encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            runpy.run_module("jacq.wire_diff", run_name="__main__")
        assert e.value.code == 2 and "can't read" in capsys.readouterr().out, content


def test_vi_job_sh_takes_a_job_path_too():
    """The first line of vi_job.sh (remote mode) follows the same rule as jacq.lint.job_name for the file-name half (anything not ending in .py is taken as-is as the job name, whether the name is valid is up to
    the pass handed to lint); with no argument it still stops under set -u."""
    import re
    from jacq.lint import job_name
    line = re.search(r'^case "\$1" in .*$', (REPO / "scripts" / "vi_job.sh").read_text(encoding="utf-8"), re.M).group(0)
    sh = lambda pre, *a: subprocess.run(["bash", "-c", f'set -u\n{pre}\n{line}\nprintf %s "$JOB"', *a], capture_output=True, text=True,
                                        env={"PATH": "/usr/bin:/bin"})   # runs only this line of the script, not the script
    for arg in _JOB_ARGS:
        r = sh('set -- "$0"', arg)
        assert r.stdout == job_name(arg), (arg, r.stdout, r.stderr)
    for arg in ("jobs/zz", "zz.py/", "zz.PY", "old.zz"):   # not ending in .py: bash carries it as-is, job_name refuses
        assert sh('set -- "$0"', arg).stdout == arg
        with pytest.raises(L.JobArgError):
            job_name(arg)
    r = sh("set --")
    assert r.returncode != 0 and r.stdout == "" and "unbound" in r.stderr


def test_jacq_run_does_not_judge_a_report_from_an_earlier_round(tmp_path, monkeypatch, capsys):
    """When lab/vi_job.py errors before even starting, what's left in the sandbox and dest is the previous round's report, which must not be judged (much less used to write verified)."""
    RUN, calls, src = _stub_run(monkeypatch, tmp_path, stdout="Traceback ...\n", rc=1, write_report=False)
    src.mkdir(parents=True)
    old = src / "job_report.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (old.stat().st_mtime - 3600,) * 2)
    dest = tmp_path / "res"
    assert RUN.main(["zz", "--dest", str(dest)]) == 2
    assert calls["sub"] == 1 and calls["check"] == 0 and not dest.exists() and "produced no job_report.json" in capsys.readouterr().out
    RUN, calls, _ = _stub_run(monkeypatch, tmp_path)                        # a normal round: copied back and handed to job_check
    assert RUN.main(["zz", "--dest", str(dest)]) == 0 and calls["check"] == 1 and (dest / "job_report.json").exists()
    RUN, calls, _ = _stub_run(monkeypatch, tmp_path, stdout="NEED RESTART\n", rc=4)   # LabVIEW hung: say so clearly, don't close LabVIEW ourselves
    assert RUN.main(["zz", "--dest", str(dest)]) == 2 and "exit code 4" in capsys.readouterr().out


def test_writer_sha_matches_lab_side_and_sync_scope():
    """The local machine and the LabVIEW machine use the same function for the writer; scope = the labview_mcp/*.py synced by scripts/sync_writer.sh."""
    pkg = REPO / "writer" / "labview_mcp"
    assert writer_sha() == GUARD.writer_sha(pkg)
    assert '"$WRITER"/*.py' in (REPO / "scripts" / "sync_writer.sh").read_text(encoding="utf-8")
    # the LabVIEW machine side really calls lablib.writer_sha() (LVAGENT_SHLIM pointing to the local writer), and the result must match the local one
    code = "import sys; sys.path.insert(0, sys.argv[1]); import lablib; print(lablib.writer_sha())"
    r = subprocess.run([sys.executable, "-c", code, str(REPO / "lab")], env={**os.environ, "LVAGENT_SHLIM": str(REPO / "writer")},
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == writer_sha(), r.stderr


def test_writer_sha_covers_every_py_and_only_py(tmp_path):
    """The scope is tested by behavior (without rewriting the hash): changing / adding any *.py changes it; changing a non-.py (vi_classes.json) doesn't; no gen.py returns None."""
    pkg = tmp_path / "labview_mcp"
    shutil.copytree(REPO / "writer" / "labview_mcp", pkg, ignore=shutil.ignore_patterns("__pycache__", "vis"))
    base = GUARD.writer_sha(pkg)
    for f in sorted(pkg.glob("*.py")):
        old = f.read_bytes()
        f.write_bytes(old + b"\n")
        assert GUARD.writer_sha(pkg) != base, f.name
        f.write_bytes(old)
    assert GUARD.writer_sha(pkg) == base
    (pkg / "zz_new.py").write_text("", encoding="utf-8")
    assert GUARD.writer_sha(pkg) != base
    (pkg / "zz_new.py").unlink()
    for f in pkg.glob("*.json"):
        f.write_bytes(f.read_bytes() + b" ")
    assert GUARD.writer_sha(pkg) == base
    (pkg / "gen.py").unlink()
    assert GUARD.writer_sha(pkg) is None


def test_vi_job_sh_judges_only_this_rounds_partial_report(tmp_path):
    """The remote mode's "didn't finish" branch only runs job_check for complete=false reports; if what was pulled back is complete=true (the previous round's), no verdict."""
    import re
    src = (REPO / "scripts" / "vi_job.sh").read_text(encoding="utf-8")
    gate = re.search(r"\$PY -c '([^']+)' \"\$RPT\"", src).group(1)
    rc = {}
    for k, rep in {"partial": {"complete": False}, "old": {"complete": True}, "legacy": {}}.items():
        f = tmp_path / f"{k}.json"
        f.write_text(json.dumps(rep), encoding="utf-8")
        rc[k] = subprocess.run([sys.executable, "-c", gate, str(f)]).returncode
    rc["missing"] = subprocess.run([sys.executable, "-c", gate, str(tmp_path / "none.json")], capture_output=True).returncode
    assert rc == {"partial": 0, "old": 1, "legacy": 1, "missing": 1}


def test_vi_job_sh_incomplete_branch_wiring(tmp_path):
    """The previous case only tested the gate expression; this one extracts the if/else of the "didn't finish" branch of vi_job.sh verbatim and runs it with bash (not the whole script, SSH / pull.sh untouched),
    with $PY replaced by a stub: -c is passed to python as usual, -m jacq.job_check only prints a marker. Checks whether job_check gets called in the four cases pull-back success / failure × report new / old."""
    import re
    src = (REPO / "scripts" / "vi_job.sh").read_text(encoding="utf-8")
    block = re.search(r"\n(\s*if \[ \$PRC -eq 0 \] && \$PY -c .*?\n\s*fi)\n", src, re.S).group(1)
    stub = tmp_path / "py"
    stub.write_text(f'#!/bin/sh\nif [ "$1" = "-m" ]; then echo JOB_CHECK_CALLED; exit 0; fi\nexec {sys.executable} "$@"\n')
    seen = {}   # $PY is unquoted in the script, so "/bin/sh stub" still expands into a command (also not relying on the tmp directory being executable)
    for prc in (0, 1):
        for k, rep in {"partial": {"complete": False}, "old": {"complete": True}}.items():
            f = tmp_path / f"{k}.json"
            f.write_text(json.dumps(rep), encoding="utf-8")
            r = subprocess.run(["bash", "-c", block], env={"PATH": "/usr/bin:/bin", "PY": f"/bin/sh {stub}", "PRC": str(prc), "RPT": str(f), "JOB": "zz"},
                               capture_output=True, text=True)
            seen[(prc, k)] = "JOB_CHECK_CALLED" in r.stdout
    assert seen == {(0, "partial"): True, (0, "old"): False, (1, "partial"): False, (1, "old"): False}, seen


def test_decode_nonfinite_follows_the_spec_types():
    """The writer hands NaN / inf over as strings; numeric controls / indicators get floats back, strings stay strings."""
    import math
    spec = {"controls": [{"name": "s", "type": "string"}],
            "indicators": [{"name": "x", "type": "dbl"}, {"name": "a", "type": "dbl[][]"}, {"name": "t", "type": "string[]"},
                           {"name": "c", "type": {"cluster": [{"name": "n", "type": "string"}, {"name": "v", "type": "sgl"}]}},
                           {"name": "e", "type": {"enum": ["nan", "b"]}}]}
    got = GUARD.decode_nonfinite({"s": "nan", "x": "nan", "a": [["inf", 1.0], ["-inf"]], "t": ["nan"], "c": ["nan", "nan"],
                                  "e": "nan", "_reads": "nan"}, spec)
    assert math.isnan(got["x"]) and got["a"][0][0] == math.inf and got["a"][1][0] == -math.inf and got["a"][0][1] == 1.0
    assert got["s"] == "nan" and got["t"] == ["nan"] and got["e"] == "nan" and got["_reads"] == "nan"
    assert got["c"][0] == "nan" and math.isnan(got["c"][1])
    spec = {"indicators": [{"name": "q", "type": "str"}, {"name": "i", "type": "i32[]"},
                           {"name": "ca", "type": {"array": {"cluster": [{"name": "n", "type": "str"}, {"name": "v", "type": "double"}]}, "dims": 2}}]}
    got = GUARD.decode_nonfinite({"q": "nan", "i": ["nan"], "ca": [[["nan", "nan"]]]}, spec)
    assert got["q"] == "nan" and got["i"] == ["nan"] and got["ca"][0][0][0] == "nan" and math.isnan(got["ca"][0][0][1])


def test_decode_nonfinite_knows_every_float_name_of_the_writer():
    import sys
    from jacq.config import WRITER_SRC
    sys.path.insert(0, str(WRITER_SRC))
    from labview_mcp import gen
    floats = {n for n in [*gen._NUM, *gen._ALIASES] if gen.parse_type(n).kind in ("sgl", "dbl")}
    assert floats == GUARD.FLOAT_TYPES


def test_writer_matches_nonfinite_expectations():
    """vi_test: an expected NaN / inf matches the "nan" / "inf" string the writer's _jsonable makes of it, nothing else does."""
    import ast
    import math
    src = (REPO / "writer" / "labview_mcp" / "lv.py").read_text(encoding="utf-8")   # lv.py imports pythoncom: take just the function
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_matches")
    ns = {"math": math}
    exec(compile(ast.Module([fn], []), "lv.py", "exec"), ns)
    _matches = ns["_matches"]
    assert _matches("nan", float("nan"), 1e-9) and _matches(float("nan"), float("nan"), 1e-9)
    assert _matches("-inf", float("-inf"), 1e-9) and not _matches("inf", float("-inf"), 1e-9)
    assert not _matches(1.0, float("nan"), 1e-9) and not _matches("nan", 1.0, 1e-9) and not _matches(None, float("nan"), 1e-9)


PRODJOB = r'''
from lablib import BASE
OUT = BASE / "write2" / "jobs" / "zzprod"
SPEC_A = {"controls": [{"name": "x", "type": "dbl", "default": 0.5}], "indicators": [{"name": "y", "type": "dbl[]"}],
          "functions": [{"name": "f", "function": "Increment"}], "wires": [["x", "f.x"]]}
SPEC_B = {"controls": [{"name": "z", "type": "dbl[]"}], "indicators": [{"name": "w", "type": "dbl"}],
          "functions": [{"name": "f", "function": "Increment"}], "wires": [["z", "f.x"]]}
VIS = [{"name": "a", "spec": SPEC_A}, {"name": "b", "spec": SPEC_B}]
def check_b(v):
    return {"pass": v["w"] < 100}
PRODUCTION = [{"key": "s1", "vi": "a", "inputs": {"x": 1.0}},
              {"key": "s2", "vi": "a", "inputs": {"x": 2.0}},
              {"key": "s3", "vi": "b", "inputs": {"z": {"from": ["s1", "s2"], "out": "y"}}, "check": "check_b"}]
%s
'''

PRODDRV = r'''
import json, os, runpy, sys, lablib
def vi_run(self, p, i, o, t, a):
    if os.environ.get("INTR") and p.endswith("b.vi"):
        raise KeyboardInterrupt
    if os.environ.get("BUSY") and p.endswith("b.vi"):
        raise RuntimeError("VI is already running.")
    if p.endswith("a.vi"):
        if os.environ.get("KILL") == str(i["x"]):
            os._exit(4)
        x = "nan" if i["x"] < 0 else i["x"]
        return {"completed": True, "values": {"y": [x, x]}}
    if os.environ.get("MISS"):
        return {"completed": True, "values": {}, "unreadable": {"w": "COM error"}}
    return {"completed": True, "values": {"w": sum(i["z"])}}
lablib.FakeLV.vi_run = vi_run
if os.environ.get("PREP"):          # real mode up to LV(): files and cache as jacq.run leaves them
    import jobguard
    from lablib import BASE, writer_sha
    from vi_job import cache_key
    job = jobguard.load_job("zzprod")
    out = jobguard.job_out(BASE, "zzprod")
    out.mkdir(parents=True, exist_ok=True)
    cache = {"__writer__": writer_sha() if os.environ["PREP"] == "ok" else "old"}
    for v in job.VIS:
        (out / (v["name"] + ".vi")).write_bytes(b"RSRC")
        cache[v["name"]] = cache_key(jobguard.spec_hash(v), v["spec"], cache, out)
    (out / ".spec_hash.json").write_text(json.dumps(cache), encoding="utf-8")
sys.argv = ["production.py"] + sys.argv[1:]
runpy.run_path(%r, run_name="__main__")
'''


def fake_production(tmp_path, *args, extra="", kill=None, real=False, **env_extra):
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "jobs" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "jobs" / "zzprod.py").write_text(PRODJOB % extra, encoding="utf-8")
    drv = tmp_path / "proddrv.py"
    drv.write_text(PRODDRV % str(REPO / "lab" / "production.py"), encoding="utf-8")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": os.pathsep.join([str(tmp_path), str(REPO / "lab")]), "PYTHONIOENCODING": "utf-8",
           "LVAGENT_SHLIM": str(REPO / "writer")}   # the driver imports lablib before production.py could set its default
    for k in ("KILL", "INTR", "BUSY", "MISS", "PREP"):
        env.pop(k, None)
    if real:
        env.pop("LVAGENT_FAKE")
    if kill is not None:
        env["KILL"] = str(kill)
    env.update(env_extra)
    r = subprocess.run([sys.executable, str(drv), "zzprod", *args], env=env, cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    out = tmp_path / "sb" / "lv" / "write2" / "jobs" / "zzprod"
    return r, out / ("production" if real else "production_fake")


def rec(prod, k):
    return json.loads((prod / f"{k}.json").read_text(encoding="utf-8"))


def test_production_runs_steps_in_order_and_resumes(tmp_path):
    """Steps run in order, references are resolved (a list concatenates), results / log / heartbeat are written, a rerun skips finished steps;
    fake results never land where a real run would take them as done."""
    r, prod = fake_production(tmp_path)
    assert r.returncode == 0 and "PRODUCTION COMPLETE" in r.stdout, r.stdout + r.stderr
    s3 = rec(prod, "s3")
    assert s3["inputs"]["z"] == [1.0, 1.0, 2.0, 2.0] and s3["values"]["w"] == 6.0 and s3["ok"] is True and s3["check"] == {"pass": True}
    assert "PRODUCTION COMPLETE" in (prod / "production.log").read_text(encoding="utf-8")
    assert json.loads((prod / "heartbeat.json").read_text(encoding="utf-8"))["exited"] is True
    assert not (prod / "current.txt").exists() and not (prod.parent / "production").exists()
    r, prod = fake_production(tmp_path)
    assert r.returncode == 0 and "== s1" not in r.stdout, r.stdout
    r, prod = fake_production(tmp_path, "--plan")
    assert r.returncode == 0 and r.stdout.count(" done") == 3, r.stdout


def test_production_reruns_what_a_changed_step_affects(tmp_path):
    """Editing s1 reruns s1 and s3 (takes from s1) but not s2: a finished result is reused only while what it depends on is unchanged."""
    fake_production(tmp_path)
    r, prod = fake_production(tmp_path, extra='PRODUCTION[0]["inputs"]["x"] = 3.0')
    assert r.returncode == 0 and "== s1" in r.stdout and "== s2" not in r.stdout and "== s3" in r.stdout, r.stdout
    assert rec(prod, "s3")["inputs"]["z"] == [3.0, 3.0, 2.0, 2.0]


def test_production_reruns_an_interrupted_step_at_most_twice(tmp_path):
    """The process dies during s2 (reboot / hung LabVIEW): the next run counts it as an attempt and runs it again; dying twice stops for a person."""
    r, prod = fake_production(tmp_path, kill=2.0)
    assert r.returncode == 4 and (prod / "current.txt").read_text(encoding="utf-8").startswith("s2\t") and (prod / "s1.json").exists()
    r, prod = fake_production(tmp_path)
    assert r.returncode == 0 and "interrupted during step 's2'" in r.stdout and "== s1" not in r.stdout, r.stdout
    for f in ("s2.json", "s3.json"):
        (prod / f).unlink()
    fake_production(tmp_path, kill=2.0)
    fake_production(tmp_path, kill=2.0)
    r, prod = fake_production(tmp_path)
    assert r.returncode == 1 and "failed twice" in r.stdout, r.stdout
    assert rec(prod, "s2")["attempts"] == 2


def test_production_ctrl_c_is_an_interruption_and_a_busy_vi_is_not_an_attempt(tmp_path):
    """Ctrl+C exits at once like closing the window (the step counts as interrupted); "VI is already running" (left by a killed run) stops
    without spending one of the two attempts."""
    r, prod = fake_production(tmp_path, BUSY="1")
    assert r.returncode == 1 and "no attempt counted" in r.stdout and not (prod / "current.txt").exists(), r.stdout + r.stderr
    assert rec(prod, "s3")["attempts"] == 0
    r, prod = fake_production(tmp_path, INTR="1")
    assert r.returncode == 1 and "Ctrl+C" in r.stdout and (prod / "current.txt").exists(), r.stdout + r.stderr
    r, prod = fake_production(tmp_path)
    assert r.returncode == 0 and "interrupted during step 's3' (attempt 1)" in r.stdout and "PRODUCTION COMPLETE" in r.stdout, r.stdout


def test_production_counts_a_crash_of_a_step_being_redone(tmp_path):
    """s2 was done, then edited: its rerun dies twice -> stopped, not an endless loop (the old ok record must not hide the crashes)."""
    fake_production(tmp_path)
    for _ in range(2):
        r, prod = fake_production(tmp_path, extra='PRODUCTION[1]["inputs"]["x"] = 5.0', kill=5.0)
        assert r.returncode == 4, r.stdout
    r, prod = fake_production(tmp_path, extra='PRODUCTION[1]["inputs"]["x"] = 5.0')
    assert r.returncode == 1 and "failed twice" in r.stdout, r.stdout


def test_production_reruns_what_a_rebuilt_vi_or_a_redone_step_affects(tmp_path):
    """A VI rebuilt from another subVI / writer (its .spec_hash.json entry) reruns its steps and what takes from them; a deleted result too."""
    fake_production(tmp_path)
    out = tmp_path / "sb" / "lv" / "write2" / "jobs" / "zzprod"
    (out / ".spec_hash.json").write_text(json.dumps({"b": "rebuilt"}), encoding="utf-8")
    r, prod = fake_production(tmp_path)
    assert "== s1" not in r.stdout and "== s3" in r.stdout and r.returncode == 0, r.stdout
    (prod / "s1.json").unlink()
    r, prod = fake_production(tmp_path)
    assert "== s1" in r.stdout and "== s2" not in r.stdout and "== s3" in r.stdout and r.returncode == 0, r.stdout


def test_production_fails_a_step_whose_outputs_were_not_read_or_whose_check_fails(tmp_path):
    r, prod = fake_production(tmp_path, MISS="1")
    assert r.returncode == 1 and "not read back" in r.stdout and rec(prod, "s3")["unreadable"] == {"w": "COM error"}, r.stdout
    r, prod = fake_production(tmp_path / "c", extra='PRODUCTION[0]["inputs"]["x"] = 60.0')     # w = 124: check_b fails
    assert r.returncode == 1 and "check_b failed" in r.stdout and rec(prod, "s3")["ok"] is False, r.stdout


def test_production_stops_on_nan_input_unless_allowed(tmp_path):
    """s1 gives NaN (the writer's "nan" string, decoded to a float): s3 must not compute on it unless the step says allow_nan."""
    import math
    r, prod = fake_production(tmp_path, extra='PRODUCTION[0]["inputs"]["x"] = -1.0')
    assert r.returncode == 1 and "contain NaN" in r.stdout and not (prod / "s3.json").exists(), r.stdout
    assert math.isnan(rec(prod, "s1")["values"]["y"][0])     # a float NaN, not the string
    r, prod = fake_production(tmp_path, extra='PRODUCTION[0]["inputs"]["x"] = -1.0\nPRODUCTION[2]["allow_nan"] = True')
    assert "== s3" in r.stdout and math.isnan(rec(prod, "s3")["inputs"]["z"][0]), r.stdout   # computed on NaN (then check_b: NaN < 100 fails)


def test_production_refuses_bad_steps_and_options(tmp_path):
    """Invalid PRODUCTION (the same check as lint PROD), bad options: exit 2 before touching LabVIEW; --only says it is only part."""
    r, prod = fake_production(tmp_path, extra='PRODUCTION[2]["inputs"]["z"] = {"from": "s9", "out": "y"}')
    assert r.returncode == 2 and "PRODUCTION REFUSED" in r.stdout and "s9" in r.stdout and not prod.exists(), r.stdout
    for args in (("--only", "s1,zz"), ("--only", ","), ("--only",), ("--bogus",)):
        r, prod = fake_production(tmp_path, *args)
        assert r.returncode == 2, (args, r.stdout)
    r, prod = fake_production(tmp_path, "--only", "s1")
    assert r.returncode == 0 and "PRODUCTION ONLY DONE" in r.stdout and "PRODUCTION COMPLETE" not in r.stdout, r.stdout
    assert (prod / "s1.json").exists() and not (prod / "s2.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="the test holds the lock with fcntl")
def test_production_refuses_a_second_runner(tmp_path):
    import fcntl
    fake_production(tmp_path, "--plan")
    prod = tmp_path / "sb" / "lv" / "write2" / "jobs" / "zzprod" / "production_fake"
    prod.mkdir(parents=True, exist_ok=True)
    with open(prod.parent / ".production.lock", "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        r, _ = fake_production(tmp_path)
    assert r.returncode == 2 and "another production" in r.stdout and not (prod / "s1.json").exists(), r.stdout


def test_production_refuses_vis_that_are_not_verified_and_built(tmp_path):
    """Real mode: refused (exit 2, LabVIEW untouched) until every VI is verified beyond build-only and built from that spec by this writer."""
    def verified(kind, ws=None):
        (tmp_path / "jobs" / "verified.json").write_text(json.dumps({"zzprod": {v["name"]: {"spec_hash": GUARD.spec_hash(v), "evidence": {"kind": kind.get(v["name"], "tests") if isinstance(kind, dict) else kind},
                                                                                            "writer_sha": ws or writer_sha()}
                                                                          for v in job.VIS}}), encoding="utf-8")
    r, _ = fake_production(tmp_path, real=True)
    assert r.returncode == 2 and "not verified" in r.stdout, r.stdout
    job = types.SimpleNamespace()
    exec(compile((PRODJOB % "").replace("from lablib import BASE", "from pathlib import Path; BASE = Path('/x')"), "zzprod", "exec"), job.__dict__)
    verified("build only")
    r, _ = fake_production(tmp_path, real=True)
    assert r.returncode == 2 and "build only" in r.stdout, r.stdout
    verified("tests", ws="an older writer")
    r, _ = fake_production(tmp_path, real=True)
    assert r.returncode == 2 and "another writer" in r.stdout, r.stdout
    verified("tests")
    r, _ = fake_production(tmp_path, real=True)
    assert r.returncode == 2 and ".spec_hash.json" in r.stdout, r.stdout
    r, _ = fake_production(tmp_path, real=True, PREP="old")
    assert r.returncode == 2 and "writer changed" in r.stdout, r.stdout
    r, prod = fake_production(tmp_path, real=True, PREP="ok")       # ready: it gets as far as connecting to LabVIEW, which isn't here
    assert r.returncode == 1 and "cannot reach LabVIEW" in r.stdout and not list(prod.glob("s*.json")), r.stdout
    verified({"b": "build only"})                                   # b is run by s3: refused; with s3 gone b is only a VI of the job: fine
    r, _ = fake_production(tmp_path, real=True, PREP="ok")
    assert r.returncode == 2 and "only verified as 'build only'" in r.stdout and "['b']" in r.stdout, r.stdout
    r, _ = fake_production(tmp_path, extra="PRODUCTION = PRODUCTION[:2]", real=True, PREP="ok")
    assert r.returncode == 1 and "cannot reach LabVIEW" in r.stdout, r.stdout
    verified({"a": None})
    r, _ = fake_production(tmp_path, real=True, PREP="ok")
    assert r.returncode == 2 and "no evidence kind" in r.stdout, r.stdout


@pytest.mark.skipif(os.name == "nt", reason="the test holds the lock with fcntl")
def test_vi_job_refuses_while_a_production_of_the_job_runs(tmp_path):
    """A jacq.run / vi_job round (fake ones too: they write the same VIs) would rebuild the VIs under a running production: refused, leaving no
    report (no verdict), so the last round's report can't pass for this one's."""
    import fcntl
    r, rep, out = fake_vi_job(tmp_path)
    assert rep is not None
    with open(out / ".production.lock", "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        r, rep2, out = fake_vi_job(tmp_path)
    assert r.returncode != 0 and "production of zzfake is running" in r.stderr and rep2 is None, r.stdout + r.stderr
    assert not (out / "production").exists() and not (out / "production_fake").exists()   # a round doesn't create production dirs (they'd be pulled)


def test_production_refuses_a_job_that_does_not_load(tmp_path):
    r, prod = fake_production(tmp_path, extra="this is not python")
    assert r.returncode == 2 and "PRODUCTION REFUSED" in r.stdout and "does not load" in r.stdout, r.stdout + r.stderr


def test_production_rechecks_a_reused_result_and_ignores_timeout_edits(tmp_path):
    """A changed check is evaluated again on the stored values (not hashed: a stricter check must not pass on old values, a tweaked one
    must not rerun hours of VI); raising timeout_s reruns nothing."""
    fake_production(tmp_path)
    r, prod = fake_production(tmp_path, extra='PRODUCTION[0]["timeout_s"] = 7200')
    assert r.returncode == 0 and "\n== " not in r.stdout, r.stdout
    r, prod = fake_production(tmp_path, extra="def check_b(v):\n    return {'pass': v['w'] < 1}")
    assert "check_b (as it is now) fails" in r.stdout and "== s3" in r.stdout and "== s1" not in r.stdout, r.stdout


def test_production_rejects_inputs_that_are_not_json():
    m = types.SimpleNamespace(VIS=[{"name": "v", "spec": {"controls": [{"name": "x", "type": "dbl[]"}], "indicators": []}}],
                              PRODUCTION=[{"key": "s1", "vi": "v", "inputs": {"x": {1, 2}}}])
    assert any("plain JSON" in msg for _, msg in GUARD.production_problems(m))


def test_lv_call_passes_a_timeout_error_of_the_call_through_at_once(tmp_path):
    """LV.c waits in short slices; a TimeoutError raised by the called function itself (OSError ETIMEDOUT is one) must surface at once,
    not spin until the budget is used up and then claim LabVIEW hung."""
    code = ("import time, lablib\n"
            "lv = lablib.LV()\n"
            "def f(): raise TimeoutError('own')\n"
            "t0 = time.time()\n"
            "try:\n    lv.c(f, t=30)\nexcept TimeoutError as e:\n    print('RAISED', e, round(time.time() - t0, 1))\n")
    env = {**os.environ, "LVAGENT_FAKE": "1", "LVAGENT_BASE": str(tmp_path / "sb" / "lv"), "LVAGENT_SANDBOX": str(tmp_path / "sb"),
           "PYTHONPATH": str(REPO / "lab")}
    env.pop("LVAGENT_FAKE_TIMEOUT", None)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("RAISED own") and float(r.stdout.split()[-1]) < 5, r.stdout + r.stderr


BADCHECK = "class NP:\n    pass\ndef check_b(v):\n    v['w'] = 'clobbered'\n    return {'pass': False, 'resid': NP()}"


def test_production_check_gets_a_copy_and_a_non_json_answer_is_still_saved(tmp_path):
    """A check that changes its values or answers with numpy-like scalars must not lose the step's result (a strict dump would crash
    outside the step's try: the result lost, current.txt left, a false "interrupted" next time)."""
    r, prod = fake_production(tmp_path, extra=BADCHECK)
    assert r.returncode == 1 and "check_b failed" in r.stdout and "unexpected error" not in r.stdout, r.stdout + r.stderr
    assert rec(prod, "s3")["values"] == {"w": 6} and not (prod / "current.txt").exists()


def test_production_reuses_a_result_whose_edited_check_now_passes_and_a_new_timeout_resets_attempts(tmp_path):
    """Check and timeout_s are outside the signature, so editing them must still get a stopped step going: a completed result that failed
    only its check counts as done once the edited check passes on it; a changed timeout_s starts a new attempt count."""
    fake_production(tmp_path, extra=BADCHECK)
    fake_production(tmp_path, extra=BADCHECK)
    r, prod = fake_production(tmp_path, extra=BADCHECK)
    assert r.returncode == 1 and "failed twice already" in r.stdout, r.stdout
    r, prod = fake_production(tmp_path, extra=BADCHECK + '\nPRODUCTION[2]["timeout_s"] = 60')
    assert "== s3" in r.stdout and "failed twice already" not in r.stdout, r.stdout
    r, prod = fake_production(tmp_path, extra='PRODUCTION[2]["timeout_s"] = 60')
    assert r.returncode == 0 and "counted as done" in r.stdout and "\n== " not in r.stdout, r.stdout
    s3 = rec(prod, "s3")
    assert s3["ok"] and s3["check"] == {"pass": True} and s3["values"] == {"w": 6}
