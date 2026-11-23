"""Type propagation for lint: starting from the types of controls/constants/subVIs, infer a coarse type for every wire along the wires, and check whether the sink can accept it.

Only type-category pairs with "on-site evidence of going broken" are reported as E (CONFIRMED, sources below); other mismatches are reported as W;
functions whose type can't be inferred (no rule written) give I (the few wires downstream haven't been type-checked).

Coarse type T(cls, dims, sub): cls ∈ num bool str path enum cluster err ref queue ts var;
dims = number of array dimensions; sub = cluster fields [(name, T)] / queue element T / numeric representation ("dbl"/"i32"..., display only).

The rule table _RULES only covers common functions; terminal names follow the catalog (known_terms). Each rule only states "what the output is" and
"which input terminals accept only which categories"; numeric functions are polymorphic element-wise over arrays/clusters (LabVIEW polymorphism), here only dimensions are handled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from labview_mcp import gen
except ImportError:  # pragma: no cover
    gen = None

GRAPH_STYLES = {"Waveform Graph", "Waveform Chart", "XY Graph", "Intensity Graph", "Intensity Chart"}
_NUMS = {"dbl", "sgl", "ext", "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "cdb", "csg", "cxt"}


@dataclass(frozen=True)
class T:
    cls: str
    dims: int = 0
    sub: object = None

    def elem(self):
        return T(self.cls, 0, self.sub)

    def with_dims(self, d):
        return T(self.cls, max(d, 0), self.sub)

    def __str__(self):
        base = {"num": str(self.sub or "num"), "str": "string", "err": "error"}.get(self.cls, self.cls)
        return base + "[]" * self.dims


I32, U32, DBL, BOOL, STR, PATH, ERR = T("num", 0, "i32"), T("num", 0, "u32"), T("num", 0, "dbl"), T("bool"), T("str"), T("path"), T("err")


def from_lv(t) -> T | None:
    """gen.LVType → T"""
    if t is None:
        return None
    k = t.kind
    if k == "array":
        e = from_lv(t.elem)
        return None if e is None else T(e.cls, e.dims + t.dims, e.sub)
    if k in _NUMS:
        return T("num", 0, k)
    if k == "bool":
        return BOOL
    if k == "string":
        return STR
    if k == "path":
        return PATH
    if k == "enum":
        return T("enum", 0, tuple(t.items or ()))
    if k == "refnum":
        return T("ref", 0, (t.items or [None])[0])
    if k == "cluster":
        fs = tuple((n, from_lv(ft)) for n, ft in t.fields or [])
        if [n for n, _ in fs] == ["status", "code", "source"]:
            return ERR
        return T("cluster", 0, fs)
    return None   # flat etc.: unknown


def parse(spec_type) -> T | None:
    if gen is None:
        return None
    try:
        return from_lv(gen.parse_type(spec_type))
    except Exception:  # noqa: BLE001
        return None


# ---------- category pairs (src category, sink category) confirmed on-site to "go broken once wired" ----------
# source: private/lab_corpus/probes/ (2026-11 single-function probes: all inputs wired to dbl controls, outputs wired to dbl indicators, exec_state recorded)
#   bool→num : Greater?/Less?/Equal?... (dbl input itself is fine, bool output wired to a dbl indicator) → broken
#   str→num  : Number To Decimal String (string output wired to a dbl indicator) → broken
#   num→str  : String Length.string ← dbl (output i32→dbl is fine) → broken
#   num→bool : Select.s ← dbl (t/f/output all dbl) → broken
#   0→n dims : Array Size.array ← dbl scalar (output i32→dbl is fine) → broken
#   n→0 dims : the 1-D output of Build Array(dbl, dbl) wired to a dbl indicator → broken
#   num→cluster : Unbundle.cluster ← dbl (f020/f151; the output wired to nothing but the indicator) → broken
#   cluster→num : the cluster output of Bundle(dbl, dbl) wired to a dbl indicator (f019/f150; Bundle accepts any type) → broken
# added by the jobs/typeprobe.py on-site batch (private/lab_corpus/write2/jobs/typeprobe/):
#   path→str tp01, str→path tp02, bool→str tp03, str→bool tp04, num→path tp37, path→num tp38 (all via Select into an indicator);
#   path→Concatenate Strings tp17; different number of cluster elements tp07 (2 → 1)
# generalized to all sinks: the category pairs above were confirmed on indicators, and are also reported as E on function terminals / While stop terminal / case selector / both inputs of Select --
#   typeprobe tp09/tp10/tp13/tp14/tp19/tp21/tp27 were all broken on-site, the generalization holds
# still only W (not confirmed on-site): different enum items, different cluster element types (same count), bool↔path, enqueued element ≠ queue element
# can be built on-site (don't report): i32→enum tp05, enum→dbl tp06, cluster element names differ but types same tp08, error→isomorphic cluster tp11, i32→u8[] element tp12,
#   u8−dbl tp15, i16+i32 tp16, error→While stop terminal tp20, dbl→For N tp22, scalar→dbl[] of a Waveform Chart tp39
# counter-examples (same probe batch, build fine, not broken -- don't report errors by intuition): And/Or/Exclusive Or/Not accept dbl; To Upper/Lower Case accept dbl;
#   Type Cast accepts dbl; conversions like To Long Integer accept dbl.
CONFIRMED = {("bool", "num"), ("str", "num"), ("num", "str"), ("num", "bool"), ("dims", "0→n"), ("dims", "n→0"),
             ("num", "cluster"), ("cluster", "num"),
             ("path", "str"), ("str", "path"), ("bool", "str"), ("str", "bool"), ("num", "path"), ("path", "num"),
             ("cluster", "count")}
CHART_STYLES = {"Waveform Chart", "Intensity Chart"}   # charts accept a single point as well as a set: scalar → 1-D chart builds (tp39)
_NUMISH = {"num", "enum"}


def mismatch(src: T, dst: T, dst_dims_any=False):
    """None = compatible; otherwise (category pair, description)."""
    if src is None or dst is None:
        return None
    if not dst_dims_any and src.dims != dst.dims:
        key = "0→n" if src.dims == 0 else ("n→0" if dst.dims == 0 else "n→m")
        return ("dims", key), f"{src.dims}-D → {dst.dims}-D"
    a, b = src.cls, dst.cls
    if a == b == "enum" and src.sub and dst.sub and tuple(src.sub) != tuple(dst.sub):
        return ("enum", "enum"), f"different enum items: {list(src.sub)} → {list(dst.sub)}"
    if a in _NUMISH and b in _NUMISH:
        return None
    if a == b and a not in ("cluster",):
        return None
    if {a, b} <= {"cluster", "err"}:
        fa = [x for _, x in src.sub] if a == "cluster" else [BOOL, I32, STR]
        fb = [x for _, x in dst.sub] if b == "cluster" else [BOOL, I32, STR]
        if len(fa) != len(fb):
            return ("cluster", "count"), f"number of cluster elements {len(fa)} → {len(fb)}"
        for x, y in zip(fa, fb):
            m = mismatch(x, y)
            if m:
                return ("cluster", "cluster"), f"cluster element {m[1]}"
        return None
    return (a, b), f"{src} → {dst}"


# ---------- function rules ----------
# output: fn(ins: {terminal: T}) -> {output terminal: T}; accept: {input terminal: (set of allowed categories, whether it must be an array)}
class _Unknown(Exception):
    """some input type can't be inferred (e.g. the output of a vilib subVI) → this function's output is unknown too"""


def _maxd(ins, *names):
    if any(n not in ins for n in names):
        raise _Unknown
    return max([ins[n].dims for n in names] or [0])


def _num_out(ins, names, sub=None):
    d = _maxd(ins, *names)
    s = sub or next((ins[n].sub for n in names if ins[n].cls == "num"), None)
    return T("num", d, s)


NUMARG = ({"num", "enum"}, False)
STRARG = ({"str"}, False)
ARRARG = (None, True)          # any element category, must be an array
BOOLARG = ({"bool"}, False)
LOGARG = ({"bool", "num", "enum"}, False)


def _arith2(out, sub=None):
    return {"out": lambda i: {out: _num_out(i, ["x", "y"], sub)}, "accept": {"x": NUMARG, "y": NUMARG}}


def _arith1(out, inp="x", sub=None):
    return {"out": lambda i: {out: _num_out(i, [inp], sub)}, "accept": {inp: NUMARG}}


def _cmp2(out):
    return {"out": lambda i: {out: T("bool", _maxd(i, "x", "y"))}, "accept": {}}


def _cmp1(out):
    return {"out": lambda i: {out: T("bool", _maxd(i, "x"))}, "accept": {"x": NUMARG}}


def _logic(out, names=("x", "y")):
    def f(i):
        d = _maxd(i, *names)
        allbool = all(i[n].cls == "bool" for n in names)
        return {out: T("bool", d) if allbool else _num_out(i, list(names))}
    return {"out": f, "accept": {n: LOGARG for n in names}}


def _same(out, inp, accept=ARRARG):
    return {"out": lambda i: {out: i[inp]} if inp in i else {}, "accept": {inp: accept}}


def _index_array(i):
    a = i.get("array")
    if a is None or a.dims == 0:
        return {}
    k = sum(1 for n in i if n.startswith("index"))
    t = a.with_dims(a.dims - max(k, 1))
    return {"element": t, "subarray": t}


def _build_array(i):
    els = [v for k, v in i.items() if k.startswith("element") or k.startswith("array")]
    if not els:
        return {}
    m = max(v.dims for v in els)
    # all inputs have the same dimensions → +1 dimension (two scalars → 1-D); both n-D and (n-1)-D present → append, still n-D (e.g. appending a scalar to the laps array)
    d = m + 1 if all(v.dims == m for v in els) else m
    b = next(v for v in els if v.dims == m)
    return {"appended array": b.with_dims(d)}


def _unbundle_by_name(i):
    c = i.get("input cluster")
    if c is None or c.cls != "cluster":
        return {"#err": ERR} if c is not None and c.cls == "err" else {}
    return dict(c.sub)


def _select(i):
    t = i.get("t") or i.get("f")
    return {"s? t:f": t} if t else {}


def _queue_out(i):
    e = i.get("element data type")
    return {"queue out": T("queue", 0, e)} if e else {}


def _deq(i):
    q = i.get("queue")
    return {"element": q.sub, "queue out": q} if q is not None and q.cls == "queue" and q.sub else ({"queue out": q} if q else {})


def _array_max_min(i):
    a = i.get("array")
    if a is None:
        return {}
    idx = I32 if a.dims <= 1 else I32.with_dims(1)
    return {"max value": a.elem(), "min value": a.elem(), "max index (indices)": idx, "min index (indices)": idx}


def _fields(c):
    if c is None:
        raise _Unknown
    if c.cls == "err":
        return [BOOL, I32, STR]
    if c.cls == "cluster":
        return [t for _, t in c.sub]
    return None


def _unbundle(i):
    fs = _fields(i.get("cluster"))
    return {f"element[{k + 1}]": t for k, t in enumerate(fs or [])}


def _bundle(i):
    if "cluster" in i:
        return {"output cluster": i["cluster"]}
    els = sorted((int(k[8:-1]), v) for k, v in i.items() if k.startswith("element["))
    return {"output cluster": T("cluster", 0, tuple(("", v) for _, v in els))} if els else {}


def _reshape(i):
    a = i.get("array")
    k = sum(1 for n in i if n.startswith("dimension size"))
    return {"output array": a.with_dims(max(k, 1))} if a is not None else {}


_RULES = {
    "Add": _arith2("x+y"), "Subtract": _arith2("x-y"), "Multiply": _arith2("x*y"), "Divide": _arith2("x/y", "dbl"),
    "Quotient & Remainder": {"out": lambda i: {"floor(x/y)": _num_out(i, ["x", "y"]), "x-y*floor(x/y)": _num_out(i, ["x", "y"])},
                             "accept": {"x": NUMARG, "y": NUMARG}},
    "Max & Min": {"out": lambda i: {"max(x,y)": _num_out(i, ["x", "y"]), "min(x,y)": _num_out(i, ["x", "y"])},
                  "accept": {"x": NUMARG, "y": NUMARG}},
    "Increment": _arith1("x+1"), "Decrement": _arith1("x-1"), "Absolute Value": _arith1("abs(x)"), "Negate": _arith1("-x"),
    "Square": _arith1("x^2"), "Square Root": _arith1("sqrt(x)", sub="dbl"), "Sine": _arith1("sin(x)", sub="dbl"),
    "Cosine": _arith1("cos(x)", sub="dbl"), "Exponential": _arith1("exp(x)", sub="dbl"), "Natural Logarithm": _arith1("ln(x)", sub="dbl"),
    "Power Of 10": _arith1("10^x", sub="dbl"), "Reciprocal": _arith1("1/x", sub="dbl"),
    "Round To Nearest": _arith1("nearest integer value", "number"),
    "Round Toward -Infinity": _arith1("floor(x): largest int <= x"),
    "To Double Precision Float": _arith1("double precision float", "number", "dbl"),
    "To Long Integer": _arith1("32bit integer", "number", "i32"),
    "To Unsigned Word Integer": _arith1("unsigned 16bit integer", "number", "u16"),
    "To Unsigned Long Integer": _arith1("unsigned 32bit integer", "number", "u32"),
    "To Unsigned Byte Integer": _arith1("unsigned 8bit integer", "number", "u8"),
    "Greater?": _cmp2("x > y?"), "Less?": _cmp2("x < y?"), "Equal?": _cmp2("x = y?"), "Not Equal?": _cmp2("x != y?"),
    "Greater Or Equal?": _cmp2("x >= y?"), "Less Or Equal?": _cmp2("x <= y?"),
    "Equal To 0?": _cmp1("x = 0?"), "Not Equal To 0?": _cmp1("x != 0?"), "Greater Than 0?": _cmp1("x > 0?"), "Less Than 0?": _cmp1("x < 0?"),
    "And": _logic("x .and. y?"), "Or": _logic("x .or. y?"), "Exclusive Or": _logic("x .xor. y?"), "Not": _logic(".not. x?", ("x",)),
    "Select": {"out": _select, "accept": {"s": BOOLARG}},
    "In Range and Coerce": {"out": lambda i: {"coerced(x)": i["x"], "In Range?": T("bool", i["x"].dims)} if "x" in i else {},
                            "accept": {}},
    "Array Size": {"out": lambda i: {"size(s)": I32 if i["array"].dims <= 1 else I32.with_dims(1)} if "array" in i else {},
                   "accept": {"array": ARRARG}},
    "Index Array": {"out": _index_array, "accept": {"array": ARRARG, "index": NUMARG, "index (row)": NUMARG, "index (col)": NUMARG}},
    "Build Array": {"out": _build_array, "accept": {}},
    "Array Subset": _same("subarray", "array"), "Sort 1D Array": _same("sorted array", "array"),
    "Reverse 1D Array": _same("reversed array", "array"),
    "Rotate 1D Array": _same("array (last n elements first)", "array"),
    "Split 1D Array": {"out": lambda i: {"first subarray": i["array"], "second subarray": i["array"]} if "array" in i else {},
                       "accept": {"array": ARRARG}},
    "Delete From Array": {"out": lambda i: {"array w/ subset deleted": i["array"]} if "array" in i else {}, "accept": {"array": ARRARG}},
    "Insert Into Array": _same("output array", "array"), "Replace Array Subset": _same("output array", "array"),
    "Transpose 2D Array": _same("transposed array", "2D array"),
    "Search 1D Array": {"out": lambda i: {"index of element": I32}, "accept": {"1D array": ARRARG}},
    "Add Array Elements": {"out": lambda i: {"sum": i["numeric array"].elem()} if "numeric array" in i else {},
                           "accept": {"numeric array": ({"num", "enum"}, True)}},
    "Multiply Array Elements": {"out": lambda i: {"product": i["numeric array"].elem()} if "numeric array" in i else {},
                                "accept": {"numeric array": ({"num", "enum"}, True)}},
    "Array Max & Min": {"out": _array_max_min, "accept": {"array": ARRARG}},
    "Empty Array?": {"out": lambda i: {"empty?": BOOL}, "accept": {}},
    "Initialize Array": {"out": lambda i: {"initialized array": i["element"].with_dims(i["element"].dims + 1)} if "element" in i else {},
                         "accept": {"dimension size": NUMARG}},
    "Concatenate Strings": {"out": lambda i: {"concatenated string": STR}, "accept": {"*": ({"str"}, False)}},
    "String Length": {"out": lambda i: {"length": I32.with_dims(i["string"].dims) if "string" in i else I32}, "accept": {"string": STRARG}},
    "Number To Decimal String": {"out": lambda i: {"decimal integer string": STR.with_dims(i["number"].dims) if "number" in i else STR},
                                 "accept": {"number": NUMARG, "width (-)": NUMARG}},
    "Decimal String To Number": {"out": lambda i: {"number": i.get("default (0L)", I32).with_dims(i["string"].dims if "string" in i else 0)},
                                 "accept": {"string": STRARG, "offset": NUMARG}},
    "Format Into String": {"out": lambda i: {"resulting string": STR}, "accept": {"format string": STRARG, "initial string": STRARG}},
    "Match Pattern": {"out": lambda i: {"before substring": STR, "match substring": STR, "after substring": STR, "offset past match": I32},
                      "accept": {"string": STRARG, "regular expression": STRARG, "offset (0)": NUMARG}},
    "Search and Replace String": {"out": lambda i: {"result string": STR, "number of replacements": I32, "offset past replacement": I32,
                                                    "error out": ERR},
                                  "accept": {"input string": STRARG, "search string": STRARG, "replace string": STRARG}},
    "To Upper Case": _same("all upper case string", "string", ({"str", "num"}, False)),
    "To Lower Case": _same("all lower case string", "string", ({"str", "num"}, False)),
    "Tick Count (ms)": {"out": lambda i: {"millisecond timer value": U32}, "accept": {}},
    "Wait (ms)": {"out": lambda i: {"millisecond timer value": U32}, "accept": {"milliseconds to wait": NUMARG}},
    "Wait Until Next ms Multiple": {"out": lambda i: {"millisecond timer value": U32}, "accept": {"millisecond multiple": NUMARG}},
    "Build Path": {"out": lambda i: {"appended path": PATH}, "accept": {"base path": ({"path"}, False)}},
    "Get Date/Time In Seconds": {"out": lambda i: {"seconds since 1Jan1904": T("ts")}, "accept": {}},
    "Obtain Queue": {"out": _queue_out, "accept": {}},
    "Enqueue Element": {"out": lambda i: {"queue out": i["queue"]} if "queue" in i else {}, "accept": {"queue": ({"queue"}, False)}},
    "Dequeue Element": {"out": _deq, "accept": {"queue": ({"queue"}, False)}},
    "Release Queue": {"out": lambda i: {}, "accept": {"queue": ({"queue"}, False)}},
    "String To Byte Array": {"out": lambda i: {"unsigned byte array": T("num", i["string"].dims + 1, "u8")} if "string" in i else {},
                             "accept": {"string": STRARG}},
    "Byte Array To String": {"out": lambda i: {"string": STR.with_dims(i["unsigned byte array"].dims - 1)} if "unsigned byte array" in i else {},
                             "accept": {"unsigned byte array": ({"num"}, True)}},      # probe f145: dbl scalar → broken
    "Boolean To (0,1)": {"out": lambda i: {"0, 1": T("num", i["Boolean"].dims, "i16")} if "Boolean" in i else {},
                         "accept": {"Boolean": BOOLARG}},
    "Reshape Array": {"out": _reshape, "accept": {"array": ARRARG, "dimension size": NUMARG}},
    "Unbundle": {"out": _unbundle, "accept": {"cluster": ({"cluster", "err"}, False)}},
    "Bundle": {"out": _bundle, "accept": {"cluster": ({"cluster", "err"}, False)}},
    "Cluster To Array": {"out": lambda i: {"array": _fields(i.get("cluster"))[0].with_dims(1)} if _fields(i.get("cluster")) else {},
                         "accept": {"cluster": ({"cluster", "err"}, False)}},
    "Array To Cluster": {"out": lambda i: {}, "accept": {"array": ARRARG}},
    "Unbundle By Name": {"out": _unbundle_by_name, "accept": {"input cluster": ({"cluster", "err"}, False)}},
    "Bundle By Name": {"out": lambda i: {"output cluster": i["input cluster"]} if "input cluster" in i else {},
                       "accept": {"input cluster": ({"cluster", "err"}, False)}},
}


# required inputs (unwired → broken). Only those definitely without a default are listed; those confirmed on-site (REQ_CONFIRMED) are reported as E `REQ`, the rest as W `REQ?`
REQUIRED = {f: ("x", "y") for f in ("Add", "Subtract", "Multiply", "Divide", "Quotient & Remainder", "Max & Min",
                                    "Greater?", "Less?", "Equal?", "Not Equal?", "Greater Or Equal?", "Less Or Equal?",
                                    "And", "Or", "Exclusive Or")}
REQUIRED.update({f: ("x",) for f in ("Increment", "Decrement", "Absolute Value", "Negate", "Square", "Square Root", "Sine",
                                     "Cosine", "Exponential", "Natural Logarithm", "Reciprocal", "Not", "Equal To 0?",
                                     "Not Equal To 0?", "Greater Than 0?", "Less Than 0?")})
REQUIRED.update({"Select": ("s", "t", "f"), "Array Size": ("array",), "Index Array": ("array",),
                 "String Length": ("string",), "Number To Decimal String": ("number",), "In Range and Coerce": ("x",)})


REQ_CONFIRMED = {("Add", "y"), ("Select", "f")}     # typeprobe tp23 / tp24


def has_rule(fname):
    return fname in _RULES


def accepts(fname, term):
    r = _RULES.get(fname)
    if not r:
        return None
    a = r["accept"]
    if term in a:
        return a[term]
    if "*" in a:
        return a["*"]
    return None


def outputs(fname, ins):
    r = _RULES.get(fname)
    if not r:
        return None
    try:
        return {k: v for k, v in r["out"](ins).items() if v is not None}
    except (_Unknown, KeyError, AttributeError, TypeError):
        return {}


# ---------- propagation ----------
def infer(sp, kind, cont, info, end_chain, job_types=None, frame_of=None):
    """Returns (src_type(end) -> T|None, wire_type(a, b) -> (source T, T on arrival at the sink), notes).
    job_types: {"<vi>.vi": {"in": {label: T}, "out": {label: T}}} (subVIs built earlier in this job).
    frame_of: {event data node name: name of the control registered by the frame}"""
    job_types = job_types or {}
    frame_of = frame_of or {}
    wires = sp.get("wires", [])
    base = {}
    for n, k in kind.items():
        if k in ("ctl", "ind", "const"):
            base[n] = parse(info[n].get("type", "dbl"))
    reg = {}           # intermediate results: sr / tun / function inputs
    fn_out = {}

    def crossing(a, b):
        ca, cb = end_chain(a, True), end_chain(b, False)
        exits = [x for x in ca if x not in cb and kind.get(x) == "for"]
        enters = [x for x in cb if x not in ca and kind.get(x) == "for"]
        return exits, enters

    def src(end):
        n, _, t = end.partition(".")
        k = kind.get(n)
        if k in ("ctl", "const"):
            return base.get(n)
        if k == "ind":                         # reading an indicator like a local variable: unsupported, not judged
            return None
        if k == "sr":
            return reg.get(("sr", n))
        if k == "tun":
            return reg.get(("tun", n))
        if k in ("for", "while") and t == "i":
            return I32
        if k == "fn":
            return (fn_out.get(n) or {}).get(t)
        if k == "vi":
            base_name = info[n]["_vi"]
            return (job_types.get(base_name) or {}).get("out", {}).get(t)
        if k == "pn":
            c = info[n].get("control")
            if t == "Value" and c in base:
                return base[c]
            return None
        if n in frame_of and t in ("NewVal", "OldVal"):
            return base.get(frame_of[n])
        return None

    def arrive(a, b):
        """(source type, type on arrival at the sink, number of For levels exited)"""
        s = src(a)
        if s is None:
            return None, None, 0
        exits, enters = crossing(a, b)
        nb = b.partition(".")[0]
        d = s.dims + len(exits)
        if kind.get(nb) != "sr":
            d -= min(len(enters), d) if s.dims > 0 else 0
        return s, s.with_dims(d), len(exits)

    n_in = {}
    for a, b in wires:
        n_in[b.partition(".")[0]] = n_in.get(b.partition(".")[0], 0) + 1
    for _ in range(64):
        changed = False
        ins = {}
        for a, b in wires:
            _, t_at, _ = arrive(a, b)
            if t_at is None:
                continue
            nb, _, tb = b.partition(".")
            kb = kind.get(nb)
            if kb == "sr" and ("sr", nb) not in reg:
                reg[("sr", nb)] = t_at; changed = True
            elif kb == "tun" and ("tun", nb) not in reg:
                reg[("tun", nb)] = t_at; changed = True
            elif kb == "fn":
                ins.setdefault(nb, {})[tb] = t_at
        for n, i in ins.items():
            if len(i) < n_in.get(n, 0):
                continue                     # some wired input type hasn't been inferred yet: don't infer the output yet (otherwise growable functions would compute it wrongly from partial inputs)
            o = outputs(info[n]["function"], i)
            if o is not None and o != fn_out.get(n):
                fn_out[n] = o; changed = True
        if not changed:
            break
    return src, arrive


def sink_expect(end, kind, info, base_types, job_types):
    """What the sink wants: (T or None, set of allowed categories or None, whether it must be an array, whether dimensions are arbitrary, description)"""
    n, _, t = end.partition(".")
    k = kind.get(n)
    if k == "ind":
        return base_types.get(n), None, False, False, f"indicator {n}"
    if k == "vi":
        tt = (job_types.get(info[n]["_vi"]) or {}).get("in", {}).get(t)
        return tt, None, False, False, f"subVI {info[n]['_vi']}'s input {t!r}"
    if k == "fn":
        acc = accepts(info[n]["function"], t)
        if acc:
            cls, arr = acc
            return None, cls, arr, True, f"{info[n]['function']}.{t}"
        return None, None, False, True, ""
    if k == "pn" and t == "Value" and info[n].get("write") and info[n].get("control") in base_types:
        c = info[n]["control"]
        return base_types[c], None, False, False, f"property node writing {c}.Value"
    if k == "while" and t in ("stop", "condition"):
        return None, {"bool", "err"}, False, False, f"the stop terminal of While loop {n}"
    if k == "for" and t == "N":
        return None, {"num", "enum"}, False, False, f"N of For loop {n}"
    if k == "case" and t == "selector":
        return None, {"bool", "num", "enum", "str", "err"}, False, False, f"the selector of case structure {n}"
    return None, None, False, True, ""


def check(sp, kind, cont, info, end_chain, job_types=None, frame_of=None):
    """[(level, code, message, fix), ...] + src function (for the dimension check)"""
    job_types = job_types or {}
    src, arrive = infer(sp, kind, cont, info, end_chain, job_types, frame_of)
    base_types = {n: parse(info[n].get("type", "dbl")) for n, k in kind.items() if k in ("ctl", "ind", "const")}
    out = []
    no_rule = {}
    for a, b in sp.get("wires", []):
        na, _, ta = a.partition(".")
        if kind.get(na) == "fn" and src(a) is None:
            f = info[na]["function"]
            if not has_rule(f):
                no_rule.setdefault(f, []).append(a)
            continue
        s0, s, nexit = arrive(a, b)
        if s is None:
            continue
        tt, cls, arr, dims_any, what = sink_expect(b, kind, info, base_types, job_types)
        if not what:
            continue
        style = info[b.partition(".")[0]].get("style") if kind.get(b.partition(".")[0]) == "ind" else None
        graph = style in GRAPH_STYLES
        if tt is not None:
            m = mismatch(s, tt)
            if m and style in CHART_STYLES and m[0] == ("dims", "0→n") and tt.dims == s.dims + 1 and not mismatch(s, T(tt.cls, s.dims, tt.sub)):
                m = None                     # charts accept a single point (tp39)
            if m and m[0][0] == "dims" and nexit and kind.get(b.partition(".")[0]) == "ind":
                continue                     # dimensions when leaving a For are reported by lint's AUTOIDX / DIMS (more specific wording)
            if m:
                lvl = "E" if m[0] in CONFIRMED and not graph else "W"   # graph terminals adapt to the wired type (not confirmed on-site) → only W
                out.append((lvl, "TYPE" if lvl == "E" else "TYPE?", f"{a} → {b}: type mismatch -- source {s}, {what} is {tt} ({m[1]})",
                            _fix(m[0], s, tt)))
            continue
        bad = None
        if arr and s.dims == 0:
            bad = (("dims", "0→n"), f"{what} wants an array, source {s} is a scalar")
        elif cls is not None and s.cls not in cls:
            bad = ((s.cls, next(iter(sorted(cls)))), f"{what} accepts {'/'.join(sorted(cls))}, the source is {s}")
        elif not dims_any and s.dims:
            bad = (("dims", "n→0"), f"{what} wants a scalar, source {s} is an array")
        if bad:
            pair = bad[0]
            confirmed = pair in CONFIRMED or (pair[0] in ("bool", "str", "num") and any((pair[0], c) in CONFIRMED for c in (cls or ())))
            lvl = "E" if confirmed else "W"
            out.append((lvl, "TYPE" if lvl == "E" else "TYPE?", f"{a} → {b}: {bad[1]}", _fix(pair, s, None)))
    # merging: types coming in on each path into the same output tunnel / shift register / queue must agree (only the first path sets the type, the other paths are compared here)
    joins = {}
    for a, b in sp.get("wires", []):
        nb, _, tb = b.partition(".")
        if kind.get(nb) in ("tun", "sr"):
            t_at = arrive(a, b)[1]
            if t_at is not None:
                joins.setdefault(nb, []).append((a, t_at))
    for nb, xs in joins.items():
        for a, t_at in xs[1:]:
            m = mismatch(t_at, xs[0][1])
            if m:
                out.append(("E" if m[0] in CONFIRMED else "W", "TYPE" if m[0] in CONFIRMED else "TYPE?",
                            f"{a} → {nb}: inconsistent types merging into the same {'output tunnel' if kind[nb] == 'tun' else 'shift register'} -- this path {t_at}, the {xs[0][0]} path {xs[0][1]}",
                            _fix(m[0], t_at, xs[0][1])))
    for a, b in sp.get("wires", []):
        nb, _, tb = b.partition(".")
        if kind.get(nb) == "fn" and info[nb]["function"] == "Enqueue Element" and tb == "element":
            q = next((arrive(x, y)[1] for x, y in sp.get("wires", []) if y == f"{nb}.queue"), None)
            e = arrive(a, b)[1]
            if q is not None and q.cls == "queue" and q.sub is not None and e is not None:
                m = mismatch(e, q.sub)
                if m:
                    out.append(("W", "TYPE?", f"{a} → {b}: enqueued element {e} doesn't match the queue element type {q.sub} ({m[1]})",
                                "The element data type of Obtain Queue must be the same type as the enqueued data"))
    # required inputs not wired
    wired = {}
    for a, b in sp.get("wires", []):
        nb, _, tb = b.partition(".")
        wired.setdefault(nb, set()).add(tb)
    for n, k in kind.items():
        if k == "fn" and info[n]["function"] in REQUIRED:
            miss = [t for t in REQUIRED[info[n]["function"]] if t not in wired.get(n, set())]
            if miss:
                conf = any((info[n]["function"], t) in REQ_CONFIRMED for t in miss)
                out.append(("E" if conf else "W", "REQ" if conf else "REQ?",
                            f"{n} ({info[n]['function']}): input {miss} is not wired -- this input has no default, unwired the VI is {'broken (confirmed on-site)' if conf else 'most likely broken'}",
                            f"Wire {', '.join(f'{n}.{t}' for t in miss)}"))
            if info[n]["function"] == "Select":
                tt = [arrive(a, b)[1] for a, b in sp.get("wires", []) if b in (f"{n}.t", f"{n}.f")]
                if len(tt) == 2 and None not in tt:
                    m = mismatch(tt[0], tt[1])
                    if m:
                        out.append(("E" if m[0] in CONFIRMED else "W", "TYPE" if m[0] in CONFIRMED else "TYPE?",
                                    f"{n} (Select): t is {tt[0]}, f is {tt[1]} -- the two inputs must have the same type", _fix(m[0], tt[0], tt[1])))
    n_w = len(sp.get("wires", []))
    typed = sum(1 for a, b in sp.get("wires", []) if arrive(a, b)[1] is not None)
    stats = {"wires": n_w, "typed": typed}
    for f, ends in no_rule.items():
        out.append(("I", "TYPE-UNK", f"{f!r} has no type rule: its outputs {sorted(set(ends))} are not type-checked downstream",
                    "(add a rule to _RULES in jacq/typecheck.py; it needs an on-site source)"))
    return out, src, stats


def _fix(pair, s, tt):
    a, b = pair
    if a == "dims":
        return ("Wrong dimensions: scalar↔array. An array entering a For loop is auto-indexed (-1 dimension), leaving a For loop adds +1 dimension; "
                "for the final value go through a shift register; for an array use Build Array / change the indicator to an array")
    if (a, b) == ("bool", "num"):
        return "A boolean can't be used directly as a number: use Select (a constant on each of true/false) or Boolean To (0,1), or change the indicator to bool"
    if (a, b) == ("num", "bool"):
        return "A number can't be used directly as a boolean: compare first (e.g. Not Equal To 0?), or change the control to bool"
    if (a, b) == ("str", "num"):
        return "A string can't be used directly as a number: use Decimal String To Number / Fract/Exp String To Number, or change the indicator to string"
    if (a, b) == ("num", "str"):
        return "A number can't be used directly as a string: use Number To Decimal String / Format Into String"
    return "Check the types at both ends (the type in the spec, the function output type)"
