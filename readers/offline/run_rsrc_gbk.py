"""Run pylabview's readRSRC.py with lenient GBK decoding (VIs saved on Chinese Windows contain illegal multibyte sequences).

Usage (run in the directory where the XML should be written):
    python run_rsrc_gbk.py -t gbkr -x -i some.vi
The pylabview location is given by the environment variable PYLABVIEW_DIR; if not set, look for ../pylabview, then ../../pylabview next to the repo
(git clone --depth 1 https://github.com/mefistotelis/pylabview.git).
"""
from pathlib import Path
import codecs
import os
import runpy
import sys

if "-x" not in sys.argv[1:] or any(a in ("-c", "--create") or a.startswith("--create") for a in sys.argv[1:]):
    # gbkr both decodes and encodes with replace (lossy): VIs repacked with it "can't load the front panel" in LabVIEW. Only read-only extraction is allowed
    sys.exit("run_rsrc_gbk.py only does read-only extraction (-x); gbkr is a lossy encoding and must not be used to repack VIs (-c)")

_g = codecs.lookup("gbk")


def _dec(b, errors="strict"):
    return _g.decode(b, "replace")


def _enc(s, errors="strict"):
    return _g.encode(s, "replace")


codecs.register(lambda name: codecs.CodecInfo(_enc, _dec, name="gbkr") if name == "gbkr" else None)

_repo = Path(__file__).resolve().parents[2]
PYLV = os.environ.get("PYLABVIEW_DIR") or next(
    (str(c) for c in (_repo.parent / "pylabview", _repo.parents[1] / "pylabview") if (c / "pylabview" / "readRSRC.py").is_file()),
    str(_repo.parent / "pylabview"))
sys.path.insert(0, PYLV)
sys.argv = ["readRSRC.py"] + sys.argv[1:]
runpy.run_path(os.path.join(PYLV, "pylabview", "readRSRC.py"), run_name="__main__")
