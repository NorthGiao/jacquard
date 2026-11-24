"""One case per pitfall hit: lint must report it (code + keyword), and what should pass must pass. Origins in docs/pitfalls.md."""
import pytest
from jacq.lint import lint_spec

LOOP = lambda body: {"name": "L", "type": "for_loop", **body}
inc = lambda n="a": {"name": n, "function": "Increment"}
CASES = {
    "GEN property node wired to a tunnel (gives the in-frame form)": ({"controls": [{"name": "b", "type": "bool"}], "indicators": [{"name": "led", "type": "bool"}],
        "structures": [{"name": "C", "type": "case", "output_tunnels": ["o"], "cases": [
            {"value": True, "constants": [{"name": "t", "type": "bool", "value": True}]},
            {"value": False, "default": True, "constants": [{"name": "f", "type": "bool", "value": False}]}]}],
        "property_nodes": [{"name": "p", "control": "led", "properties": ["Value"], "write": True}],
        "wires": [["b", "C.selector"], ["t", "o"], ["f", "o"], ["o", "p.Value"]]}, "E", "each case frame"),
    "AUTOIDX scalar indicator wired to a source inside the loop": ({"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "constants": [{"name": "n", "type": "i32", "value": 3}], "structures": [LOOP({"functions": [inc()]})],
        "wires": [["n", "L.N"], ["x", "a.x"], ["a.x+1", "y"]]}, "E", "AUTOIDX"),
    "DIMS 1-D indicator wired to an array source inside the loop (on-site failtest/broken)": ({"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32[]"}],
        "constants": [{"name": "n", "type": "i32", "value": 1}],
        "structures": [LOOP({"constants": [{"name": "J", "type": "i32[]", "value": [0, 1]}], "shift_registers": ["s"],
                             "functions": [{"name": "a", "function": "Add"}]})],
        "wires": [["n", "L.N"], ["x", "s.left"], ["s.left", "s.right"], ["J", "a.x"], ["s.left", "a.y"], ["a.x+y", "y"]]}, "E", "DIMS"),
    "STOP-ONCE stop button wired directly to the While stop terminal (no event structure)": ({"controls": [{"name": "stop", "type": "bool"}],
        "indicators": [{"name": "cnt", "type": "i32"}],
        "structures": [{"name": "W", "type": "while_loop", "functions": [inc()]}],
        "wires": [["W.i", "a.x"], ["stop", "W.stop"], ["a.x+1", "cnt"]]}, "E", "STOP-ONCE"),
    "STYLE-LINK styled control referenced only by property nodes in event frames": ({"controls": [{"name": "go", "type": "bool"}, {"name": "stop", "type": "bool"}],
        "indicators": [{"name": "led", "type": "bool", "style": "Round LED"}],
        "structures": [{"name": "W", "type": "while_loop", "structures": [{"name": "E", "type": "event", "events": [
            {"control": "go", "constants": [{"name": "t", "type": "bool", "value": True}],
             "property_nodes": [{"name": "p", "control": "led", "properties": ["Value"], "write": True}]},
            {"control": "stop"}]}]}],
        "wires": [["t", "p.Value"], ["stop", "W.stop"]]}, "W", "STYLE-LINK"),
    "BANNED Random Number": ({"indicators": [{"name": "y", "type": "dbl"}], "functions": [{"name": "r", "function": "Random Number (0-1)"}],
        "wires": [["r.number (0 to 1)", "y"]]}, "E", "BANNED"),
    "TERM wrong terminal name (gives the closest)": ({"controls": [{"name": "b", "type": "bool[]"}], "indicators": [{"name": "s", "type": "i32"}],
        "functions": [{"name": "c", "function": "Boolean To (0,1)"}, {"name": "sum", "function": "Add Array Elements"}],
        "wires": [["b", "c.Boolean"], ["c.0,1", "sum.numeric array"], ["sum.sum", "s"]]}, "E", "TERM"),
    "UNVERIFIED unverified function": ({"controls": [{"name": "x", "type": "dbl"}], "indicators": [{"name": "y", "type": "dbl"}],
        "functions": [{"name": "f", "function": "Hyperbolic Arcsine"}], "wires": [["x", "f.x"], ["f.y", "y"]]}, "E", "UNVERIFIED"),
    "ARRSTYLE whole array changed to LED": ({"indicators": [{"name": "b", "type": "bool[][]", "style": "Round LED"}],
        "constants": [{"name": "k", "type": "bool[][]", "value": [[True]]}],
        "functions": [{"name": "t", "function": "Transpose 2D Array"}], "wires": [["k", "t.2D array"], ["t.transposed array", "b"]]}, "E", "ARRSTYLE"),
    "GEN only terminals, no nodes": ({"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}], "wires": [["x", "y"]]}, "E", "GEN"),
    "MULTI two wires into one sink": ({"controls": [{"name": "x", "type": "i32"}, {"name": "z", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "functions": [inc()], "wires": [["x", "a.x"], ["z", "a.x"], ["a.x+1", "y"]]}, "E", "MULTI"),
    "ZHSUB subVI terminal names are Chinese": ({"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "subvis": [{"name": "s", "path": "D:\\x\\zh_sub.vi"}], "wires": [["x", "s.输入"], ["s.输出", "y"]]}, "E", "ASCII"),
    "WHILE-STOP While stop terminal not wired": ({"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "structures": [{"name": "W", "type": "while_loop", "functions": [inc()]}], "wires": [["x", "a.x"], ["a.x+1", "y"]]}, "E", "WHILE-STOP"),
    "GEN misspelled node name gives a suggestion": ({"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
        "functions": [{"name": "incr", "function": "Increment"}], "wires": [["x", "inc.x"], ["incr.x+1", "y"]]}, "E", "incr"),
}
OK = {
    "property node writing Value drives the indicator (not UNDRIVEN)": {"controls": [{"name": "stop", "type": "bool"}], "indicators": [{"name": "led", "type": "bool"}],
        "constants": [{"name": "t", "type": "bool", "value": True}],
        "structures": [{"name": "W", "type": "while_loop",
                        "property_nodes": [{"name": "p", "control": "led", "properties": ["Value"], "write": True},
                                           {"name": "ps", "control": "stop", "properties": ["Value"]}]}],
        "wires": [["t", "p.Value"], ["ps.Value", "W.stop"]]},   # the stop button is read via a property node inside the loop (a direct wire would be blocked by STOP-ONCE)
    "Chinese labels (allowed in top-level VIs)": {"controls": [{"name": "x", "type": "i32", "label": "输入"}], "indicators": [{"name": "y", "type": "i32"}],
        "functions": [inc()], "wires": [["x", "a.x"], ["a.x+1", "y"]]},
    "case structure output tunnel with one wire per frame (not MULTI)": {"controls": [{"name": "st", "type": {"enum": ["A", "B"]}}], "indicators": [{"name": "v", "type": "i32"}],
        "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
            {"value": "A", "constants": [{"name": "c0", "type": "i32", "value": 0}]},
            {"value": "B", "default": True, "constants": [{"name": "c1", "type": "i32", "value": 1}]}]}],
        "wires": [["st", "C.selector"], ["c0", "t"], ["c1", "t"], ["t", "v"]]},
    "While + shift register + stop": {"controls": [{"name": "n", "type": "i32"}], "indicators": [{"name": "cnt", "type": "i32"}],
        "constants": [{"name": "z", "type": "i32", "value": 0}],
        "structures": [{"name": "W", "type": "while_loop", "shift_registers": ["s"],
                        "functions": [inc("inc"), {"name": "ge", "function": "Greater Or Equal?"}]}],
        "wires": [["z", "s.left"], ["s.left", "inc.x"], ["inc.x+1", "s.right"], ["inc.x+1", "ge.x"],
                  ["n", "ge.y"], ["ge.x >= y?", "W.stop"], ["s.right", "cnt"]]},
}


@pytest.mark.parametrize("name", CASES)
def test_pitfall_is_caught(name, dummy_vi, tmp_queue):
    spec, lvl, needle = CASES[name]
    fs = lint_spec(spec, {}, dummy_vi)
    hits = [f for f in fs if f.level == lvl and (needle == f.code or needle in f.msg or needle in f.fix)]
    assert hits, [str(f) for f in fs]
    assert all(f.fix for f in fs if f.level == "E"), "every E must carry a fix"


@pytest.mark.parametrize("name", OK)
def test_valid_spec_passes(name, dummy_vi, tmp_queue):
    fs = lint_spec(OK[name], {}, dummy_vi)
    assert not [f for f in fs if f.level == "E"], [str(f) for f in fs]


def test_offline_queues_unverified(dummy_vi, tmp_queue):
    import json
    spec = CASES["UNVERIFIED unverified function"][0]
    fs = lint_spec(spec, {}, dummy_vi, offline=True, job="t")
    assert not [f for f in fs if f.level == "E"]
    assert any(f.code == "UNVERIFIED" and f.level == "W" for f in fs)
    q = json.loads(tmp_queue.read_text())
    assert "Hyperbolic Arcsine" in q["items"] and "t" in q["items"]["Hyperbolic Arcsine"]["jobs"]


def test_function_name_with_quote_is_an_error_and_never_queued(dummy_vi, tmp_queue):
    """Names in the probe queue get spliced by scripts/probe_terms.sh --queue into a command sent over SSH to the LabVIEW machine: quotes / $ / backticks / control characters may not enter the queue; E in both offline and strict mode."""
    from jacq import catalog as C
    for name in ("Foo'; calc; '", 'a"b', "a$b", "a`b"):   # newlines / control characters are already refused by the writer itself (GEN), they never reach the queueing step
        spec = {"controls": [{"name": "x", "type": "dbl"}], "indicators": [{"name": "y", "type": "dbl"}],
                "functions": [{"name": "f", "function": name}], "wires": [["x", "f.x"], ["f.x+1", "y"]]}
        for offline in (True, False):
            fs = lint_spec(spec, {}, dummy_vi, offline=offline)
            assert any(f.level == "E" and f.code == "FUNC?" and "is invalid" in f.msg for f in fs), (name, offline, [str(f) for f in fs])
    assert not tmp_queue.exists() or not C.queue_load()["items"]


def test_offline_typo_stays_error(dummy_vi, tmp_queue):
    spec = {"controls": [{"name": "x", "type": "dbl"}], "indicators": [{"name": "y", "type": "dbl"}],
            "functions": [{"name": "f", "function": "Incremen"}], "wires": [["x", "f.x"], ["f.x+1", "y"]]}
    fs = lint_spec(spec, {}, dummy_vi, offline=True)
    assert any(f.level == "E" and f.code == "FUNC?" and "Increment" in f.fix for f in fs), [str(f) for f in fs]
    spec2 = CASES["TERM wrong terminal name (gives the closest)"][0]
    fs2 = lint_spec(spec2, {}, dummy_vi, offline=True)
    assert any(f.level == "E" and f.code == "TERM" and "0, 1" in f.fix for f in fs2), [str(f) for f in fs2]


def test_writer_keeps_style_and_default(dummy_vi):
    """The writer must carry style/default from the spec into the generated result, otherwise things like a Waveform Graph silently become arrays."""
    import sys
    from jacq.config import WRITER_SRC
    sys.path.insert(0, str(WRITER_SRC))
    from labview_mcp import gen
    spec = {"controls": [{"name": "a", "type": "i32", "default": 3, "label": "增益"},
                         {"name": "b", "type": "bool", "style": "Stop Button"}],
            "indicators": [{"name": "y", "type": "dbl[]", "style": "Waveform Graph"}],
            "functions": [{"name": "f", "function": "Increment"}],
            "wires": [["a", "f.x"]]}
    s = gen.build(spec)
    assert s.defaults == {"a": 3}
    assert s.styles == {"b": "Stop Button", "y": "Waveform Graph"}


# ---------------------------------------------------------------- shapes, stop terminals, job arguments and hardware I/O
import copy  # noqa: E402
import types  # noqa: E402
from pathlib import PureWindowsPath  # noqa: E402

from jacq import lint as L  # noqa: E402

_OUT = PureWindowsPath(r"D:\agent_sandbox\lv_agentic\write2\jobs\zz")
_GOOD = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
         "functions": [{"name": "a", "function": "Increment"}], "wires": [["x", "a.x"], ["a.x+1", "y"]]}


def _job(spec=_GOOD, **v):
    m = types.ModuleType("jobs.zz")
    m.OUT = _OUT
    m.VIS = [{"name": "v", "spec": spec, "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}], **v}]
    return m


def _codes(fs, level="E"):
    return sorted({f.code for f in fs if f.level == level})


SHAPES = {
    "subVI path forgot str()": (dict(_GOOD, subvis=[{"name": "s", "path": _OUT / "sub.vi"}]), {}, "str(OUT"),
    "subVI missing path": (dict(_GOOD, subvis=[{"name": "s"}]), {}, "path"),
    "control missing name": (dict(_GOOD, controls=[{"type": "i32"}]), {}, "name"),
    "structure missing type": (dict(_GOOD, structures=[{"name": "L"}]), {}, "type"),
    "controls written as dict": (dict(_GOOD, controls={"x": "i32"}), {}, "list"),
    "spec is None": (None, {}, "dict"),
    "tests written as dict": (_GOOD, {"tests": {"inputs": {"x": 1}, "expect": {"y": 2}}}, "tests"),
    "expect written as list": (_GOOD, {"tests": [{"inputs": {"x": 1}, "expect": ["y"]}]}, "expect"),
    "runs written as list": (_GOOD, {"runs": [{"inputs": {"x": 1}}]}, "runs"),
    "function in a frame missing name": (dict(_GOOD, structures=[{"name": "C", "type": "case", "cases": [{"value": 0, "functions": [{"function": "Add"}]}]}]), {}, "name"),
}


@pytest.mark.parametrize("name", SHAPES)
def test_shape_errors_get_a_fix_not_a_traceback(name, tmp_queue):
    """These mistakes must not crash lint: each one is E SHAPE + a fix."""
    spec, v, kw = SHAPES[name]
    fs = L.lint_job(_job(spec, **v), offline=True)
    hit = [f for f in fs if f.level == "E" and f.code == "SHAPE"]
    assert hit and all(f.fix for f in hit) and any(kw in f.msg + f.fix for f in hit), [str(f) for f in fs]


def test_shape_error_in_one_vi_does_not_hide_others(tmp_queue):
    m = _job(dict(_GOOD, controls={"x": "i32"}))
    m.VIS.append({"name": "w", "spec": dict(_GOOD, wires=[["x", "a.x"], ["a.x+2", "y"]]), "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]})
    assert {"SHAPE", "TERM"} <= set(_codes(L.lint_job(m, offline=True)))
    m.VIS = {"v": _GOOD}
    assert _codes(L.lint_job(m, offline=True)) == ["SHAPE"]


def test_lint_main_never_shows_a_traceback(monkeypatch, capsys):
    """Situations lint itself didn't anticipate also get an E (LINT-BUG), no traceback."""
    monkeypatch.setattr(L, "load_job", lambda n: _job())
    monkeypatch.setattr(L, "lint_job", lambda job, offline=False: (_ for _ in ()).throw(KeyError("boom")))
    assert L.main(["zz", "--offline"]) == 1
    out = capsys.readouterr().out
    assert "E [LINT-BUG]" in out and "KeyError" in out and "Fix" in out and "Traceback" not in out


def _stop_spec(first):
    w = [["stop", "o.x"], ["stop", "W.stop"]]
    return {"controls": [{"name": "stop", "type": "bool"}], "indicators": [{"name": "y", "type": "bool"}],
            "structures": [{"name": "W", "type": "while_loop", "functions": [{"name": "o", "function": "Or"}], "shift_registers": ["s"]}],
            "wires": [["stop", "s.left"], ["s.left", "o.y"], ["o.x .or. y?", "s.right"], ["s.right", "y"]] + (w if first == "or" else w[::-1])}


def test_stop_once_does_not_depend_on_wire_order(dummy_vi):
    """The same stop button wired both elsewhere and to W.stop: whether E is reported doesn't depend on which wire is written first."""
    for first in ("or", "stop"):
        fs = lint_spec(_stop_spec(first), {}, dummy_vi, offline=True)
        assert [f.code for f in fs if f.level == "E"] == ["STOP-ONCE"], (first, [str(f) for f in fs])
        assert not [f for f in fs if f.code in ("READ-ONCE", "STOP-ONCE?")]


def test_stop_once_through_a_function_warns(dummy_vi):
    """stop → Or → W.stop ("stop or error"): a boolean control reports W STOP-ONCE?; a numeric control READ-ONCE as before."""
    spec = {"controls": [{"name": "stop", "type": "bool"}, {"name": "lim", "type": "i32"}], "indicators": [{"name": "n", "type": "i32"}],
            "structures": [{"name": "W", "type": "while_loop", "functions": [{"name": "o", "function": "Or"}, {"name": "g", "function": "Greater?"}]}],
            "wires": [["stop", "o.x"], ["W.i", "g.x"], ["lim", "g.y"], ["g.x > y?", "o.y"], ["o.x .or. y?", "W.stop"], ["W.i", "n"]]}
    fs = lint_spec(spec, {}, dummy_vi, offline=True)
    assert [(f.level, f.code) for f in fs if f.code.startswith(("STOP", "READ"))] == [("W", "STOP-ONCE?"), ("I", "READ-ONCE")], [str(f) for f in fs]
    assert not [f for f in fs if f.level == "E"]


STOP_PATHS = {
    "via a function outside the loop": {"functions": [{"name": "n", "function": "Not"}],
                 "structures": [{"name": "W", "type": "while_loop"}],
                 "wires": [["stop", "n.x"], ["n..not. x?", "W.stop"], ["W.i", "y"]]},
    "via a shift register": {"structures": [{"name": "W", "type": "while_loop", "shift_registers": ["r"]}],
               "wires": [["stop", "r.left"], ["r.left", "W.stop"], ["r.left", "r.right"], ["W.i", "y"]]},
    "via a case structure tunnel inside the loop": {"structures": [{"name": "W", "type": "while_loop", "structures": [
        {"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
            {"value": True, "constants": [{"name": "k1", "type": "bool", "value": True}]},
            {"value": False, "default": True, "constants": [{"name": "k0", "type": "bool", "value": False}]}]}]}],
        "wires": [["stop", "C.selector"], ["k1", "t"], ["k0", "t"], ["t", "W.stop"], ["W.i", "y"]]},
    "via the pass of a case frame": {"constants": [{"name": "sel", "type": "bool", "value": True}],   # pass is not in wires
        "structures": [{"name": "W", "type": "while_loop", "structures": [
        {"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
            {"value": True, "pass": [["stop", "t"]]},
            {"value": False, "default": True, "constants": [{"name": "k0", "type": "bool", "value": False}]}]}]}],
        "wires": [["sel", "C.selector"], ["k0", "t"], ["t", "W.stop"], ["W.i", "y"]]},
}


@pytest.mark.parametrize("name", STOP_PATHS)
def test_stop_once_through_other_paths_warns(name, dummy_vi):
    """A button wired to the stop terminal via a function outside the loop or a shift register must also get a STOP-related hint.
    Going through a case structure's selector doesn't count as "along the wires to the stop terminal" (the selector only decides which frame runs), only READ-ONCE is given."""
    spec = dict(STOP_PATHS[name], controls=[{"name": "stop", "type": "bool"}], indicators=[{"name": "y", "type": "i32"}])
    fs = lint_spec(spec, {}, dummy_vi, offline=True)
    assert not [f for f in fs if f.level == "E"], [str(f) for f in fs]
    got = [(f.level, f.code) for f in fs if f.code.startswith(("STOP", "READ"))]
    assert got == ([("I", "READ-ONCE")] if "case structure" in name else [("W", "STOP-ONCE?")]), [str(f) for f in fs]


def test_more_shapes_that_used_to_fall_through_to_lint_bug(tmp_queue):
    """These must report SHAPE (with a concrete fix), not fall through to main()'s catch-all LINT-BUG; shift_registers written as a string must not be misreported as WHILE-STOP either."""
    loop = {"name": "W", "type": "while_loop", "shift_registers": "s"}
    for spec, v, kw in ((dict(_GOOD, wires=None), {}, "wires"), (dict(_GOOD, structures=[loop]), {}, "shift_registers"),
                        (_GOOD, {"runs": {"actions": ["click"]}}, "actions")):
        fs = L.lint_job(_job(spec, **v), offline=True)
        assert _codes(fs) == ["SHAPE"] and any(kw in f.msg for f in fs), (kw, [str(f) for f in fs])
    m = _job()
    m.LINT_EXPECT = ["TYPE"]
    fs = L.lint_job(m, offline=True)
    assert _codes(fs) == ["SHAPE"] and "LINT_EXPECT" in fs[-1].msg + "".join(f.msg for f in fs)


@pytest.mark.parametrize("runs, kw", [
    ({"actions": [{"click": ["x"]}], "check": "c"}, "click"),
    ({"actions": [{"set": "x"}], "check": "c"}, "set"),                   # _act_to_labels on the LabVIEW machine would crash
    ({"actions": [{"read": "y"}], "check": "c"}, "read"),
    ({"actions": [{"wait": "1"}], "check": "c"}, "wait"),
    ({"actions": [{"press": "x"}], "check": "c"}, "press"),
    ({"actions": [{"click": "x", "wait": 1}], "check": "c"}, "one key"),
    ({"inputs": {"x": 1}, "repeat": "3", "check": "c"}, "repeat"),        # range("3") on the LabVIEW machine would crash
])
def test_bad_actions_and_repeat_get_shape(runs, kw, tmp_queue):
    """The value shape of each actions step and the type of repeat: lint reports SHAPE (with a fix), instead of leaving it to crash on-site."""
    m = _job()
    m.VIS[0]["runs"] = runs
    fs = L.lint_job(m, offline=True)
    assert "SHAPE" in _codes(fs) and any(kw in f.msg for f in fs if f.code == "SHAPE"), [str(f) for f in fs]
    assert "LINT-BUG" not in _codes(fs)


def test_tuples_get_shape_with_a_clear_fix(tmp_queue):
    """functions / wires etc. in the spec written as tuples raise TypeError downstream when concatenated as lists.
    So SHAPE is still reported, but the fix says explicitly "change it to a list"."""
    m = _job()
    m.VIS[0]["spec"] = dict(m.VIS[0]["spec"], functions=tuple(m.VIS[0]["spec"]["functions"]))
    fs = [f for f in L.lint_job(m, offline=True) if f.code == "SHAPE"]
    assert fs and "change it to a list" in fs[0].msg, [str(f) for f in fs]


def test_runs_without_any_assertion_warned(tmp_queue):
    """runs has no check and the VI has no tests → nothing is asserted on-site."""
    m = _job()
    m.VIS[0].pop("tests")
    m.VIS[0]["runs"] = {"inputs": {"x": 1}, "outputs": ["y"]}
    assert "RUN-NOCHECK" in _codes(L.lint_job(m, offline=True), "W")
    m.check_v = lambda v: {"pass": True}
    m.VIS[0]["runs"]["check"] = "check_v"
    assert "RUN-NOCHECK" not in _codes(L.lint_job(m, offline=True), "W")


LABELS = {
    "two controls with the same label": ([{"name": "x", "type": "dbl", "label": "Gain"}, {"name": "g", "type": "dbl", "label": "Gain"}], [{"name": "y", "type": "dbl"}]),
    "name of a Chinese-labelled control equals another label": ([{"name": "x", "type": "dbl", "label": "增益"}, {"name": "g", "type": "dbl", "label": "x"}], [{"name": "y", "type": "dbl"}]),
    "two identical Chinese labels": ([{"name": "x", "type": "dbl", "label": "增益"}, {"name": "g", "type": "dbl", "label": "增益"}], [{"name": "y", "type": "dbl"}]),
    "control and indicator with the same label": ([{"name": "x", "type": "dbl", "label": "v"}, {"name": "g", "type": "dbl"}], [{"name": "y", "type": "dbl", "label": "v"}]),
}


@pytest.mark.parametrize("name", LABELS)
def test_duplicate_labels_rejected(name, dummy_vi):
    """vi_job sets and reads values by label, the writer finds references by label, subVI terminal names = labels; with duplicates there's no telling which one is acted on."""
    ctl, ind = LABELS[name]
    spec = {"controls": ctl, "indicators": ind, "functions": [{"name": "a", "function": "Add"}],
            "wires": [[ctl[0]["name"], "a.x"], [ctl[1]["name"], "a.y"], ["a.x+y", ind[0]["name"]]]}
    assert "LABEL-DUP" in _codes(lint_spec(spec, {}, dummy_vi, offline=True))
    ctl[1]["label"] = "another"
    if name.startswith("control and"):
        ind[0]["label"] = "out"
    assert "LABEL-DUP" not in _codes(lint_spec(spec, {}, dummy_vi, offline=True))


def test_label_outside_lab_codepage_warned(dummy_vi, monkeypatch):
    spec = dict(_GOOD, controls=[{"name": "x", "type": "i32", "label": "안녕"}])
    assert "LABEL-CP" in _codes(lint_spec(spec, {}, dummy_vi, offline=True), "W")
    spec = dict(_GOOD, controls=[{"name": "x", "type": "i32", "label": "增益"}])
    assert "LABEL-CP" not in _codes(lint_spec(spec, {}, dummy_vi, offline=True), "W")
    monkeypatch.setattr(L, "LAB_CODEPAGE", "cp1252")
    assert "LABEL-CP" in _codes(lint_spec(spec, {}, dummy_vi, offline=True), "W")


def test_vi_name_must_be_a_plain_file_name(tmp_queue):
    """The VI name is the file name under OUT."""
    for bad in ("..\\..\\evil", "a/b", "x:y", "what?", " lead", "trail.", "x\ty", "x\n", "CON", "nul", "Com1.x", "LPT9", "v" * 101):
        m = _job()
        m.VIS[0]["name"] = bad
        assert "JOB-NAME" in _codes(L.lint_job(m, offline=True)), bad
    m = _job()
    m.VIS[0]["name"] = ""   # empty name: lint already gives SHAPE in the shape check (BAD_VI_NAME on the LabVIEW machine refuses it too, see the direct vi_job run cases in test_pipeline)
    assert "SHAPE" in _codes(L.lint_job(m, offline=True))
    m = _job()
    m.VIS[0]["name"] = "主程序"
    fs = L.lint_job(m, offline=True)
    assert "JOB-NAME?" in _codes(fs, "W") and not _codes(fs)
    for ok in ("w01_while_sr", "ga-8q", "A1", "a..b", "console", "com10", "auxiliary", "v" * 100):   # reserved names only match the whole name (including ones with a dotted suffix like "CON.x"), things like console don't
        m.VIS[0]["name"] = ok
        fs = L.lint_job(m, offline=True)
        assert not _codes(fs) and "JOB-NAME?" not in _codes(fs, "W"), ok


def test_job_name_and_out_use_the_lab_side_rule(tmp_queue):
    """job name not matching the LabVIEW machine's regex → E JOB-NAME; OUT only "ends right" but its root isn't BASE → E JOB-OUT (the LabVIEW machine requires equality)."""
    for bad in ("gal-x", "zz v2", "作业"):   # the module name takes what follows the last dot, so names with dots aren't tested
        m = _job()
        m.__name__ = f"jobs.{bad}"
        m.OUT = L.GUARD.job_out(L.REMOTE_BASE, bad)
        assert "JOB-NAME" in _codes(L.lint_job(m, offline=True)), bad
    m = _job()
    assert "JOB-OUT" not in _codes(L.lint_job(m, offline=True))
    m.OUT = PureWindowsPath(r"E:\\elsewhere\\write2\\jobs\\zz")
    assert "JOB-OUT" in _codes(L.lint_job(m, offline=True))


def test_job_arg_must_be_a_job_name_or_a_path_directly_under_jobs(capsys):
    """A command-line job argument written as x.py elsewhere, x.py.py and the like must not be silently taken as jobs/x (then lint would check a different file,
    and job_check's record key wouldn't match the file actually loaded): E JOB-ARG, with a fix; a bare x.py without a directory is just the job name x."""
    for bad in ("other/gallery.py", "jobs/old/gallery.py", "../gallery.py", "gallery.py.py", "jobs/gallery .py", "jobs/.py"):
        with pytest.raises(L.JobArgError):
            L.job_name(bad)
        assert L.main([bad, "--offline"]) == 1, bad
        out = capsys.readouterr().out
        assert "JOB-ARG" in out and "letters, digits, underscores" in out, bad   # the latter only appears in the "Fix" (the message for another directory doesn't say it itself)
    for ok in ("gallery", "gallery.py", "jobs/gallery.py", "./jobs/gallery.py"):
        assert L.job_name(ok) == "gallery", ok


def test_lint_usage_text_names_the_pitfalls_column_that_exists():
    """The usage of jacq.lint says "the ... column in the table": that column must really exist in docs/pitfalls.md (in the table it is "Symptom (on-site)";
    some rows are tool constraints without an on-site origin -- the text must not imply every row has one)."""
    from pathlib import Path
    table = (Path(L.__file__).resolve().parent.parent / "docs" / "pitfalls.md").read_text(encoding="utf-8")
    assert "Symptom (on-site)" in table and "Symptom (on-site)" in L.__doc__ and "which VI it was hit on" not in L.__doc__


def test_every_lint_code_is_listed_in_pitfalls_md():
    """AGENTS.md "When changing this repo": a new code needs a row in docs/pitfalls.md.
    In lint.py the code name is the second argument of `F(level, code, …)` / `add(level, code, …)`; in typecheck.py it is the tuple of `out.append((level, code, message, fix))`,
    both have to be scanned.
    It must be found as a whole backticked word in the **first cell** of some table row (`JOB-ARGX` doesn't count as `JOB-ARG`; a passing mention of `READ-ONCE` in another entry's description doesn't count as it having its own row);
    the W version with ? may share a row with the E of the same name (`X` / `X?`)."""
    import ast
    from pathlib import Path
    root = Path(L.__file__).parent
    codes, unreadable = set(), []
    for f in ("lint.py", "typecheck.py"):
        for n in ast.walk(ast.parse((root / f).read_text(encoding="utf-8"))):
            if not isinstance(n, ast.Call):
                continue
            if getattr(n.func, "id", getattr(n.func, "attr", None)) in ("F", "add") and len(n.args) >= 2:
                a, strict = n.args[1], True   # F(level, code, message, fix) / add(level, code, message, fix)
            elif (f == "typecheck.py" and getattr(n.func, "attr", None) == "append" and len(n.args) == 1
                  and isinstance(n.args[0], ast.Tuple) and len(n.args[0].elts) == 4):
                a, strict = n.args[0].elts[1], False   # out.append((level, code, message, fix)); other 4-tuples don't count, only string constants are collected
            else:
                continue
            for c in (a.body, a.orelse) if isinstance(a, ast.IfExp) else (a,):   # "X" if cond else "X?": both branches must be registered
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    codes.add(c.value)
                elif strict and not isinstance(c, ast.Name):   # a variable is a pass-through (the code name is picked up where it is created elsewhere); code names built by concatenation are invisible here
                    unreadable.append(f"{f}:{n.lineno}")
    doc = (root.parent / "docs" / "pitfalls.md").read_text(encoding="utf-8")
    firsts = [l.split("|")[1] for l in doc.splitlines() if l.startswith("|") and l.count("|") >= 3]   # the first cell of each table row
    missing = sorted(c for c in codes if not any(f"`{c}`" in f or f"`{c.rstrip('?')}`" in f for f in firsts))
    assert len(codes) > 40 and {"JOB-ARG", "IMPORT", "READ-ONCE", "CASE-DUP?", "CASE-DEFAULT?", "TYPE", "TYPE?", "REQ", "REQ?", "TYPE-UNK"} <= codes, "the code list wasn't picked up: this must be updated to follow how lint is written"
    assert not unreadable, f"the code names at these places are not string constants / two-branch constants / variables, the guard can't see them, rewrite them as constants: {unreadable}"
    assert not missing, f"these lint codes are not in docs/pitfalls.md: {missing}"


def test_subvi_into_job_dir_must_be_a_vis_entry(tmp_queue):
    """A subVI pointing into this job's directory but not a VI in VIS (an extra subdirectory level, or a name not in VIS) → on-site the file isn't found and the build fails; lint gives E right here."""
    sub = {"name": "sub", "spec": _GOOD, "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]}
    call = lambda p: dict(_GOOD, subvis=[{"name": "s", "path": str(p)}], functions=[], wires=[["x", "s.x"], ["s.y", "y"]])
    for bad in (_OUT / "nested" / "sub.vi", _OUT / "nosuch.vi", _OUT / "SUB.vi"):   # case must match too: the cache dependencies on the LabVIEW machine are identified by name
        m = _job(call(bad))
        m.VIS.insert(0, copy.deepcopy(sub))
        assert "JOB-SUBVI" in _codes(L.lint_job(m, offline=True)), bad
    m = _job(call(_OUT / "sub.vi"))
    m.VIS.insert(0, copy.deepcopy(sub))
    assert "JOB-SUBVI" not in _codes(L.lint_job(m, offline=True))


def test_hw_io_scans_against_the_computed_out(tmp_queue):
    """OUT written as BASE/write2/other/write2/jobs/zz (the end is right, the root isn't), with the subVI placed under this OUT --
    the LabVIEW machine judges by the directory computed from the job name and refuses the whole job; lint must also report HW-IO (subVI not in this job's directory) and JOB-OUT right here, not let it go to the LabVIEW machine with 0 E."""
    odd = PureWindowsPath(r"D:\agent_sandbox\lv_agentic\write2\other\write2\jobs\zz")
    m = _job(dict(_GOOD, subvis=[{"name": "s", "path": str(odd / "sub.vi")}], functions=[], wires=[["x", "s.x"], ["s.y", "y"]]))
    m.OUT = odd
    for offline in (False, True):
        codes = _codes(L.lint_job(m, offline=offline))
        assert {"HW-IO", "JOB-OUT"} <= set(codes), codes


def test_hardware_io_is_an_error_even_in_strict_mode(tmp_queue):
    """VISA Write is in the learned layer: judging by terminal names alone strict lint gives 0 E 0 W, HW-IO must block it."""
    spec = {"controls": [{"name": "cmd", "type": "string"}], "indicators": [{"name": "n", "type": "u32"}],
            "constants": [{"name": "port", "type": "string", "value": "COM3"}], "functions": [{"name": "w", "function": "VISA Write"}],
            "wires": [["port", "w.VISA resource name"], ["cmd", "w.write buffer"], ["w.return count", "n"]]}
    m = _job(spec)
    m.VIS[0] = {"name": "send", "spec": spec, "runs": {"inputs": {"cmd": "X"}, "outputs": ["n"]}}
    for offline in (False, True):
        fs = L.lint_job(m, offline=offline)
        assert "HW-IO" in _codes(fs) and all(f.fix for f in fs if f.code == "HW-IO")
    m.LINT_EXPECT = {"send": ["HW-IO"]}                 # the downgrade switch has no effect on it
    assert {"HW-IO", "EXPECT-BAD"} <= set(_codes(L.lint_job(m)))


def test_subvi_must_live_in_job_dir_or_vilib(tmp_queue):
    """The same name isn't enough -- a subVI must be either under this job's OUT or in vi.lib (non-hardware); a copy of an existing VI won't do."""
    m = _job()
    main = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
            "subvis": [{"name": "s", "path": str(_OUT / "v.vi")}], "wires": [["x", "s.x"], ["s.y", "y"]]}
    m.VIS.append({"name": "main", "spec": main, "tests": [{"inputs": {"x": 1}, "expect": {"y": 2}}]})
    assert not _codes(L.lint_job(m, offline=True))
    for where in (r"D:\agent_sandbox\lv_agentic\lab_copy\grp\v.vi",      # same file name, wrong location
                  r"D:\some_group_dir\vi.lib\v.vi"):                         # any directory that happens to be called vi.lib
        main["subvis"][0]["path"] = where
        for offline in (True, False):
            assert "HW-IO" in _codes(L.lint_job(m, offline=offline)), where


def test_hardware_scan_does_not_trip_on_names(tmp_queue):
    """Pure-computation jobs with words like serial or visa in the job name / VI name must not be blocked (no substring matching over the whole path)."""
    m = types.ModuleType("jobs.serial_improvisation")
    m.OUT = PureWindowsPath(r"D:\agent_sandbox\lv_agentic\write2\jobs\serial_improvisation")
    main = {"controls": [{"name": "x", "type": "i32"}], "indicators": [{"name": "y", "type": "i32"}],
            "subvis": [{"name": "s", "path": str(m.OUT / "parse_serial_frame.vi")}], "wires": [["x", "s.x"], ["s.y", "y"]]}
    t = [{"inputs": {"x": 1}, "expect": {"y": 2}}]
    m.VIS = [{"name": "parse_serial_frame", "spec": _GOOD, "tests": t}, {"name": "deserialize_visa", "spec": main, "tests": t}]
    assert not _codes(L.lint_job(m, offline=True))


def test_wire_diff_unsupported_spec_is_flagged_offline(tmp_queue):
    """Constructs job_check's wire check doesn't recognize (invoke_nodes) won't be recorded as verified -- say so offline first, rather than finding out only after the LabVIEW machine run."""
    spec = dict(_GOOD, indicators=_GOOD["indicators"] + [{"name": "e", "type": "error"}],
                invoke_nodes=[{"name": "iv", "control": "x", "method": "Reinitialize To Default"}])
    spec["wires"] = _GOOD["wires"] + [["iv.error out", "e"]]
    fs = L.lint_job(_job(spec), offline=True)
    assert not _codes(fs) and "WIRE-UNSUP" in _codes(fs, "W"), [str(f) for f in fs]
    assert "WIRE-UNSUP" not in _codes(L.lint_job(_job(), offline=True), "W")


def test_offline_queue_forgets_names_the_job_no_longer_uses(tmp_queue):
    """After a made-up function name is queued, fixing the job must make the entry disappear; entries queued by other jobs are unaffected."""
    from jacq import catalog as C
    C.queue_add("Other Thing", "t", "otherjob", "lint --offline")
    spec = dict(_GOOD, functions=_GOOD["functions"] + [{"name": "b", "function": "Frobnicate Widget"}], wires=_GOOD["wires"] + [["x", "b.in"]])
    L.lint_job(_job(spec), offline=True)
    assert set(C.queue_load()["items"]) == {"Other Thing", "Frobnicate Widget"}
    L.lint_job(_job(), offline=True)
    assert set(C.queue_load()["items"]) == {"Other Thing"}


def test_writer_stacks_graphs_by_their_style(dummy_vi):
    """Graph / chart styles are ~245 px tall on-site (crds_fig2, 2026-11-21): stacked at the 55 px of a plain array they overlapped.
    Hand-placed controls don't take a slot in the automatic column."""
    import re
    import sys
    from jacq.config import WRITER_SRC
    sys.path.insert(0, str(WRITER_SRC))
    from labview_mcp import gen
    spec = {"controls": [{"name": "a", "type": "dbl"}],
            "indicators": [{"name": "g1", "type": "dbl[]", "style": "Waveform Graph"},
                           {"name": "g2", "type": "dbl[]", "style": "Waveform Chart"},
                           {"name": "p", "type": "dbl", "position": [600, 0]},
                           {"name": "y", "type": "dbl[]"}, {"name": "z", "type": "dbl"}],
            "functions": [{"name": "f", "function": "Increment"}], "wires": [["a", "f.x"], ["f.x+1", "z"]]}
    xml = gen.build(spec).xml
    y = {n: int(v) for n, v in re.findall(r'<Control name="(\w+)"[^>]*position="\(\d+,(\d+)\)"', xml)}
    assert y["g2"] - y["g1"] >= 245 and y["y"] - y["g2"] == gen._GRAPH_H + 25 >= 245, y   # == : the hand-placed "p" between them took no slot
    assert y["z"] - y["y"] == 45 + 10 + 25, y          # a plain array keeps its old pitch; "p" took no slot


def test_production_steps_are_checked_by_lint():
    """PRODUCTION mistakes would only surface hours into lab/production.py: lint reports them as E PROD (same jobguard code)."""
    m = _job()
    m.check_ok = lambda v: {"pass": True}
    m.PRODUCTION = [{"key": "s1", "vi": "v", "inputs": {"x": 1}, "timeout_s": 60},
                    {"key": "s2", "vi": "v", "inputs": {"x": {"from": "s1", "out": "y"}}, "check": "check_ok"}]
    assert "PROD" not in _codes(L.lint_job(m, offline=True))
    for bad in ([{"key": "s1", "vi": "nope"}],                                              # VI not in VIS
                [{"key": "s1", "vi": "v", "inputs": {"q": 1}}],                              # not a control
                [{"key": "s1", "vi": "v", "inputs": {"x": {"from": "s0", "out": "y"}}}],    # not an earlier step
                [{"key": "s1", "vi": "v"}, {"key": "s2", "vi": "v", "inputs": {"x": {"from": "s1", "out": "x"}}}],  # not an indicator
                [{"key": "s1", "vi": "v"}, {"key": "s1", "vi": "v"}],                        # duplicate key
                [{"key": "a/b", "vi": "v"}], [{"key": "s1", "vi": "v", "timeout_s": 0}],
                [{"key": "s1", "vi": "v", "check": "nope"}], [{"key": "s1", "vi": "v", "tmeout_s": 5}], [],
                [{"key": "heartbeat", "vi": "v"}], [{"key": "CON", "vi": "v"}], [{"key": "s1.", "vi": "v"}],   # files next to the results
                [{"key": "S1", "vi": "v"}, {"key": "s1", "vi": "v"}],                        # one file on Windows
                # malformed: reported, never a crash
                [{"key": "s1", "vi": ["v"]}], [{"key": "s1", "vi": "v"}, {"key": "s2", "vi": "v", "inputs": {"x": {"from": [["s1"]], "out": "y"}}}],
                [{"key": "s1", "vi": "v"}, {"key": "s2", "vi": "v", "inputs": {"x": {"from": {"s1": 1}, "out": "y"}}}],
                [{"key": "s1", "vi": "v"}, {"key": "s2", "vi": "v", "inputs": {"x": {"from": "s1", "out": ["y"]}}}],
                [{"key": "s1", "vi": "v", 3: 1}],
                [{"key": "s1", "vi": "v", "inputs": {}}]):                                    # x has no default: it would keep an earlier step's value
        m.PRODUCTION = bad
        assert "PROD" in _codes(L.lint_job(m, offline=True)), bad
    m.VIS = [{"name": "v", "spec": "not a dict"}] + list(m.VIS)       # a malformed VIS entry is SHAPE's business, PROD must not crash on it
    m.PRODUCTION = [{"key": "s1", "vi": "v", "inputs": {"x": 1}}]
    assert L.GUARD.production_problems(m) == []
