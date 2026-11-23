"""job (type-probe batch, BUILD_ONLY): each VI tests only one lint type rule -- on-site it is only built, exec_state checked, dumped, not run.
On the LabVIEW machine: scripts/vi_job.sh typeprobe → after pulling back, python -m jacq.oracle typeprobe compares each VI's on-site outcome with lint's prediction:
  lint E, broken on-site → rule correct (CONFIRMED gets a source)     lint E, builds on-site → false alarm: change the rule
  lint W, broken on-site → raise this category pair into CONFIRMED        lint W, builds on-site → this category pair is actually compatible: remove it from W
  lint none, broken on-site → miss: add a rule (this VI is the source) lint none, builds on-site → letting it pass is correct
All are pure-computation VIs, not run (BUILD_ONLY), only under write2. Design: see docs/lint_robustness.md.
On-site result: 39/39 built, frozen rules had 0 misses and 0 false alarms; the rules raised/lowered accordingly are in the header of jacq/typecheck.py."""
from lablib import BASE

OUT = BASE / "write2" / "jobs" / "typeprobe"
BUILD_ONLY = True

ctl = lambda n, t: {"name": n, "type": t}
CL2 = {"cluster": [{"name": "a", "type": "dbl"}, {"name": "b", "type": "dbl"}]}
CL1 = {"cluster": [{"name": "a", "type": "dbl"}]}
CLXY = {"cluster": [{"name": "x", "type": "dbl"}, {"name": "y", "type": "dbl"}]}
CLAB = {"cluster": [{"name": "a", "type": "dbl"}, {"name": "b", "type": "i32"}]}
EN = {"enum": ["A", "B"]}


def via_select(src_t, dst_t):
    """control(src_t) → Select(t=f=it, s=constant true) → indicator(dst_t): tests "source category → indicator category" (Select is polymorphic over any type)"""
    return {"controls": [ctl("x", src_t)], "indicators": [ctl("y", dst_t)],
            "constants": [{"name": "k", "type": "bool", "value": True}],
            "functions": [{"name": "s", "function": "Select"}],
            "wires": [["k", "s.s"], ["x", "s.t"], ["x", "s.f"], ["s.s? t:f", "y"]]}


def fn2(fn, xt, yt, out_term, out_t, wire_y=True, x_term="x", y_term="y"):
    sp = {"controls": [ctl("a", xt)] + ([ctl("b", yt)] if wire_y else []), "indicators": [ctl("o", out_t)],
          "functions": [{"name": "f", "function": fn}], "wires": [["a", f"f.{x_term}"], [f"f.{out_term}", "o"]]}
    if wire_y:
        sp["wires"].append(["b", f"f.{y_term}"])
    return sp


def into_while_stop(src_t):
    return {"controls": [ctl("x", src_t)], "indicators": [ctl("n", "i32")],
            "constants": [{"name": "k", "type": "bool", "value": True}],
            "functions": [{"name": "s", "function": "Select"}],
            "structures": [{"name": "W", "type": "while_loop"}],
            "wires": [["k", "s.s"], ["x", "s.t"], ["x", "s.f"], ["s.s? t:f", "W.stop"], ["W.i", "n"]]}


def into_case(src_t, cases):
    return {"controls": [ctl("x", src_t)], "indicators": [ctl("y", "i32")],
            "structures": [{"name": "C", "type": "case", "output_tunnels": ["t"], "cases": [
                dict(c, constants=[{"name": f"c{i}", "type": "i32", "value": i}]) for i, c in enumerate(cases)]}],
            "wires": [["x", "C.selector"]] + [[f"c{i}", "t"] for i in range(len(cases))] + [["t", "y"]]}


# (name, spec, what is tested)
P = [
    # ---- indicator side: category pairs (lint currently: E = confirmed, W = unconfirmed, none = considered compatible)
    ("tp01_path_to_str", via_select("path", "string"), "path → string indicator (lint W)"),
    ("tp02_str_to_path", via_select("string", "path"), "string → path indicator (lint W)"),
    ("tp03_bool_to_str", via_select("bool", "string"), "bool → string indicator (lint W)"),
    ("tp04_str_to_bool", via_select("string", "bool"), "string → bool indicator (lint W)"),
    ("tp05_i32_to_enum", via_select("i32", EN), "i32 → enum indicator (lint considers compatible)"),
    ("tp06_enum_to_dbl", via_select(EN, "dbl"), "enum → dbl indicator (lint considers compatible)"),
    ("tp07_cl2_to_cl1", via_select(CL2, CL1), "2-element cluster → 1-element cluster (lint W)"),
    ("tp08_clab_to_clxy", via_select(CLAB, CLXY), "cluster(dbl,i32) → cluster(dbl,dbl), different element names (lint considers compatible)"),
    ("tp09_num_to_str_ind", via_select("dbl", "string"), "dbl → string indicator (lint E: the source of num→str is a function terminal, this tests whether it generalizes to indicators)"),
    ("tp10_num_to_bool_ind", via_select("dbl", "bool"), "dbl → bool indicator (lint E: as above, the source is Select.s)"),
    ("tp11_err_to_cluster", via_select("error", {"cluster": [{"name": "status", "type": "bool"}, {"name": "code", "type": "i32"},
                                                              {"name": "source", "type": "string"}]}), "error → isomorphic cluster (lint considers compatible)"),
    ("tp37_num_to_path", via_select("dbl", "path"), "dbl → path indicator (lint W; splits out num→path of the Build Path probe)"),
    ("tp38_path_to_num", via_select("path", "dbl"), "path → dbl indicator (lint W; splits out path→num of the Build Path probe)"),
    ("tp39_dbl_to_chart_arr", {"controls": [ctl("x", "dbl")], "indicators": [{"name": "y", "type": "dbl[]", "style": "Waveform Chart"}],
                               "constants": [{"name": "k", "type": "bool", "value": True}], "functions": [{"name": "s", "function": "Select"}],
                               "wires": [["k", "s.s"], ["x", "s.t"], ["x", "s.f"], ["s.s? t:f", "y"]]},
     "dbl scalar → dbl[] Waveform Chart (lint W: whether chart terminals adapt to the wired type)"),
    ("tp12_i32_to_u8arr", via_select("i32[]", "u8[]"), "i32[] → u8[] (between numeric arrays, lint considers compatible)"),
    # ---- function terminals
    ("tp13_bool_into_add", fn2("Add", "bool", "dbl", "x+y", "dbl"), "bool → Add.x (lint E: the source of bool→num is an indicator)"),
    ("tp14_str_into_add", fn2("Add", "string", "dbl", "x+y", "dbl"), "string → Add.x (lint E)"),
    ("tp15_u8_minus_dbl", fn2("Subtract", "u8", "dbl", "x-y", "dbl"), "u8 - dbl (lint considers compatible)"),
    ("tp16_i16_plus_i32", fn2("Add", "i16", "i32", "x+y", "i32"), "i16 + i32 (lint considers compatible)"),
    ("tp17_path_into_concat", {"controls": [ctl("a", "path"), ctl("b", "string")], "indicators": [ctl("o", "string")],
                               "functions": [{"name": "f", "function": "Concatenate Strings", "size": 2}],
                               "wires": [["a", "f.element[1]"], ["b", "f.element[2]"], ["f.concatenated string", "o"]]},
     "path → Concatenate Strings (lint W)"),
    ("tp18_dbl_into_sel_s", {"controls": [ctl("a", "dbl"), ctl("b", "dbl")], "indicators": [ctl("o", "dbl")],
                             "functions": [{"name": "f", "function": "Select"}],
                             "wires": [["a", "f.s"], ["b", "f.t"], ["b", "f.f"], ["f.s? t:f", "o"]]}, "dbl → Select.s (lint E; re-check of the original probe Select(3))"),
    # ---- structure terminals
    ("tp19_dbl_into_stop", into_while_stop("dbl"), "dbl → While stop terminal (lint E: num→bool)"),
    ("tp20_err_into_stop", into_while_stop("error"), "error → While stop terminal (lint considers compatible)"),
    ("tp21_arr_into_case", into_case("i32[]", [{"value": 0}, {"value": 1, "default": True}]), "i32[] → case structure selector (lint E: dimensions)"),
    ("tp22_dbl_into_N", {"controls": [ctl("n", "dbl")], "indicators": [ctl("o", "i32[]")],
                         "structures": [{"name": "L", "type": "for_loop"}], "wires": [["n", "L.N"], ["L.i", "o"]]},
     "dbl → For loop N (lint considers compatible)"),
    # ---- required inputs unwired (lint currently gives only W REQ? for all of them)
    ("tp23_add_y_unwired", fn2("Add", "dbl", None, "x+y", "dbl", wire_y=False), "y of Add unwired (lint W REQ?)"),
    ("tp24_select_f_unwired", {"controls": [ctl("s", "bool"), ctl("t", "dbl")], "indicators": [ctl("o", "dbl")],
                               "functions": [{"name": "f", "function": "Select"}],
                               "wires": [["s", "f.s"], ["t", "f.t"], ["f.s? t:f", "o"]]}, "f of Select unwired (lint W REQ?)"),
    ("tp25_index_no_index", {"controls": [ctl("a", "dbl[]")], "indicators": [ctl("o", "dbl")],
                             "functions": [{"name": "f", "function": "Index Array"}],
                             "wires": [["a", "f.array"], ["f.element", "o"]]}, "index of Index Array unwired (lint considers it optional)"),
    ("tp26_nds_no_width", {"controls": [ctl("a", "i32")], "indicators": [ctl("o", "string")],
                           "functions": [{"name": "f", "function": "Number To Decimal String"}],
                           "wires": [["a", "f.number"], ["f.decimal integer string", "o"]]}, "width of Number To Decimal String unwired (lint considers it optional)"),
    # ---- the two inputs of Select
    ("tp27_select_dbl_str", {"controls": [ctl("s", "bool"), ctl("t", "dbl"), ctl("f", "string")], "indicators": [ctl("o", "dbl")],
                             "functions": [{"name": "g", "function": "Select"}],
                             "wires": [["s", "g.s"], ["t", "g.t"], ["f", "g.f"], ["g.s? t:f", "o"]]}, "Select t=dbl f=string (lint E)"),
    ("tp28_select_dbl_i32", {"controls": [ctl("s", "bool"), ctl("t", "dbl"), ctl("f", "i32")], "indicators": [ctl("o", "dbl")],
                             "functions": [{"name": "g", "function": "Select"}],
                             "wires": [["s", "g.s"], ["t", "g.t"], ["f", "g.f"], ["g.s? t:f", "o"]]}, "Select t=dbl f=i32 (lint considers compatible)"),
    # ---- reverse check of existing rules: whether the inferred output types are right (wrong would be broken)
    ("tp29_strlen_arr", {"controls": [ctl("a", "string[]")], "indicators": [ctl("o", "i32[]")],
                         "functions": [{"name": "f", "function": "String Length"}],
                         "wires": [["a", "f.string"], ["f.length", "o"]]}, "String Length(string[]) → i32[] (lint infers 1-D)"),
    ("tp30_asize_2d", {"controls": [ctl("a", "dbl[][]")], "indicators": [ctl("o", "i32[]")],
                       "functions": [{"name": "f", "function": "Array Size"}],
                       "wires": [["a", "f.array"], ["f.size(s)", "o"]]}, "Array Size(2-D) → i32[] (lint infers 1-D)"),
    ("tp31_build_arr_append", {"controls": [ctl("a", "dbl[]"), ctl("x", "dbl")], "indicators": [ctl("o", "dbl[]")],
                               "functions": [{"name": "f", "function": "Build Array", "size": 2}],
                               "wires": [["a", "f.element[1]"], ["x", "f.element[2]"], ["f.appended array", "o"]]},
     "Build Array(dbl[], dbl) → dbl[] (lint infers append)"),
    ("tp32_build_arr_stack", {"controls": [ctl("a", "dbl[]"), ctl("b", "dbl[]")], "indicators": [ctl("o", "dbl[][]")],
                              "functions": [{"name": "f", "function": "Build Array", "size": 2}],
                              "wires": [["a", "f.element[1]"], ["b", "f.element[2]"], ["f.appended array", "o"]]},
     "Build Array(dbl[], dbl[]) → dbl[][] (lint infers +1 dimension)"),
    # ---- case structure case values (lint currently gives only W; the writer has never built these forms → put last, so getting stuck doesn't lose the earlier outcomes)
    ("tp33_case_dup", into_case("i32", [{"value": 0}, {"value": 0}, {"value": 1, "default": True}]), "duplicate integer case values (lint W CASE-DUP?)"),
    ("tp34_case_no_default", into_case("i32", [{"value": 0}, {"value": 1}]), "i32 selector without a default case (lint W CASE-DEFAULT?)"),
    ("tp35_case_bad_enum", into_case(EN, [{"value": "A"}, {"value": "Z", "default": True}]), "case value not among the enum items (lint W CASE-VAL?)"),
    ("tp36_case_enum_full", into_case(EN, [{"value": "A"}, {"value": "B"}]), "enum items fully covered, no default case (lint considers it fine)"),
]

VIS = [{"name": n, "spec": sp, "desc": d} for n, sp, d in P]
# VIs lint predicts as broken (E TYPE/DIMS/AUTOIDX): in a BUILD_ONLY job automatically downgraded to I [BUILD_ONLY], so they can go to the LabVIEW machine to see whether they are really broken
