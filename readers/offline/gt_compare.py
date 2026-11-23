"""① Check against the answers: the front-panel defaults read by LabVIEW (flat type string + default data in lab_corpus/gt/*.json)
vs. the DFDS defaults from offline pylabview extraction (as defined by vi_outline.dfds_defaults). Paired by label; only scalars and 1-D numeric arrays are compared.
Writes offline/gt_compare.json (contains the user's VI names, private); stdout only prints the summary.
Usage: python gt_compare.py"""
import glob, json, re, struct, sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "private"
sys.path.insert(0, str(REPO / "writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import codecs  # noqa: E402
try:
    codecs.lookup("mbcs")
except LookupError:  # there's no mbcs on Linux: the LabVIEW machine is a Simplified Chinese system, ANSI code page = GBK
    codecs.register(lambda n: codecs.lookup("gbk") if n == "mbcs" else None)
from labview_mcp import td  # noqa: E402
import vi_outline  # noqa: E402

FMT = {"i8": "b", "i16": "h", "i32": "i", "i64": "q", "u8": "B", "u16": "H", "u32": "I", "u64": "Q", "sgl": "f", "dbl": "d"}


def dec(t, b, p=0):
    """flattened data -> (value, new offset); unsupported types raise ValueError."""
    if isinstance(t, str) and t in FMT:
        n = struct.calcsize(FMT[t])
        return struct.unpack_from(">" + FMT[t], b, p)[0], p + n
    if t == "bool":
        return bool(b[p]), p + 1
    if t == "string":
        n = struct.unpack_from(">i", b, p)[0]
        return b[p + 4:p + 4 + n].decode("gbk", "replace"), p + 4 + n
    if isinstance(t, dict) and "enum" in t:
        return t["enum"][struct.unpack_from(">H", b, p)[0]] if struct.unpack_from(">H", b, p)[0] < len(t["enum"]) else None, p + 2
    if isinstance(t, str) and t.endswith("[]") and t.count("[]") == 1 and t[:-2] in FMT:
        n = struct.unpack_from(">i", b, p)[0]
        p += 4
        out = []
        for _ in range(n):
            v, p = dec(t[:-2], b, p)
            out.append(v)
        return out, p
    raise ValueError(f"unsupported {t}")


def norm(v):
    if isinstance(v, bool):
        return ["1" if v else "0", "true" if v else "false"]
    if isinstance(v, float):
        return [repr(v)]
    return [str(v)]


def same(gt, off_vals):
    """gt: decoded value; off_vals: list of DFDS leaf value strings."""
    g = gt if isinstance(gt, list) else [gt]
    if len(g) != len(off_vals):
        return False
    for a, s in zip(g, off_vals):
        s = s.strip()
        if isinstance(a, bool):
            if s.lower() not in ("1", "true", "0", "false") or (s.lower() in ("1", "true")) != a:
                return False
        elif isinstance(a, (int, float)):
            try:
                x = float(s)
            except ValueError:
                return False
            if not (abs(x - a) <= 1e-9 * max(1.0, abs(a)) or (x != x and a != a)):
                return False
        elif str(a) != s:
            return False
    return True


def dfds_raw(main_xml):
    raw = Path(main_xml).read_bytes().decode("utf-8", "replace")
    m = re.search(r"<DFDS>(.*?)</DFDS>", raw, re.S)
    out = {}
    for blk in re.findall(r"<DataFill [^>]*>(.*?)</DataFill>", m.group(1) if m else "", re.S):
        c = re.search(r"<!--(.*?)-->", blk, re.S)
        name = c.group(1).strip() if c else ""
        if not name or name.startswith(vi_outline.INTERNAL_DFDS):
            continue
        vals = re.findall(r"<(DBL|SGL|EXT|I8|I16|I32|I64|U8|U16|U32|U64|String|Boolean|Bool)>([^<]*)</\1>", blk)
        out.setdefault(name, []).append([x[1] for x in vals])
    return out


def main():
    score, rows = Counter(), []
    for f in sorted(glob.glob(str(ROOT / "lab_corpus/gt/*.json"))):
        if Path(f).name.startswith("_"):
            continue
        r = json.loads(Path(f).read_text(encoding="utf-8"))
        res = r.get("result") or {}
        key = r["key"]
        d = ROOT / "offline/lab" / key.replace("\\", "__")
        mx = d / (key.split("\\")[-1][:-3] + ".xml")
        if not mx.exists():
            score["no_offline"] += 1
            continue
        off = dfds_raw(mx)
        for c in res.get("fp", []):
            if not c.get("flat") or c.get("data") is None or str(c["data"]).startswith("<"):
                score["gt_unreadable"] += 1
                continue
            try:
                t, lab, _ = td.parse(bytes.fromhex(c["flat"]))
                c = dict(c, label=c["label"] or lab)
                v, _ = dec(t, bytes.fromhex(c["data"]))
            except Exception:
                score["type_skipped"] += 1
                continue
            cand = off.get(c["label"])
            if not cand:
                score["no_dfds_entry"] += 1
                rows.append({"vi": key, "label": c["label"], "type": t, "gt": v, "off": None, "verdict": "missing"})
                continue
            if isinstance(t, dict) and "enum" in t:  # DFDS stores the index
                v = t["enum"].index(v) if v in t["enum"] else v
            ok = any(same(v, vals) for vals in cand)
            score["right" if ok else "wrong"] += 1
            if not ok:
                rows.append({"vi": key, "label": c["label"], "type": t, "gt": v, "off": cand[:2], "verdict": "wrong"})
    (ROOT / "offline/gt_compare.json").write_text(json.dumps({"score": score, "mismatch": rows}, ensure_ascii=False, indent=1, default=str))
    print(dict(score))
    print("wrong type distribution:", Counter(json.dumps(x["type"]) for x in rows if x["verdict"] == "wrong").most_common(8))
    print("missing type distribution:", Counter(json.dumps(x["type"]) for x in rows if x["verdict"] == "missing").most_common(8))


if __name__ == "__main__":
    main()
