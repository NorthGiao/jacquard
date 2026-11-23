import json, subprocess, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "jacq_hook.py"


def run(ev):
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(ev), capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def test_outside_repo_is_silent():
    assert run({"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}}) == (0, "", "")


def test_protected_file_blocked():
    rc, _, err = run({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                      "tool_input": {"file_path": str(REPO / "catalog" / "terminals_confirmed.json")}})
    assert rc == 2 and "probe_terms" in err


def test_protected_files_are_matched_by_file_not_by_spelling(tmp_path, monkeypatch):
    """Protected files are compared by file system (samefile): hard links and spellings with different case (macOS / Windows / some mounted volumes) resolve to paths different from those in PROTECTED,
    and must be blocked too. Hard links are created under tmp (the hook's test comes before "is it in the repo"); case spellings can only be tested when the file system is case-insensitive."""
    import importlib.util, os
    spec = importlib.util.spec_from_file_location("jacq_hook_t", HOOK)
    H = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(H)
    target = tmp_path / "Protected.json"
    target.write_text("{}")
    monkeypatch.setattr(H, "PROTECTED", {target: "why"})
    link = tmp_path / "link.json"
    os.link(target, link)
    assert H.protected(target) == target and H.protected(link.resolve()) == target
    assert H.protected(tmp_path / "other.json") is None and H.protected(tmp_path / "nope" / "x.json") is None
    for v in (REPO / "jobs" / "verified.json", REPO / "catalog" / "terminals_confirmed.json"):
        up = v.parent.with_name(v.parent.name.upper()) / v.name.upper()
        if up.exists():   # case-insensitive file system: the all-uppercase spelling is the same file
            rc, _, err = run({"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(up)}})
            assert rc == 2 and "Refusing manual edit" in err, (up, rc, err)
    (tmp_path / "other.json").write_text("{}")
    boom = lambda exc: (lambda a, b: (_ for _ in ()).throw(exc))   # noqa: E731
    monkeypatch.setattr(H.os.path, "samefile", boom(PermissionError("no")))
    assert H.protected(tmp_path / "other.json") == target   # can't compare (permissions etc.): treat as protected, refusing on error is safer than allowing
    monkeypatch.setattr(H.os.path, "samefile", boom(FileNotFoundError("gone")))
    assert H.protected(tmp_path / "other.json") is None     # the protected file was deleted: not the same file


def test_only_files_directly_under_jobs_are_linted():
    """Things like jobs/old/gallery.py are not jobs: the hook must not use the stem to lint jobs/gallery.py (feedback labelled with the former, content from the latter)."""
    for sub in ("old", "a/b"):
        assert run({"hook_event_name": "PostToolUse", "tool_name": "Write",
                    "tool_input": {"file_path": str(REPO / "jobs" / sub / "zz_hooksub.py")}}) == (0, "", "")


def test_hook_lints_the_file_that_was_edited_not_another_one(tmp_path):
    """The stem of jobs/x.py.py is x.py: if the hook passed the stem to lint, lint would take it once more to get x and lint another file, jobs/x.py.
    The hook passes the real path, and lint gives JOB-ARG; the file itself doesn't need to exist (the hook only reads it after lint passes)."""
    rc, _, err = run({"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": str(REPO / "jobs" / "zz_hookdot.py.py")}})
    assert rc == 2 and "JOB-ARG" in err and "zz_hookdot.py" in err and "is invalid" in err, (rc, err)


def test_bad_job_reports_errors(tmp_path):
    p = REPO / "jobs" / "zz_hooktest.py"
    for c in (p.parent / "__pycache__").glob(f"{p.stem}.*"):   # left over by old versions (when loaded by import), cleared first so that "none left this time" can be tested below
        c.unlink()
    p.write_text('from lablib import BASE\nOUT = BASE / "write2" / "jobs" / "zz_hooktest"\n'
                 'VIS = [{"name": "a", "spec": {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],'
                 ' "functions": [{"name": "f", "function": "Increment"}], "wires": [["x", "f.x"], ["f.x+2", "y"]]},'
                 ' "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]}]\n', encoding="utf-8")
    try:
        rc, _, err = run({"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": str(p)}})
    finally:
        p.unlink()
    assert rc == 2 and "TERM" in err and "x+1" in err
    assert not list((p.parent / "__pycache__").glob(f"{p.stem}.*"))   # jobs are loaded from source (jobguard.load_job), no .pyc is left in the real jobs/
