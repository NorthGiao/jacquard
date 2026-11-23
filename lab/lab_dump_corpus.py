"""Read-only export of existing VIs.
Step 1 (copy): copy the LabVIEW files (excluding data files) in the --src directory tree, preserving structure, into BASE\\lab_copy\\<name>\\.
Step 2 (dump): run vi_info + vi_dump(scan) + vi_dump(dfir) + vi_hierarchy on each copy, results written to BASE\\lab_dump\\.
These VIs are never run or saved (vi_dump only loads the VI into memory and runs shlim-t's own export helper).
Processed from smallest to largest file size. Resumable: after being killed by the outer timeout just rerun this script, the hung one is automatically recorded as hung.
Usage:
  python lab_dump_corpus.py copy <src_dir> <name>
  python lab_dump_corpus.py dump [--sample N (stratified by size, take N)] [--only substring]
"""
import os, shutil, sys
from pathlib import Path
from lablib import BASE, LV, Resume, timed, dump_json, sha256, say, must_sandbox, main_guard

LV_EXT = {".vi", ".ctl", ".vit", ".ctt", ".llb", ".lvlib", ".lvclass", ".lvproj", ".xctl", ".mnu", ".rtm"}
COPY = BASE / "lab_copy"
OUT = BASE / "lab_dump"
MAX_BYTES = 2 * 2**30


def do_copy(src, name):
    src = Path(src)
    dst = must_sandbox(COPY / name)
    if not src.is_dir():
        raise SystemExit(f"not a directory: {src}")
    files = [p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in LV_EXT]
    total = sum(p.stat().st_size for p in files)
    say(f"{len(files)} LabVIEW files, {total / 2**20:.1f} MB in total")
    if total > MAX_BYTES:
        raise SystemExit("over 2 GB, stopping for confirmation")
    manifest = []
    for p in files:
        q = dst / p.relative_to(src)
        q.parent.mkdir(parents=True, exist_ok=True)
        if not q.exists():
            shutil.copy2(p, q)
        manifest.append({"src": str(p), "copy": str(q), "bytes": p.stat().st_size, "sha256": sha256(q)})
    dump_json(dst / "_manifest.json", manifest)
    say("copy finished:", dst)


def order(paths):
    # from smallest to largest, to get as many samples as possible first
    return sorted(paths, key=lambda p: p.stat().st_size)


def stratified(vis, n):
    """Take n stratified by size (covering small subVIs up to large main programs)."""
    pool = sorted(vis, key=lambda p: p.stat().st_size)
    if n >= len(pool):
        return pool
    idx = sorted({round(i * (len(pool) - 1) / (n - 1)) for i in range(n)}) if n > 1 else [len(pool) // 2]
    return [pool[i] for i in idx]


def same_path(a, b):
    import os
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def do_dump(sample=None, only=None, retry_hung=False):
    vis = [p for p in COPY.rglob("*") if p.is_file() and p.suffix.lower() in (".vi", ".ctl", ".vit")]
    if only:
        vis = [p for p in vis if only.lower() in str(p).lower()]
    vis = stratified(vis, int(sample)) if sample else order(vis)
    rs = Resume(OUT)
    if retry_hung:   # only retry those that hung last time: remove from the hung list, scan only (dfir/hierarchy skipped), relaxed timeout
        want = set(rs.hung)
        vis = [p for p in vis if str(p.relative_to(COPY)) in want]
        sel = {str(p.relative_to(COPY)) for p in vis}
        rs.hung = [k for k in rs.hung if k not in sel]
        dump_json(rs.hung_file, rs.hung)
        os.environ.setdefault("LVAGENT_DUMP_TIMEOUT", "900")
    lv = LV()
    n_ok = n_dup = deferred = 0
    seen = {}   # sha256 → completed key: identical copies are dumped only once
    for j in OUT.glob("*.json"):
        r0 = Resume.prev(j)
        if isinstance(r0, dict) and r0.get("complete") and r0.get("sha256") and "dup_of" not in r0:
            seen.setdefault(r0["sha256"], r0["key"])
    loaded = set()  # VI names loaded this round; one LabVIEW instance can't hold two VIs with the same name
    for i, p in enumerate(vis):
        key = str(p.relative_to(COPY))
        res_path = OUT / (key.replace("\\", "__").replace("/", "__") + ".json")
        if rs.skip(key, res_path):
            continue
        h = sha256(p)
        if h in seen and seen[h] != key:
            dump_json(res_path, {"key": key, "sha256": h, "dup_of": seen[h], "complete": True, "attempts": 0})
            n_dup += 1
            continue
        if p.name.lower() in loaded:
            deferred += 1          # pushed to the next round (new instance)
            continue
        loaded.add(p.name.lower())
        rs.begin(key)
        say(f"[{i + 1}/{len(vis)}] {key}")
        r = {"key": key, "path": str(p), "sha256": h, "bytes": p.stat().st_size}
        r["info"] = timed(lv.info, p)
        got = (r["info"].get("result") or {}).get("path")
        if r["info"]["ok"] and got and not same_path(got, p):
            # a VI with the same name is already in memory (most likely a same-named subVI of an earlier VI): the dump would attach to another file, unusable, push to the next round
            rs.end()
            deferred += 1
            say("   a VI with the same name is already in memory (another file): pushed to the next round")
            continue
        r["dump_scan"] = timed(lv.dump, p, "scan")
        if retry_hung:
            r["dump_dfir"] = {"ok": False, "error": "skipped (retry of hung VI)"}
        else:
            r["dump_dfir"] = timed(lv.dump, p, "dfir")
            r["hierarchy"] = timed(lv.hierarchy, p, 1)
        rs.finish(res_path, r, r["dump_scan"]["ok"])
        if r["dump_scan"]["ok"]:
            seen.setdefault(h, key)
        n_ok += r["dump_scan"]["ok"]
        say("   scan", "OK" if r["dump_scan"]["ok"] else r["dump_scan"]["error"][:150],
            "| dfir", "OK" if r["dump_dfir"]["ok"] else "FAIL")
    say(f"this round: scan succeeded {n_ok}, identical copies {n_dup}, pushed to the next round {deferred}; hung: {rs.hung}")
    if deferred:
        say("NEED RESTART")
    else:
        say("CORPUS COMPLETE")


def main():
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    if a[0] == "copy":
        do_copy(a[1], a[2])
    elif a[0] == "dump":
        kw = dict(zip(a[1::2], a[2::2]))
        do_dump(kw.get("--sample"), kw.get("--only"), "--retry-hung" in a)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main_guard(main)
