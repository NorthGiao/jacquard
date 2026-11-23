"""wire_diff: spec wires vs. dump wires. The dump structure follows the on-site dump of gallery/w06 (only keys used in the comparison are kept)."""
from jacq import wire_diff

SPEC = {"controls": [{"name": "pre", "type": "string"}, {"name": "n", "type": "i32"}],
        "indicators": [{"name": "s", "type": "string"}],
        "functions": [{"name": "nd", "function": "Number To Decimal String"},
                      {"name": "cat", "function": "Concatenate Strings", "size": 2}],
        "wires": [["n", "nd.number"], ["pre", "cat.element[1]"],
                  ["nd.decimal integer string", "cat.element[2]"], ["cat.concatenated string", "s"]]}


def dump(pre_to="concatenate_strings_91.string[1]", nd_to="concatenate_strings_91.string[2]"):
    return {"nodes": [{"id": "pre_53", "kind": "control", "label": "pre"},
                      {"id": "n_66", "kind": "control", "label": "n"},
                      {"id": "s_78", "kind": "indicator", "label": "s"},
                      {"id": "number_to_decimal_string_80", "kind": "function", "function": "Number To Decimal String"},
                      {"id": "concatenate_strings_91", "kind": "function", "function": "Concatenate Strings"}],
            "wires": [{"from": "concatenate_strings_91.concatenated string", "to": ["s_78"]},
                      {"from": "pre_53", "to": [pre_to]},
                      {"from": "number_to_decimal_string_80.decimal integer string", "to": [nd_to]},
                      {"from": "n_66", "to": ["number_to_decimal_string_80.number"]}]}


def test_growable_positional_names_match():
    """The spec writes element[k] (the writer wires by position), the dump reports the real name string[k]: compared by position, not a mismatch."""
    got, bad = wire_diff.dump_sig(dump())
    assert got == wire_diff.spec_sig(SPEC) and not bad


def test_growable_swapped_positions_caught():
    got, _ = wire_diff.dump_sig(dump(pre_to="concatenate_strings_91.string[2]", nd_to="concatenate_strings_91.string[1]"))
    assert got != wire_diff.spec_sig(SPEC)


# ---- case / event: representation follows the on-site dumps of gallery w04 (case output tunnel), w10 (stop → W.stop inside an event frame)
W04 = {"controls": [{"name": "st", "type": {"enum": ["Init", "Run"]}}], "indicators": [{"name": "v", "type": "i32"}],
       "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
           {"value": "Init", "constants": [{"name": "c0", "type": "i32", "value": 0}]},
           {"value": "Run", "default": True, "constants": [{"name": "c1", "type": "i32", "value": 1}]}]}],
       "wires": [["st", "C.selector"], ["c0", "t"], ["c1", "t"], ["t", "v"]]}
W04_DUMP = {"nodes": [{"id": "st_54", "kind": "control", "label": "st"}, {"id": "v_67", "kind": "indicator", "label": "v"},
                      {"id": "case_69", "kind": "case"},
                      {"id": "k0", "kind": "constant", "type": "i32"}, {"id": "k1", "kind": "constant", "type": "i32"},
                      {"id": "tunnel_85", "kind": "tunnel", "class": "Tunnel", "structure": "case_69"},
                      {"id": "tunnel_125", "kind": "tunnel", "class": "SelectorTunnel", "structure": "case_69"}],
            "wires": [{"from": "st_54", "to": ["tunnel_85.outer"]}, {"from": "tunnel_125.outer", "to": ["v_67"]},
                      {"from": "k0", "to": ["tunnel_125.inner[frame 0]"]}, {"from": "k1", "to": ["tunnel_125.inner[frame 1]"]}]}


def test_case_output_tunnel_and_selector():
    got, bad = wire_diff.dump_sig(W04_DUMP)
    assert not wire_diff.diff(wire_diff.spec_sig(W04), got) and not bad


def test_case_frame_not_wired_caught():
    d = {**W04_DUMP, "wires": W04_DUMP["wires"][:3]}          # frame 2 not wired (LabVIEW uses the default value, no error)
    got, _ = wire_diff.dump_sig(d)
    assert wire_diff.diff(wire_diff.spec_sig(W04), got)


W10 = {"controls": [{"name": "stop", "type": "bool"}, {"name": "x", "type": "dbl"}, {"name": "y0", "type": "dbl"}],
       "indicators": [{"name": "d", "type": "dbl"}],
       "structures": [{"name": "W", "type": "while_loop", "structures": [{"name": "E", "type": "event", "events": [
           {"control": "stop"}, {"event": "Timeout", "timeout": 50, "functions": [{"name": "s", "function": "Subtract"}]}]}]}],
       "wires": [["stop", "W.stop"], ["x", "s.x"], ["y0", "s.y"], ["s.x-y", "d"]]}


def w10_dump(x_to="sub.x", y_to="sub.y"):
    return {"nodes": [{"id": "stop_53", "kind": "control", "label": "stop"}, {"id": "x_1", "kind": "control", "label": "x"},
                      {"id": "y_1", "kind": "control", "label": "y0"}, {"id": "d_1", "kind": "indicator", "label": "d"},
                      {"id": "wl", "kind": "while_loop"}, {"id": "ev", "kind": "event_structure"},
                      {"id": "sub", "kind": "function", "function": "Subtract"}, {"id": "k50", "kind": "constant", "type": "i32"},
                      {"id": "tunnel_122", "kind": "tunnel", "class": "Tunnel", "structure": "ev"},
                      {"id": "tunnel_124", "kind": "tunnel", "class": "Tunnel", "structure": "ev"},      # unwired
                      {"id": "tunnel_155", "kind": "tunnel", "class": "SelectorTunnel", "structure": "ev"}],
            "wires": [{"from": "tunnel_155.outer", "to": ["wl.stop"]}, {"from": "stop_53", "to": ["tunnel_155.inner[frame 0]"]},
                      {"from": "k50", "to": ["tunnel_122.outer"]}, {"from": "x_1", "to": [x_to]}, {"from": "y_1", "to": [y_to]},
                      {"from": "sub.x-y", "to": ["d_1"]}]}


def test_event_auto_tunnel_and_timeout():
    got, bad = wire_diff.dump_sig(w10_dump())
    assert not wire_diff.diff(wire_diff.spec_sig(W10), got) and not bad


def test_swapped_inputs_caught():
    got, _ = wire_diff.dump_sig(w10_dump(x_to="sub.y", y_to="sub.x"))
    assert wire_diff.diff(wire_diff.spec_sig(W10), got)


def test_build_array_unpositioned_wildcard():
    want = {("ctl:a", "Build Array.#1"): 1, ("ctl:b", "Build Array.#2"): 1}
    assert not wire_diff.diff(want, {("ctl:a", "Build Array.#?"): 1, ("ctl:b", "Build Array.#?"): 1})
    assert wire_diff.diff(want, {("ctl:a", "Build Array.#?"): 2})
