"""Local offline VI reading: pylabview read-only extraction (-x, latin-1 lossless) → vi_outline outline. No LabVIEW needed.

Usage:
    python readers/outline_vi.py <a.vi | dir>... [--out dir] [--text gbk|none] [--print]
Directories are searched recursively for all .vi (just give the main VI and its subVIs together). .ctl/.vit haven't been tried on real files and aren't handled.
Extractions are cached in private/offline/single/<name>-<sha8>/ (the same file isn't extracted twice).
Outlines are written by default to private/offline/outlines/<input name>/<relative subdir>/<name>.outline.txt, preserving the input directory structure,
so different VIs with the same name (backup copies, different versions) don't overwrite each other; names still clashing within one call get -<sha8> appended to the file name.
The user's VIs and outlines only go under private/; --out pointing outside private/ is refused unless --public is added (only for VIs the agent built itself).
The outline has: dependencies, defaults, front-panel controls ([control] = input, [indicator] = indicator), block-diagram structures and nodes; no wires (the wire table isn't decoded). Wires need an on-site scan dump, see readers/vi_trace.py.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "offline"))
import offline_batch as OB  # noqa: E402

# extraction needs pylabview with the x64 VICD patch: location see offline_batch.pylv_dir() (environment variable PYLV, or ../pylabview next to the repo)

REPO = HERE.parent
PRIVATE = REPO / "private"
EXT = {".vi"}


def collect(args):
    """[(vi path, input name, subdirectory relative to the input)]"""
    out = []
    for a in args:
        if not a.strip():
            raise SystemExit("empty path argument (shell variable not expanded?)")
        p = Path(a)
        if p.is_dir():
            root = p.resolve()
            out += [(x, root.name, x.parent.relative_to(root)) for x in sorted(root.rglob("*")) if x.suffix.lower() in EXT and x.is_file()]
        elif p.is_file():
            out.append((p, p.resolve().parent.name, Path(".")))
        else:
            raise SystemExit(f"not found: {a}")
    return out


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default):
        if name in a:
            i = a.index(name)
            if i + 1 >= len(a) or a[i + 1].startswith("--"):   # missing value: `--out --print` must not take --print as the directory
                raise SystemExit(f"{name} must be followed by a value (see usage)")
            v = a[i + 1]
            del a[i:i + 2]
            return v
        return default

    out = Path(opt("--out", str(PRIVATE / "offline" / "outlines"))).resolve()
    text = opt("--text", "gbk")
    show = "--print" in a
    public = "--public" in a
    a = [x for x in a if x not in ("--print", "--public")]
    unknown = [x for x in a if x.startswith("--")]   # misspelled options (or --out / --text given twice)
    if unknown:
        raise SystemExit(f"unknown options {unknown} (see usage)")
    if not a:
        raise SystemExit(__doc__)
    if not public and PRIVATE.resolve() not in out.parents and out != PRIVATE.resolve():
        raise SystemExit(f"--out {out} is not under private/; outlines of the user's VIs only go in private/ (add --public only for VIs the agent built itself)")
    items, fails, seen = [], [], set()
    for vi, root, rel in collect(a):
        sha = hashlib.sha256(vi.read_bytes()).hexdigest()[:8]
        d = OB.extract(vi, f"single/{vi.stem}-{sha}", "latin-1")
        if not any(d.glob("*_BDHb.xml")):
            fails.append(f"{vi.name}: extraction produced no block diagram (see {d / 'extract.log'})")
            continue
        dest = out / root / rel
        name = f"{vi.stem}.outline.txt"
        if (dest / name) in seen:
            name = f"{vi.stem}-{sha}.outline.txt"
        seen.add(dest / name)
        items.append((vi, d, dest, name))
    for vi, d, dest, name in items:
        # the extraction directory is called <name>-<sha8>, the files in it are called <name>.xml: tell vi_outline with --name (no symlinks, Windows has no permission to create them by default);
        # output is written first to this VI's own temporary directory, then copied to the destination
        work = d.parent / ".by_name" / d.name.rsplit("-", 1)[1]
        work.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([sys.executable, str(HERE / "offline" / "vi_outline.py"), str(d), "--name", vi.stem,
                            "--out", str(work / "out"), "--text", text], capture_output=True, text=True, encoding="utf-8", errors="replace")
        msg = (r.stdout + r.stderr).strip()
        produced = work / "out" / f"{vi.stem}.outline.txt"
        if "FAIL" in msg or r.returncode or not produced.exists():
            fails.append(f"{vi.name}: {msg}")
            continue
        dest.mkdir(parents=True, exist_ok=True)
        f = dest / name
        f.write_text(produced.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"{msg}  → {f}")
        if show:
            print(f.read_text(encoding="utf-8"))
    for x in fails:
        print("failed:", x, file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
