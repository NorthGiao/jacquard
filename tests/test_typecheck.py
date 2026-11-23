"""lint's type propagation (jacq/typecheck.py). E only for category pairs confirmed by on-site probes to go broken; what should pass (builds on-site) must pass."""
import types
from jacq.lint import lint_spec, lint_job

ctl = lambda n, t: {"name": n, "type": t}


def codes(spec, dummy_vi, level=None, job_types=None):
    fs = lint_spec(spec, {}, dummy_vi, offline=True, job_types=job_types)
    return {(f.level, f.code) for f in fs if level is None or f.level == level}, fs


def one_fn(fn, ins, out_t, wires, outs=None):
    return {"controls": [ctl(n, t) for n, t in ins], "indicators": [ctl("o", out_t)],
            "functions": [{"name": "f", "function": fn}], "wires": wires + [[f"f.{outs}", "o"]] if outs else wires}


def test_bool_into_dbl_indicator_is_E(dummy_vi, tmp_queue):
    """Probe: the bool output of Greater?(dbl, dbl) wired to a dbl indicator → broken"""
    sp = one_fn("Greater?", [("a", "dbl"), ("b", "dbl")], "dbl", [["a", "f.x"], ["b", "f.y"]], "x > y?")
    c, _ = codes(sp, dummy_vi, "E")
    assert ("E", "TYPE") in c


def test_number_into_string_terminal_is_E(dummy_vi, tmp_queue):
    """Probe: String Length.string ← dbl → broken"""
    sp = one_fn("String Length", [("a", "dbl")], "i32", [["a", "f.string"]], "length")
    assert ("E", "TYPE") in codes(sp, dummy_vi, "E")[0]


def test_scalar_into_array_terminal_is_E(dummy_vi, tmp_queue):
    """Probe: Array Size.array ← dbl scalar → broken"""
    sp = one_fn("Array Size", [("a", "dbl")], "i32", [["a", "f.array"]], "size(s)")
    assert ("E", "TYPE") in codes(sp, dummy_vi, "E")[0]


def test_counterintuitive_ok_cases_pass(dummy_vi, tmp_queue):
    """Probe: And(dbl, dbl), To Upper Case(dbl) build on-site -- must not report errors"""
    for sp in (one_fn("And", [("a", "dbl"), ("b", "dbl")], "dbl", [["a", "f.x"], ["b", "f.y"]], "x .and. y?"),
               one_fn("To Upper Case", [("a", "dbl")], "dbl", [["a", "f.string"]], "all upper case string")):
        c, fs = codes(sp, dummy_vi)
        assert not [x for x in c if x[0] in "EW" and x[1].startswith("TYPE")], [str(f) for f in fs]


def test_numeric_coercion_passes(dummy_vi, tmp_queue):
    """i32 + dbl → dbl indicator; u8 - dbl: automatic conversion between numerics, not reported"""
    sp = one_fn("Subtract", [("a", "u8"), ("b", "dbl")], "i32", [["a", "f.x"], ["b", "f.y"]], "x-y")
    assert not [x for x in codes(sp, dummy_vi)[0] if x[1].startswith("TYPE")]


def test_build_array_append_keeps_dims(dummy_vi, tmp_queue):
    """Build Array(dbl[], dbl) is append → dbl[] (like the laps array); Build Array(dbl, dbl) → dbl[]"""
    sp = {"controls": [ctl("arr", "dbl[]"), ctl("x", "dbl")], "indicators": [ctl("o", "dbl[]"), ctl("o2", "dbl[]")],
          "functions": [{"name": "b", "function": "Build Array", "size": 2}, {"name": "b2", "function": "Build Array", "size": 2}],
          "wires": [["arr", "b.element[1]"], ["x", "b.element[2]"], ["b.appended array", "o"],
                    ["x", "b2.element[1]"], ["x", "b2.element[2]"], ["b2.appended array", "o2"]]}
    c, fs = codes(sp, dummy_vi)
    assert not [x for x in c if x[1].startswith(("TYPE", "DIMS"))], [str(f) for f in fs]


def test_subvi_output_types_resolve_dims(dummy_vi, tmp_queue):
    """The output of a subVI of this job is a scalar → becomes 1-D leaving the For, no DIMS?"""
    sub = {"controls": [ctl("x", "dbl")], "indicators": [ctl("y", "dbl")], "functions": [{"name": "f", "function": "Increment"}],
           "wires": [["x", "f.x"], ["f.x+1", "y"]]}
    main = {"controls": [ctl("xs", "dbl[]")], "indicators": [ctl("ys", "dbl[]")],
            "structures": [{"name": "L", "type": "for_loop", "subvis": [{"name": "s", "path": "D:\\x\\sub.vi"}]}],
            "wires": [["xs", "s.x"], ["s.y", "ys"]]}
    job = types.ModuleType("jobs.tc_sub")
    job.OUT = "D:\\agent_sandbox\\lv_agentic\\write2\\jobs\\tc_sub"
    job.VIS = [{"name": "sub", "spec": sub, "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]},
               {"name": "main", "spec": main, "tests": [{"inputs": {"xs": [1]}, "expect": {"ys": [2]}}]}]
    fs = lint_job(job, offline=True)
    assert not [f for f in fs if f.code.startswith(("DIMS", "TYPE")) and f.level in "EW"], [str(f) for f in fs]


def test_unknown_input_gives_unknown_output(dummy_vi, tmp_queue):
    """One input comes from a source of unknown type (vilib subVI) → the output of Add isn't inferred (mcpi randu: must not misjudge it as a scalar based on the other scalar input)"""
    sp = {"controls": [ctl("n", "i32")], "indicators": [ctl("u", "dbl[]")], "constants": [{"name": "h", "type": "dbl", "value": 0.5}],
          "subvis": [{"name": "nz", "path": "C:\\vi.lib\\noise.vi"}], "functions": [{"name": "add", "function": "Add"}],
          "wires": [["n", "nz.samples"], ["nz.out", "add.x"], ["h", "add.y"], ["add.x+y", "u"]]}
    fs = lint_spec(sp, {"noise.vi": {"samples", "out"}}, dummy_vi, offline=True)
    assert not [f for f in fs if f.code == "TYPE"], [str(f) for f in fs]


def test_select_branches_must_agree(dummy_vi, tmp_queue):
    sp = {"controls": [ctl("s", "bool"), ctl("a", "dbl"), ctl("b", "string")], "indicators": [ctl("o", "dbl")],
          "functions": [{"name": "f", "function": "Select"}],
          "wires": [["s", "f.s"], ["a", "f.t"], ["b", "f.f"], ["f.s? t:f", "o"]]}
    c, _ = codes(sp, dummy_vi)
    assert ("E", "TYPE") in c


def test_required_input_unwired(dummy_vi, tmp_queue):
    """typeprobe tp23: Add.y unwired → broken on-site → E REQ; those not confirmed on-site (Subtract.y) are still W REQ?"""
    sp = one_fn("Add", [("a", "dbl")], "dbl", [["a", "f.x"]], "x+y")
    assert ("E", "REQ") in codes(sp, dummy_vi)[0]
    sp = one_fn("Subtract", [("a", "dbl")], "dbl", [["a", "f.x"]], "x-y")
    assert ("W", "REQ?") in codes(sp, dummy_vi)[0]


def _via_select(src_t, ind):
    """Like jobs/typeprobe.py: the source goes through Select into an indicator (the writer doesn't allow wiring a control directly to an indicator)"""
    return {"controls": [ctl("a", src_t), ctl("k", "bool")], "indicators": [ind], "functions": [{"name": "s", "function": "Select"}],
            "wires": [["k", "s.s"], ["a", "s.t"], ["a", "s.f"], ["s.s? t:f", ind["name"]]]}


def test_typeprobe_pairs_promoted(dummy_vi, tmp_queue):
    """typeprobe tp01–tp04/tp37/tp38 (broken on-site): path/bool/num ↔ string/path report E; tp39 scalar into a Waveform Chart builds"""
    for src, dst in (("path", "string"), ("string", "path"), ("bool", "string"), ("string", "bool"), ("dbl", "path"), ("path", "dbl")):
        assert ("E", "TYPE") in codes(_via_select(src, ctl("o", dst)), dummy_vi)[0], (src, dst)
    sp = _via_select("dbl", {"name": "o", "type": "dbl[]", "style": "Waveform Chart"})
    c, fs = codes(sp, dummy_vi)
    assert not [x for x in c if x[1].startswith(("TYPE", "DIMS"))], [str(f) for f in fs]


def test_typeprobe_oracle_agrees():
    """If the on-site report of typeprobe has been pulled back: lint predictions agree with on-site 39/39 (no more W)"""
    import io, contextlib, pytest
    from jacq import oracle
    from jacq.config import job_local_dir
    if not (job_local_dir("typeprobe") / "job_report.json").exists():
        pytest.skip("no on-site typeprobe report (private/)")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        oracle.job_main("typeprobe")
    assert "verdicts agree 39/39" in buf.getvalue(), buf.getvalue()[-400:]


def test_lint_expect_downgrades_and_detects_regression(tmp_queue):
    bad = {"controls": [ctl("a", "dbl")], "indicators": [ctl("o", "dbl")], "functions": [{"name": "f", "function": "String Length"}],
           "wires": [["a", "f.string"], ["f.length", "o"]]}
    job = types.ModuleType("jobs.tc_exp")
    job.OUT = "D:\\agent_sandbox\\lv_agentic\\write2\\jobs\\tc_exp"
    job.VIS = [{"name": "bad", "spec": bad, "tests": [{"inputs": {"a": 1}, "expect": {"o": 1}}]}]
    job.LINT_EXPECT = {"bad": ["TYPE"]}
    fs = lint_job(job, offline=True)
    assert not [f for f in fs if f.level == "E"] and any(f.code == "TYPE" and f.level == "I" for f in fs)
    job.LINT_EXPECT = {"bad": ["TYPE", "DIMS"]}
    assert any(f.code == "EXPECT-MISS" for f in lint_job(job, offline=True))
    job.LINT_EXPECT = {"bad": ["TYPE", "MULTI"]}   # only predictive codes may be downgraded
    assert any(f.code == "EXPECT-BAD" and f.level == "E" for f in lint_job(job, offline=True))


def test_probe_oracle_no_false_broken():
    """If the private corpus private/lab_corpus/probes/ is present: what lint predicts as broken is really broken on-site (0 false alarms)"""
    import pytest
    from jacq import oracle
    from jacq.config import LAB_CORPUS
    if not (LAB_CORPUS / "probes").is_dir():
        pytest.skip("no private probe corpus")
    assert oracle.probes_main() == 0


def test_build_only_job_downgrades_predictive_only(tmp_queue):
    from jacq.lint import lint_job, load_job
    fs = lint_job(load_job("typeprobe"), offline=True)
    assert not [f for f in fs if f.level == "E"]
    assert sum(f.msg.startswith("[BUILD_ONLY]") for f in fs) >= 8
    assert not [f for f in fs if f.code == "NOTEST"]


def _case(sel_t, cases):
    return {"controls": [ctl("x", sel_t)], "indicators": [ctl("y", "i32")],
            "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
                dict(c, constants=[{"name": f"c{i}", "type": "i32", "value": i}]) for i, c in enumerate(cases)]}],
            "wires": [["x", "C.selector"]] + [[f"c{i}", "t"] for i in range(len(cases))] + [["t", "y"]]}


def test_case_value_checks(dummy_vi, tmp_queue):
    EN = {"enum": ["A", "B"]}
    # typeprobe tp33–tp35 broken on-site → E; string selectors not confirmed on-site → W
    assert ("E", "CASE-DUP") in codes(_case("i32", [{"value": 0}, {"value": 0}, {"value": 1, "default": True}]), dummy_vi)[0]
    assert ("E", "CASE-DEFAULT") in codes(_case("i32", [{"value": 0}, {"value": 1}]), dummy_vi)[0]
    assert ("E", "CASE-VAL") in codes(_case(EN, [{"value": "A"}, {"value": "Z", "default": True}]), dummy_vi)[0]
    assert ("W", "CASE-DEFAULT?") in codes(_case("string", [{"value": "a"}, {"value": "b"}]), dummy_vi)[0]
    # pass: enum fully covered without default; bool with both true and false; integer range strings (upstream case_multi passed on-site)
    for sp in (_case(EN, [{"value": "A"}, {"value": "B"}]), _case("bool", [{"value": True}, {"value": False}]),
               _case("i32", [{"value": "1..3"}, {"value": "5, 7, 9.."}, {"value": 0, "default": True}])):
        c, fs = codes(sp, dummy_vi)
        assert not [x for x in c if x[1].startswith("CASE")], [str(f) for f in fs]


def test_enum_items_differ_warned(dummy_vi, tmp_queue):
    sp = {"constants": [{"name": "c", "type": {"enum": ["A", "B"]}, "value": "A"}], "indicators": [ctl("o", {"enum": ["A", "C"]})],
          "functions": [{"name": "s", "function": "Select"}], "controls": [ctl("k", "bool")],
          "wires": [["k", "s.s"], ["c", "s.t"], ["c", "s.f"], ["s.s? t:f", "o"]]}
    assert ("W", "TYPE?") in codes(sp, dummy_vi)[0]


def test_case_tunnel_join_mismatch(dummy_vi, tmp_queue):
    sp = {"controls": [ctl("b", "bool")], "indicators": [ctl("o", "dbl")],
          "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
              {"value": True, "constants": [{"name": "c0", "type": "dbl", "value": 1}]},
              {"value": False, "constants": [{"name": "c1", "type": "string", "value": "x"}]}]}],
          "wires": [["b", "C.selector"], ["c0", "t"], ["c1", "t"], ["t", "o"]]}
    assert ("E", "TYPE") in codes(sp, dummy_vi)[0]


def test_property_value_write_checked(dummy_vi, tmp_queue):
    sp = {"controls": [ctl("x", "string")], "indicators": [ctl("led", "dbl")], "constants": [{"name": "k", "type": "bool", "value": True}],
          "functions": [{"name": "s", "function": "Select"}],
          "property_nodes": [{"name": "p", "control": "led", "properties": ["Value"], "write": True}],
          "wires": [["k", "s.s"], ["x", "s.t"], ["x", "s.f"], ["s.s? t:f", "p.Value"]]}
    assert ("E", "TYPE") in codes(sp, dummy_vi)[0]
