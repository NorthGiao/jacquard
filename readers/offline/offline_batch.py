"""Local: batch read-only pylabview extraction of pulled-back VIs (-x, never -c), then pair them with the online dumps by uid.
Input: private/lab_corpus/{lab_dump,lab_copy,probes,write2,...}
Output: private/offline/<key>/ (extraction results, cached), private/pair_summary.json, private/catalog_learned.json, catalog/catalog_public.json
Requires pylabview with the x64 VICD patch (third_party/pylabview-x64-vicd.diff): the environment variable PYLV points to its clone; if not set, look for ../pylabview next to the repo, then ../../pylabview
The uid hit rate only shows "can be paired"; whether the reader is right is judged on a held-out group: using only the mapping learned from the training group, predict for each node in the held-out group
its function name and the name of each terminal, compare with the dump, and report right/wrong/unknown. The held-out group used to be a fixed 20% chosen by key hash;
it was changed to leave-one-group-out cross-validation (grouped by the first two directory levels of the copy, deduplicated by sha256).
Usage: python offline_batch.py [--codec latin-1]
"""
import os, hashlib, json, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pair_check, linkmap  # noqa: E402,F401

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "private"
CORPUS, OFF = ROOT / "lab_corpus", ROOT / "offline"
PY = sys.executable
RUN_PYLV = Path(__file__).resolve().parent / "run_pylv.py"


def pylv_dir() -> Path:
    """Location of the pylabview clone; if not found, say plainly what to do."""
    cands = [Path(os.environ["PYLV"])] if os.environ.get("PYLV") else [REPO.parent / "pylabview", REPO.parents[1] / "pylabview"]
    for c in cands:
        if (c / "pylabview" / "readRSRC.py").is_file():
            return c
    raise SystemExit("pylabview not found (reading .vi needs it for read-only extraction). Looked in: " + ", ".join(str(c) for c in cands) + "\n"
                     "What to do: git clone https://github.com/mefistotelis/pylabview, apply readers/offline/third_party/pylabview-x64-vicd.diff, "
                     "put it in the directory next to the repo (../pylabview), or set the environment variable PYLV=<path of the clone>")


def extract(vi: Path, key: str, codec: str) -> Path:
    d = OFF / key
    if any(d.glob("*_BDHb.xml")):
        return d
    pylv = pylv_dir()
    d.mkdir(parents=True, exist_ok=True)
    src = d / vi.name
    src.write_bytes(vi.read_bytes())
    r = subprocess.run([PY, "-I", str(RUN_PYLV), codec, "-x", "-i", src.name], cwd=d,
                       env={**os.environ, "PYLV": str(pylv)}, capture_output=True, text=True, timeout=600)
    (d / "extract.log").write_text(r.stdout[-5000:] + r.stderr[-5000:], encoding="utf-8")
    src.unlink()            # no VI copy is left in the extraction directory
    return d


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def pairs():
    """Yields (key, group name, sha256, local VI path, dump dict). Only one ground truth per VI: scan preferred (dfir fails on most existing VIs).
    Group name: for existing VIs, the first two directory levels of the copy (a batch of programs from the same source forms one group); probes/write tests are "probe"/"write2"."""
    for j in sorted((CORPUS / "lab_dump").glob("*.json")) if (CORPUS / "lab_dump").exists() else []:
        r = json.loads(j.read_text(encoding="utf-8"))
        if not isinstance(r, dict) or "key" not in r:
            continue
        rel = Path(r["key"].replace("\\", "/"))
        vi = CORPUS / "lab_copy" / rel
        m = next((m for m in ("dump_scan", "dump_dfir") if r.get(m, {}).get("ok")), None)
        if m and vi.exists():
            yield f"lab/{j.stem}", "lab:" + "/".join(rel.parts[:2]), r.get("sha256") or _sha(vi), vi, r[m]["result"]
    for sub in ("probes", "write2"):
        for j in sorted((CORPUS / sub).glob("*.json")) if (CORPUS / sub).exists() else []:
            r = json.loads(j.read_text(encoding="utf-8"))
            vi = j.with_suffix(".vi")
            if not isinstance(r, dict) or not vi.exists():
                continue
            m = next((m for m in ("dump_scan", "dump_dfir") if r.get(m, {}).get("ok")), None)
            if m:
                yield f"{sub}/{j.stem}", sub, _sha(vi), vi, r[m]["result"]
    # dumps added per directory: write2/extra/dumps/<subdir>__<name>.json = {"ok", "result"}, the VI is at write2/<subdir>/<name>.vi
    for j in sorted((CORPUS / "write2" / "extra" / "dumps").glob("*.json")):
        sub, _, stem = j.stem.partition("__")
        vi = CORPUS / "write2" / sub / f"{stem}.vi"
        r = json.loads(j.read_text(encoding="utf-8"))
        if stem in ("noise0", "noise1") or not r.get("ok") or not vi.exists():  # noise0/1 are empty VIs left over from failed builds
            continue
        yield f"write2/{sub}/{stem}", "write2", _sha(vi), vi, r["result"]


def tables(results, exclude_group=None):
    fmap, tmap = defaultdict(Counter), defaultdict(Counter)
    for key, grp, st in results:
        if grp == exclude_group:
            continue
        for cls, rid, fn in st["node_truth"]:
            fmap[(cls, rid)][fn] += 1
        for cls, rid, fn, idx, tname in st["learned"]:
            tmap[(cls, rid, idx)][tname] += 1
    return fmap, tmap


def score_into(score, st, fmap, tmap):
    for cls, rid, fn in st["node_truth"]:
        c = fmap.get((cls, rid))
        if cls == "prim":        # primitives are counted separately (the "function name" of structures/subVIs is for_loop/subvi and the like, too easy to get right)
            score["func_prim"]["unknown" if not c else "right" if c.most_common(1)[0][0] == fn else "wrong"] += 1
        score["func"]["unknown" if not c else "right" if c.most_common(1)[0][0] == fn else "wrong"] += 1
    for cls, rid, fn, idx, tname in st["learned"]:
        c = tmap.get((cls, rid, idx))
        score["term"]["unknown" if not c else "right" if c.most_common(1)[0][0] == tname else "wrong"] += 1


def main():
    a = sys.argv[1:]
    codec = a[a.index("--codec") + 1] if "--codec" in a else "latin-1"
    summary, results, seen = {}, [], {}
    for key, grp, sha, vi, dump in pairs():
        if sha in seen:                      # identical copies: counted only once
            summary[key] = {"dup_of": seen[sha]}
            continue
        seen[sha] = key
        try:
            st = pair_check.pair(dump, extract(vi, key, codec))
        except Exception as e:  # noqa: BLE001
            summary[key] = {"error": f"{type(e).__name__}: {e}"[:300]}
            continue
        summary[key] = {k: v for k, v in st.items() if k not in ("learned", "node_truth")}
        results.append((key, grp, st))
    # leave-one-group-out cross-validation: each group is predicted only with the table built from "all other data"; probes/write tests only go into training
    lab_groups = sorted({g for _, g, _ in results if g.startswith("lab:")})
    score = {"func": Counter(), "func_prim": Counter(), "term": Counter()}
    per_group = {}
    for g in lab_groups:
        fmap, tmap = tables(results, exclude_group=g)
        sg = {"func": Counter(), "func_prim": Counter(), "term": Counter()}
        for key, grp, st in results:
            if grp == g:
                score_into(sg, st, fmap, tmap)
        per_group[g] = {k: dict(v) for k, v in sg.items()}
        for k in sg:
            score[k].update(sg[k])
    fmap, tmap = tables(results)             # final mapping table: all data
    tot = lambda f: sum(s.get(f, 0) for s in summary.values())
    agg = {"n_pairs": len(summary), "n_unique": len(results), "n_dup": sum("dup_of" in s for s in summary.values()),
           "errors": sum("error" in s for s in summary.values()),
           "uid_node_hit": f"{tot('node_hit')}/{tot('node_n')}", "uid_wire_hit": f"{tot('wire_hit')}/{tot('wire_n')}",
           "n_lab_groups": len(lab_groups), "cv_func": dict(score["func"]), "cv_func_prim": dict(score["func_prim"]), "cv_term": dict(score["term"])}
    (ROOT / "pair_summary.json").write_text(json.dumps({"aggregate": agg, "per_group_cv": per_group, "per_vi": summary},
                                                       ensure_ascii=False, indent=1))
    (ROOT / "catalog_learned.json").write_text(json.dumps({
        "functions": {f"{c}|{r}": dict(v) for (c, r), v in sorted(fmap.items(), key=str)},
        "terminals": {f"{c}|{r}|{i}": dict(v) for (c, r, i), v in sorted(tmap.items(), key=str)}},
        ensure_ascii=False, indent=1))
    # the mapping table that goes into the repo: remove the user's own subVIs ("user:"), the rest are names of NI primitives/structures/vi.lib
    (REPO / "catalog" / "catalog_public.json").write_text(json.dumps({
        "functions": {f"{c}|{r}": dict(v) for (c, r), v in sorted(fmap.items(), key=str) if not str(r).startswith("user:")},
        "terminals": {f"{c}|{r}|{i}": dict(v) for (c, r, i), v in sorted(tmap.items(), key=str) if not str(r).startswith("user:")}},
        ensure_ascii=False, indent=1))
    print(json.dumps(agg, ensure_ascii=False))
    conflicts = [k for k, v in list(fmap.items()) + list(tmap.items()) if len(v) > 1]
    print(f"mapping table: {len(fmap)} node kinds, {len(tmap)} terminal entries; conflicts {len(conflicts)}: {conflicts[:8]}")


if __name__ == "__main__":
    main()
