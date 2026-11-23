"""Offline: the LinkOffsetList of each IUVI/PUPV entry in LIbd (the block diagram link table) is the uid of the calling node (measured on-site).
From this we get {node uid: {"vi": called VI name, "poly": polymorphic VI name or None, "path": [...]}}, without LabVIEW.
The main .xml may be large and contain illegal characters, so only the <LIbd> section is cut out with a regex."""
import re
from pathlib import Path

_ENT = re.compile(r'<(IUVI|PUPV)\b[^>]*>.*?<LinkSaveQualName>(.*?)</LinkSaveQualName>.*?<LinkSavePathRef[^>]*>(.*?)</LinkSavePathRef>.*?<LinkOffsetList>(.*?)</LinkOffsetList>', re.S)
_S = re.compile(r'<String>(.*?)</String>', re.S)


def _unesc(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')


def subvi_map(d):
    xs = [p for p in Path(d).glob("*.xml") if not p.name.endswith(("_BDHb.xml", "_FPHb.xml"))]
    if not xs:
        return {}
    t = xs[0].read_text(encoding="utf-8", errors="replace")
    a, b = t.find("<LIbd>"), t.find("</LIbd>")
    if a < 0:
        return {}
    out = {}
    for tag, q, pth, offs in _ENT.findall(t[a:b]):
        name = _unesc(_S.findall(q)[-1]) if _S.findall(q) else None
        for o in re.findall(r"0x[0-9A-Fa-f]+", offs):
            e = out.setdefault(str(int(o, 16)), {"vi": None, "poly": None, "path": None})
            if tag == "PUPV":
                e["poly"] = name
            else:
                e["vi"] = name
                e["path"] = [_unesc(s) for s in _S.findall(pth)]
    return out
