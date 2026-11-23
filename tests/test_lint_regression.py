"""lint must not report E for VIs already built on-site (false-alarm regression): the jobs in jobs/verified.json, and the 28 cases of shlim-t upstream live_vi_create (28/28 on-site)."""
import ast
from pathlib import Path

import pytest

from jacq.config import REPO
from jacq.lint import lint_job, lint_spec, load_job

import json

UPSTREAM = Path(__file__).parent / "data" / "upstream_live_vi_create.py.txt"   # MIT, shlim-t/labview-mcp tests/live_vi_create.py verbatim


EXAMPLES = ("gallery", "mcpi")   # shipped examples, all verified on-site; jobs/verified.json itself is local (gitignored)


def _regression_jobs():
    vf = REPO / "jobs" / "verified.json"
    rec = json.loads(vf.read_text(encoding="utf-8")) if vf.exists() else {}
    return sorted({*EXAMPLES, *(j for j in rec if (REPO / "jobs" / f"{j}.py").exists())})


@pytest.mark.parametrize("job", _regression_jobs())
def test_verified_jobs_have_no_E(job, tmp_queue):
    es = [str(f) for f in lint_job(load_job(job), offline=True) if f.level == "E"]
    assert not es, es


def _upstream_cases():
    env = {"D": "C:/tmp/lvmcp", "os": __import__("os")}
    for node in ast.parse(UPSTREAM.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("TS", "CASES", "UI_CASES"):
            exec(compile(ast.Module([node], []), "u", "exec"), env)

    def fill(o):
        if isinstance(o, dict):
            return {k: fill(v) for k, v in o.items()}
        if isinstance(o, list):
            return [fill(v) for v in o]
        return o.replace("{D}", "C:/tmp/lvmcp") if isinstance(o, str) else o
    return {n: fill(v[0]) for g in ("CASES", "UI_CASES") for n, v in env[g].items()}


def test_upstream_live_cases_have_no_E(dummy_vi, tmp_queue):
    cases = _upstream_cases()
    assert len(cases) == 28
    # SUBVI?: the subVI of the upstream case (sub_scale.vi) is built first in its own test, its terminals aren't given here → not a false alarm
    bad = {n: [str(f) for f in lint_spec(sp, {}, dummy_vi, offline=True, job="upstream", vi=n)
               if f.level == "E" and f.code != "SUBVI?"]
           for n, sp in cases.items()}
    assert not {n: v for n, v in bad.items() if v}, bad
