"""Turn the VI XML extracted by pylabview (*_FPHb.xml / *_BDHb.xml / main .xml) into a readable program outline.

Usage:
    python vi_outline.py <extraction dir> [<extraction dir> ...] --out <output dir> [--text gbk|none]
Extraction must use latin-1 (run_pylv.py latin-1 -x): gbk extraction fails to decode FPHb; text is converted back here according to --text.
For a single VI in one step use readers/outline_vi.py.
Each extraction directory looks like tmp/vi_x/Control/ (containing Control.xml, Control_FPHb.xml, Control_BDHb.xml).
Each VI produces one <name>.outline.txt; stdout only prints one summary line.

Outline contents:
  [dependencies]   subVIs / controls / libraries linked in LIvi
  [front panel] each control/indicator: [control] (input) or [indicator] (indicator), category, label, caption, dropdown items
  [block diagram] indented by structure nesting: loops/case/sequence structures → subVIs, DAQmx, property nodes, primitives, constants, front-panel terminals, comments
Primitive names: first look up catalog/catalog_public.json (mapping table checked on-site, marked "(learned)"), then PRIM_NAMES below (guessed, with ?).
Wiring (compressedWireTable) is not decoded; data flow has to be inferred from node order and names.
"""
import argparse
import collections
import os
import re
import sys
import xml.etree.ElementTree as ET

STRUCT = {"whileLoop": "While Loop", "forLoop": "For Loop", "select": "Case Structure",
          "flatSequence": "Flat Sequence Structure", "seq": "Stacked Sequence Structure", "timeLoop": "Timed Loop",
          "eventStruct": "Event Structure", "decomposeRecomposeStructure": "In Place Element Structure",
          "inPlaceStructure": "In Place Element Structure", "diagramDisable": "Diagram Disable Structure", "condDisable": "Conditional Disable Structure"}
SUBDIAG = {"diag", "sequenceFrame"}
NODE_NAMED = {"iUse", "polyIUse", "dynIUse", "xNode", "expressNode", "sdfNode"}
SIMPLE_NODES = {"aBuild": "Build Array", "aIndx": "Index Array", "aInsert": "Insert Into Array", "subset": "Array Subset",
                "concat": "Concatenate Strings", "mux": "Bundle", "nMux": "Bundle By Name/Unbundle By Name",
                "decomposeClusterNode": "Unbundle", "cLStrObj": "String Constant Object", "bDFixed": "Fixed-Point Constant"}
SKIP = {"term", "signal", "Image", "textHair", "fontRun", "annex", "attachment", "cosm", "multiCosm",
        "bigMultiCosm", "SubCosm", "parm", "overridableParm", "SelectorRange", "hGrowCItem",
        "propItemInfo", "propItem"}


def _valid_ref(m):
    v = m.group(1).decode()
    c = int(v[1:], 16) if v[:1] in "xX" else int(v)
    return m.group(0) if c in (9, 10, 13) or c >= 32 else b"?"


def parse_xml(path):
    """Tolerate control-character references not allowed in XML 1.0 (such as &#1;)."""
    with open(path, "rb") as f:
        data = re.sub(rb"&#([xX][0-9a-fA-F]+|[0-9]+);", _valid_ref, f.read())
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        # binary default data may contain "]]>", which breaks CDATA; drop these defaults and retry
        data = re.sub(rb"<DefaultData>.*?</DefaultData>", b"<DefaultData>(binary)</DefaultData>", data, flags=re.S)
        return ET.fromstring(data)


TEXT_CODEC = ["gbk"]  # Simplified Chinese Windows is code page 936; extraction uses latin-1 (lossless), here the text bytes are converted back as GBK


def fix_text(t):
    if not t or not TEXT_CODEC[0]:
        return t
    try:
        raw = t.encode("latin-1")
    except UnicodeEncodeError:      # already Chinese (not bytes extracted with latin-1): leave unchanged
        return t
    for codec in (TEXT_CODEC[0], "utf-8"):   # a few strings (path defaults) are stored as UTF-8 bytes
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            pass
    return raw.decode(TEXT_CODEC[0], "replace")


def txt(e):
    t = fix_text((e.text or "").strip())
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        t = t[1:-1]
    return t.replace("\r\n", "⏎").replace("\n", "⏎").replace("\r", "⏎")


def own_label(e):
    """Text in the element's own label part."""
    for lab in e.iter():
        if lab.get("class") in ("label", "multiLabel") and lab is not e:
            for t in lab.iter("text"):
                s = txt(t)
                if s:
                    return s
            return ""
    return ""


def ddo_values(ddo):
    """Text in a constant/control ddo other than the label (string value, dropdown items, format)."""
    vals = []
    lab_texts = set()
    for lab in ddo.iter():
        if lab.get("class") == "label":
            lab_texts.update(id(t) for t in lab.iter("text"))
    for t in ddo.iter("text"):
        if id(t) not in lab_texts and txt(t):
            vals.append(txt(t))
    skip = _desc_ids(ddo)
    for s in ddo.iter("strings"):
        if id(s) in skip:
            continue
        items = [txt(x) for x in s if txt(x)]
        if items:
            vals.append("items=" + "/".join(items))
    return [v for v in vals if v]


def _desc_ids(e):
    """Element ids under <description> (control help text), skipped in output."""
    return {id(x) for d in e.iter("description") for x in d.iter()}


def short(s, n=160):
    return s if len(s) <= n else s[:n] + "…"


def parse_fp(path):
    """uid(fPDCO) -> (category, label); also returns the ordered control list [(category, label, caption, extra info, "control"|"indicator")]."""
    root = parse_xml(path)
    fp, order = {}, []
    for e in root.iter("SL__arrayElement"):
        if e.get("class") != "fPDCO":
            continue
        ddo = e.find("ddo")
        if ddo is None:
            continue
        label = own_label(ddo)
        cap = ""
        for c in ddo.iter("caption"):
            for t in c.iter("text"):
                cap = txt(t)
        extra = []
        skip = _desc_ids(ddo)
        for s in ddo.iter("strings"):
            if id(s) in skip:
                continue
            items = [txt(x) for x in s if txt(x)]
            if items:
                extra.append("items=" + "/".join(items))
        for d in ddo.iter("DefaultData"):
            # default data is mostly binary; only keep readable ASCII fragments from it (such as /DevN/PFI0)
            frag = re.findall(r"[A-Za-z0-9_/.:\- ]{4,}", txt(d))
            if frag:
                extra.append("default≈" + short(" ".join(frag), 60))
        # lowest bit of fPDCO.objFlags = indicator; pylabview doesn't write out an objFlags of 0, so no such element = control.
        # checked against control/indicator in on-site scan dumps: 144 existing VIs (2089 objects with unique labels), 71 VIs built by the agent (192), all consistent
        of = (e.findtext("objFlags") or "").strip()
        io = "indicator" if of.isdigit() and int(of) & 1 else "control"
        fp[e.get("uid")] = (ddo.get("class"), label)
        order.append((ddo.get("class"), label, cap, extra, io))
    return fp, order


HEAP_TYPES = {}
# primitive IDs inferred from context and terminal types (not an official NI table), to be checked in LabVIEW
PRIM_NAMES = {"1056": "Quotient & Remainder?", "1180": "Number To Decimal String?", "1200": "Sine?", "1201": "Cosine?",
              "1300": "Tick Count (ms)?", "1302": "Wait (ms)?", "1327": "Wait (ms)?", "1419": "Build Path?",
              "1809": "Array Size?", "1907": "Array Max & Min?", "1922": "VISA Close/Clear?",
              "1925": "VISA Read?", "1926": "VISA Write?"}


_NUM_FMT = {"NumFloat64": ">d", "NumFloat32": ">f", "NumInt64": ">q", "NumUInt64": ">Q", "NumInt32": ">i",
            "NumUInt32": ">I", "NumInt16": ">h", "NumUInt16": ">H", "NumInt8": ">b", "NumUInt8": ">B"}


def const_value(dco, ddo):
    """Value of a numeric/boolean constant: <ConstValue> is big-endian hex, the type is given by the ddo's typeDesc. Returns None for other types."""
    hx = (dco.findtext("ConstValue") or "").strip()
    m = re.match(r"TypeID\((\d+)\)", ddo.findtext("typeDesc") or "")
    t = HEAP_TYPES.get(int(m.group(1))) if m else None
    if not hx or not t or not re.fullmatch(r"[0-9A-Fa-f]+", hx):
        return None
    b = bytes.fromhex(hx)
    if t in _NUM_FMT:
        import struct
        n = struct.calcsize(_NUM_FMT[t])
        if not any(b):      # when the value is 0 the extracted bytes are often one more than the type (all zero), treat as 0
            return "value=0"
        if len(b) != n:
            return None
        x = struct.unpack(_NUM_FMT[t], b)[0]
        return f"value={x:g}" if isinstance(x, float) else f"value={x}"
    if t == "String" and len(b) >= 4:
        n = int.from_bytes(b[:4], "big")
        if len(b) - 4 == n or (n == 0 and not any(b)):
            raw = b[4:4 + n]
            try:
                v = raw.decode(TEXT_CODEC[0] or "latin-1")
            except UnicodeDecodeError:
                v = raw.decode("latin-1")
            return "value=" + json_str(v)
        return None
    if t == "Boolean":
        return f"value={'T' if any(b) else 'F'}"
    return None


def json_str(v):
    import json
    return json.dumps(v, ensure_ascii=False)


_LEARNED = {}


def learned_name(rid, n_terms):
    """Primitive names from catalog/catalog_public.json (learned by pairing offline extraction × on-site dumps by uid); empty if none."""
    if not _LEARNED:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "catalog", "catalog_public.json")
        try:
            import json
            with open(p, encoding="utf-8") as f:
                _LEARNED.update(json.load(f)["functions"])
        except (OSError, ValueError, KeyError):
            _LEARNED["_none"] = {}
    c = _LEARNED.get(f"prim|{rid}#{n_terms}")
    return max(c, key=c.get) + " (learned)" if c else ""


def load_heap_types(main_xml):
    """DTHP comment: Heap TypeID n = Consolidated TypeID m: type name."""
    with open(main_xml, "rb") as f:
        raw = f.read().decode("utf-8", "replace")
    return {int(a): b for a, b in
            re.findall(r"Heap TypeID\s+(\d+) = Consolidated TypeID \d+: ([^ ]+)", raw)}


def walk_bd(e, depth, out, fp, stats, parent_cls=None):
    sub_i = 0
    for ch in e:
        cls = ch.get("class")
        ind = "  " * depth
        if cls in STRUCT:
            lab = ""
            sel = ch.find(".//selString")
            if sel is not None:
                lab = " displayed case=" + "".join(txt(t) for t in sel.iter("text"))
            n = ch.find("diagramList")
            nd = n.get("elements") if n is not None else "?"
            out.append(f"{ind}▶ {STRUCT[cls]} (subdiagrams {nd}){lab}")
            stats[cls] += 1
            walk_bd(ch, depth + 1, out, fp, stats, cls)
        elif cls in SUBDIAG and parent_cls in STRUCT:
            out.append(f"{ind}── subdiagram #{sub_i}")
            sub_i += 1
            walk_bd(ch, depth + 1, out, fp, stats, None)
        elif cls in NODE_NAMED:
            out.append(f"{ind}• subVI/node: {own_label(ch) or '(no label)'}")
            stats["subvi"] += 1
        elif cls in ("propNode", "invokeNode"):
            name = "".join(txt(t) for t in ch.iter("nodeName"))
            props = [txt(t) for t in ch.iter("PropItemName")]
            kind = "property node" if cls == "propNode" else "invoke node"
            out.append(f"{ind}• {kind}[{name}]: {', '.join(props)}")
            stats[cls] += 1
        elif cls == "prim":
            rid = ch.findtext("primResID")
            types = []
            tl = ch.find("termList")
            for t in (tl if tl is not None else []):
                d = t.find("dco")
                td = d.findtext("typeDesc") if d is not None else None
                m = re.match(r"TypeID\((\d+)\)", td or "")
                if m:
                    types.append(HEAP_TYPES.get(int(m.group(1)), "?"))
            n_t = len(tl.findall("SL__arrayElement")) if tl is not None else 0
            name = learned_name(rid, n_t) or PRIM_NAMES.get(rid, "")
            out.append(f"{ind}· primitive #{rid}{(' ' + name) if name else ''} [{', '.join(types)}]")
            stats["prim:" + str(rid)] += 1
        elif cls == "fPTerm":
            d = ch.find("dco")
            k, lab = fp.get(d.get("uid") if d is not None else None, ("?", "?"))
            out.append(f"{ind}□ front-panel terminal: {lab} ({k})")
        elif cls == "bDConstDCO" or (cls == "term" and ch.find("dco") is not None
                                     and ch.find("dco").get("class") == "bDConstDCO"):
            dco = ch if cls == "bDConstDCO" else ch.find("dco")
            ddo = dco.find("ddo")
            if ddo is not None:
                v = ddo_values(ddo)
                num = const_value(dco, ddo)
                if num is not None:
                    v = [num] + ([] if num.startswith('value="') else v)
                lab = own_label(ddo)
                out.append(f"{ind}◇ constant {ddo.get('class')}{(' \"' + lab + '\"') if lab else ''}: "
                           + short("; ".join(v)))
        elif cls == "label" and parent_cls is None and e.tag == "nodeList":
            s = "".join(txt(t) for t in ch.iter("text"))
            if s:
                out.append(f"{ind}# comment: {short(s, 400)}")
        elif cls in SIMPLE_NODES:
            out.append(f"{ind}· {SIMPLE_NODES[cls]}")
        elif cls in SKIP:
            continue
        else:
            walk_bd(ch, depth, out, fp, stats, parent_cls if cls is None else None)


INTERNAL_DFDS = ("DSInit", "Table of", "Table of Probe", "Clump", "Hilite", "VI Param", "Extra DCO", "Local Input",
                 "IO Connection", "Probe", "Async", "Fixup")


def dfds_defaults(main_xml):
    """DFDS (data space defaults): the first comment of each DataFill = name, leaf values = defaults."""
    with open(main_xml, "rb") as f:
        raw = f.read().decode("utf-8", "replace")
    m = re.search(r"<DFDS>(.*?)</DFDS>", raw, re.S)
    res = []
    for blk in re.findall(r"<DataFill [^>]*>(.*?)</DataFill>", m.group(1) if m else "", re.S):
        c = re.search(r"<!--(.*?)-->", blk, re.S)
        name = fix_text(c.group(1).strip()) if c else ""
        if not name or name.startswith(INTERNAL_DFDS):
            continue
        vals = re.findall(r"<(DBL|SGL|EXT|I8|I16|I32|I64|U8|U16|U32|U64|String|Boolean|Bool)>([^<]*)</\1>", blk)
        if not vals:
            continue
        v = [x[1] for x in vals]
        shown = ", ".join(v[:6]) + (f" …({len(v)} in total)" if len(v) > 6 else "")
        res.append(f"{name} = {short(fix_text(shown), 140)}")
    return res


def deps(main_xml):
    root = parse_xml(main_xml)
    res = []
    for blk in ("LIvi", "LIbd", "LIfp"):
        b = root.find(blk)
        if b is None:
            continue
        for q in b.iter("LinkSaveQualName"):
            names = [txt(s) for s in q.iter("String")]
            res.append(":".join(names))
    return list(dict.fromkeys(res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--text", default="gbk", help="code page of the text in the VI (extraction must use latin-1); none = no conversion")
    ap.add_argument("--name", help="VI name (prefix of the files in the extraction directory); defaults to the directory name. Only allowed with one directory")
    a = ap.parse_args()
    if a.name and len(a.dirs) != 1:
        ap.error("--name only works with one extraction directory")
    TEXT_CODEC[0] = None if a.text == "none" else a.text
    os.makedirs(a.out, exist_ok=True)
    for d in a.dirs:
        name = a.name or os.path.basename(os.path.normpath(d))
        try:
            fp, order = parse_fp(os.path.join(d, f"{name}_FPHb.xml"))
            out = [f"===== {name}.vi =====", "[dependencies]"]
            out += ["  " + x for x in deps(os.path.join(d, f"{name}.xml"))]
            out.append("[defaults DFDS]")
            out += ["  " + x for x in dfds_defaults(os.path.join(d, f"{name}.xml"))]
            out.append("[front panel]")
            for k, lab, cap, extra, io in order:
                out.append(f"  [{io}] {k:12s} {lab}" + (f" (caption: {cap})" if cap else "") +
                           (("  " + " | ".join(extra)) if extra else ""))
            out.append("[block diagram]")
            stats = collections.Counter()
            HEAP_TYPES.clear()
            HEAP_TYPES.update(load_heap_types(os.path.join(d, f"{name}.xml")))
            bd = parse_xml(os.path.join(d, f"{name}_BDHb.xml"))
            walk_bd(bd, 1, out, fp, stats)
            with open(os.path.join(a.out, f"{name}.outline.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            print(f"{name}: controls {len(order)} subVIs {stats['subvi']} structures "
                  f"{sum(v for k, v in stats.items() if k in STRUCT)} lines {len(out)}")
        except Exception as ex:  # only report failures
            print(f"FAIL {name}: {ex!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
