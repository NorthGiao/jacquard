"""Every job in the repo must lint with 0 E (failtest too: its failures only show up on-site); a copy of the template passes as-is."""
import pytest
from jacq.config import JOBS
from jacq.lint import lint_job, load_job

NAMES = sorted(p.stem for p in JOBS.glob("*.py") if not p.stem.startswith("_"))


@pytest.mark.parametrize("name", NAMES)
def test_job_lints_clean(name, tmp_queue):
    fs = lint_job(load_job(name), offline=True)
    assert not [f for f in fs if f.level == "E"], "\n".join(str(f) for f in fs if f.level == "E")


def test_template_copy_lints_clean(tmp_queue):
    p = JOBS / "zz_tpl_copy.py"
    for c in (JOBS / "__pycache__").glob("zz_tpl_copy.*"):   # left over by old versions (when loaded by import), cleared first so that "none left this time" can be tested below
        c.unlink()
    p.write_text((JOBS / "_template.py").read_text(encoding="utf-8").replace("<job>", "zz_tpl_copy"), encoding="utf-8")
    try:
        fs = lint_job(load_job("zz_tpl_copy"))
    finally:
        p.unlink()
    assert not [f for f in fs if f.level in "EW"], "\n".join(map(str, fs))
    assert not list((JOBS / "__pycache__").glob("zz_tpl_copy.*"))   # jobs are loaded from source (jobguard.load_job), no .pyc is left in the real jobs/


def test_path_double_backslash_warned(tmp_queue):
    """r"D:\\a" transcribed as "D:\\\\a" (two real backslashes), LabVIEW normalizes the path to one → the test is bound to fail."""
    import types
    from pathlib import PureWindowsPath
    spec = {"controls": [{"name": "dir", "type": "path"}, {"name": "name", "type": "string"}],
            "indicators": [{"name": "p", "type": "path"}],
            "functions": [{"name": "bp", "function": "Build Path"}],
            "wires": [["dir", "bp.base path"], ["name", "bp.name or relative path"], ["bp.appended path", "p"]]}
    out = PureWindowsPath(r"D:\agent_sandbox\lv_agentic\write2\jobs\zz")
    bad = types.SimpleNamespace(OUT=out, __name__="zz", VIS=[{"name": "v", "spec": spec, "tests": [
        {"inputs": {"dir": "D:\\\\x", "name": "a.txt"}, "expect": {"p": "D:\\\\x\\\\a.txt"}}]}])
    assert any(f.code == "PATHESC" for f in lint_job(bad, offline=True))
    good = types.SimpleNamespace(OUT=out, __name__="zz", VIS=[{"name": "v", "spec": spec, "tests": [
        {"inputs": {"dir": r"D:\x", "name": "a.txt"}, "expect": {"p": r"D:\x\a.txt"}}]}])
    assert not any(f.code == "PATHESC" for f in lint_job(good, offline=True))
