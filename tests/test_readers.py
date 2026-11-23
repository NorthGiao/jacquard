"""The offline VI-reading tools in readers/: both dump formats, wire tracing, version comparison, converting GBK text back. All synthetic data, private/ is not touched."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "readers"))
sys.path.insert(0, str(REPO / "readers" / "offline"))
import vi_trace  # noqa: E402
import vi_diff  # noqa: E402
import vi_outline  # noqa: E402

G = {"nodes": [
    {"id": "x_1", "uid": 1, "kind": "control", "label": "x", "type": "dbl"},
    {"id": "add_2", "uid": 2, "kind": "function", "function": "Add"},
    {"id": "c_3", "uid": 3, "kind": "constant", "value": 1.0, "type": "dbl"},
    {"id": "y_4", "uid": 4, "kind": "indicator", "label": "y", "type": "dbl"}],
    "wires": [{"from": "x_1", "to": ["add_2.x"], "uid": 10},
              {"from": "c_3", "to": ["add_2.y"], "uid": 11},
              {"from": "add_2.x+y", "to": ["y_4"], "uid": 12}],
    "unwired_inputs": []}


def test_load_both_dump_formats(tmp_path):
    a, b = tmp_path / "job.json", tmp_path / "corpus.json"
    a.write_text(json.dumps({"ok": True, "result": G, "sec": 1}))
    b.write_text(json.dumps({"key": "x", "dump_scan": {"ok": True, "result": G}}))
    assert vi_trace.load(a) == G and vi_trace.load(b) == G


def test_walk_fwd_back():
    fwd = vi_trace.walk(G, "x_1", "fwd")
    assert fwd[0] == "x_1 -> add_2.x" and any("y_4" in x for x in fwd)
    back = vi_trace.walk(G, "y_4", "back")
    assert any("c_3 -> add_2.y" in x for x in back) and any("x_1 -> add_2.x" in x for x in back)
    assert vi_trace.walk(G, "x_1", "fwd", stop=("add_",)) == ["x_1 -> add_2.x"]


def test_diff_cli(tmp_path, capsys):
    old, new = tmp_path / "a.json", tmp_path / "b.json"
    old.write_text(json.dumps({"ok": True, "result": G}))
    g2 = json.loads(json.dumps(G))
    g2["nodes"][2]["value"] = 2.0
    new.write_text(json.dumps({"ok": True, "result": g2}))
    vi_diff.main([str(old), str(old)])
    assert "no difference" in capsys.readouterr().out
    vi_diff.main([str(old), str(new)])
    assert "1.0 -> 2.0" in capsys.readouterr().out


def test_outline_gbk_text():
    raw = "中文 Low".encode("gbk").decode("latin-1")     # what it looks like in the XML after latin-1 extraction
    vi_outline.TEXT_CODEC[0] = "gbk"
    assert vi_outline.fix_text(raw) == "中文 Low"
    assert vi_outline.fix_text("已经是中文") == "已经是中文"   # left alone if latin-1 can't encode it
    u8 = "C, 新世界, 测水".encode("utf-8").decode("latin-1")     # UTF-8 bytes in a path default value: if GBK can't decode it, use UTF-8
    assert vi_outline.fix_text(u8) == "C, 新世界, 测水"
    assert "\ufffd" in vi_outline.fix_text("\x81")             # neither can decode it: mark it with U+FFFD, don't leave latin-1 mojibake
    vi_outline.TEXT_CODEC[0] = None
    assert vi_outline.fix_text(raw) == raw
    vi_outline.TEXT_CODEC[0] = "gbk"


def test_outline_collect_keeps_structure_and_rejects_empty(tmp_path):
    import outline_vi
    import pytest
    (tmp_path / "a" / "sub").mkdir(parents=True)
    (tmp_path / "a" / "x.vi").write_bytes(b"1")
    (tmp_path / "a" / "sub" / "x.vi").write_bytes(b"2")      # same name, different subdirectory
    (tmp_path / "a" / "dir.vi").mkdir()                        # a directory called xx.vi doesn't count
    (tmp_path / "a" / "note.txt").write_text("x")
    got = outline_vi.collect([str(tmp_path / "a")])
    assert sorted((str(r), v.name) for v, _, r in got) == [(".", "x.vi"), ("sub", "x.vi")]
    with pytest.raises(SystemExit):
        outline_vi.collect([""])


FPHB = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="2"><paneHierarchy class="pane" uid="3"><zPlaneList elements="2">
<SL__arrayElement class="fPDCO" uid="10"><objFlags>66049</objFlags><ddo class="stdNum" uid="11"><partsList elements="1">
 <SL__arrayElement class="label" uid="12"><textRec class="textHair" uid="13"><text>"hits"</text></textRec></SL__arrayElement></partsList></ddo></SL__arrayElement>
<SL__arrayElement class="fPDCO" uid="20"><ddo class="indArr" uid="21"><partsList elements="1">
 <SL__arrayElement class="label" uid="22"><textRec class="textHair" uid="23"><text>"x"</text></textRec></SL__arrayElement></partsList></ddo></SL__arrayElement>
</zPlaneList></paneHierarchy></root></SL__rootObject>"""


def test_outline_marks_controls_and_indicators(tmp_path, capsys, monkeypatch):
    """lowest bit of fPDCO.objFlags = indicator, no objFlags = control (checked against on-site dumps, source in vi_outline.parse_fp).
    Also tests --name: the extraction directory need not be named after the VI (outline_vi doesn't rename via symlinks, symlinks can't be created on Windows)."""
    d = tmp_path / "hits-0a1b2c3d"
    d.mkdir()
    (d / "hits_FPHb.xml").write_text(FPHB, encoding="utf-8")
    (d / "hits_BDHb.xml").write_text("<SL__rootObject class='oHExt' uid='1'/>", encoding="utf-8")
    (d / "hits.xml").write_text("<RSRC/>", encoding="utf-8")
    _, order = vi_outline.parse_fp(str(d / "hits_FPHb.xml"))
    assert [(lab, io) for _, lab, _, _, io in order] == [("hits", "indicator"), ("x", "control")]
    monkeypatch.setattr(sys, "argv", ["vi_outline.py", str(d), "--name", "hits", "--out", str(tmp_path / "out")])
    vi_outline.main()
    assert "FAIL" not in capsys.readouterr().err
    txt = (tmp_path / "out" / "hits.outline.txt").read_text(encoding="utf-8")
    assert "[indicator] stdNum       hits" in txt and "[control] indArr       x" in txt


def test_reader_options_without_a_value_say_so(tmp_path):
    """`--stop` / `--out` / `--text` missing a value: a one-line error, `--out --print` must not take --print as the output directory."""
    import pytest
    import outline_vi
    dump = tmp_path / "d.json"
    dump.write_text(json.dumps({"ok": True, "result": G}))
    for main, argv in ((vi_trace.main, [str(dump), "fwd", "x_1", "--stop"]), (vi_trace.main, [str(dump), "fwd", "x_1", "--stop", "--json"]),
                       (outline_vi.main, [str(tmp_path / "a.vi"), "--out"]), (outline_vi.main, [str(tmp_path / "a.vi"), "--out", "--print"]),
                       (outline_vi.main, ["--text"])):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert "must be followed by" in str(e.value), argv
    assert not (tmp_path / "--print").exists()
    assert vi_trace.main([str(dump), "fwd", "x_1", "--stop", "add_"]) is None   # correct usage works as usual


def test_readers_refuse_unreadable_dumps_unknown_options_and_extra_arguments(tmp_path):
    """The three entry points that read dumps: missing / truncated / not an object / unrecognized format = one line (no JSONDecodeError or AttributeError traceback);
    misspelled options (`--sotp`), extra positional arguments, a non-integer depth are also one line each, not taken as a node id / file name."""
    import pytest
    import outline_vi
    good = tmp_path / "d.json"
    good.write_text(json.dumps({"ok": True, "result": G}))
    for name, text in (("trunc.json", '{"ok": tr'), ("list.json", "[1, 2]"), ("odd.json", '{"foo": 1}'), ("res.json", '{"result": 3}')):
        (tmp_path / name).write_text(text)
        with pytest.raises(SystemExit) as e:
            vi_trace.load(tmp_path / name)
        assert "Traceback" not in str(e.value) and str(tmp_path / name) in str(e.value), name
    with pytest.raises(SystemExit) as e:
        vi_trace.load(tmp_path / "nope.json")
    assert "can't be read" in str(e.value)
    # the first one: the number of positional arguments isn't exceeded, only the "misspelled option" check can catch it
    for argv in ([str(good), "nodes", "--sotp"], [str(good), "fwd", "x_1", "--sotp", "a"], [str(good), "fwd", "x_1", "deep"],
                 [str(good), "fwd", "x_1", "2", "extra"], [str(good), "back", "x_1", "2", "extra"],   # extra positional arguments for fwd / back
                 [str(good), "nodes", "a", "b"], [str(good), "unwired", "a"], [str(good), "bogus"]):
        with pytest.raises(SystemExit) as e:
            vi_trace.main(argv)
        assert "Traceback" not in str(e.value) and "ValueError" not in str(e.value), argv
    assert vi_trace.main([str(good), "fwd", "x_1", "2"]) is None and vi_trace.main([str(good), "unwired"]) is None   # correct usage works as usual
    for main, argv in ((vi_diff.main, [str(good), str(good), "--jsno"]), (outline_vi.main, [str(tmp_path / "a.vi"), "--bogus"])):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert "unknown options" in str(e.value), argv


def test_missing_pylabview_says_what_to_do(tmp_path, monkeypatch):
    """When extraction fails, report the real reason from extract.log, not just "extraction produced no block diagram"."""
    import pytest
    import offline_batch
    monkeypatch.setenv("PYLV", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit) as e:
        offline_batch.pylv_dir()
    assert "PYLV" in str(e.value) and "pylabview-x64-vicd.diff" in str(e.value)
    (tmp_path / "pl" / "pylabview").mkdir(parents=True)
    (tmp_path / "pl" / "pylabview" / "readRSRC.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYLV", str(tmp_path / "pl"))
    assert offline_batch.pylv_dir() == tmp_path / "pl"


def test_extract_wrappers_refuse_to_repack(tmp_path):
    """pylabview repacking corrupts LV2024 VIs (and gbkr is a lossy encoding) -- the two wrapper scripts only allow -x."""
    import subprocess
    off = REPO / "readers" / "offline"
    for cmd in ([str(off / "run_pylv.py"), "latin-1", "-c", "-i", "a.vi"], [str(off / "run_pylv.py"), "gbkr", "-x", "-c", "-i", "a.vi"],
                [str(off / "run_rsrc_gbk.py"), "-t", "gbkr", "-c", "-i", "a.vi"]):
        r = subprocess.run([sys.executable, *cmd], cwd=tmp_path, capture_output=True, text=True, env={"PYLV": str(tmp_path), "PATH": ""})
        assert r.returncode != 0 and "only does read-only extraction" in r.stderr, cmd
