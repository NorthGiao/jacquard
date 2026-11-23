"""Run pylabview readRSRC.py from the cwd, with selectable text codec.
  python -I run_pylv.py <codec-name> <readRSRC args...>
codec 'gbkr' reproduces the previous agent's lossy codec (replace on decode AND encode).
Any other name is passed through as-is (e.g. latin-1 = lossless byte<->char).
"""
import codecs, os, runpy, sys
codec = sys.argv[1]
if "-x" not in sys.argv[2:] or any(a in ("-c", "--create") or a.startswith("--create") for a in sys.argv[2:]):
    # any use other than read-only extraction is refused: pylabview repacking LV2024 VIs corrupts them (VICD), and gbkr is a lossy encoding on top of that
    sys.exit("run_pylv.py only does read-only extraction (-x); repacking VIs (-c) is not allowed")
if codec == 'gbkr':
    _g = codecs.lookup('gbk')
    codecs.register(lambda n: codecs.CodecInfo(lambda s, e='strict': _g.encode(s, 'replace'),
                                               lambda b, e='strict': _g.decode(b, 'replace'), name='gbkr') if n == 'gbkr' else None)
PYLV = os.environ['PYLV']  # a pylabview clone with third_party/pylabview-x64-vicd.diff applied (read-only extraction -x; never repack, gbkr is lossy)
sys.path.insert(0, PYLV)
sys.argv = ['readRSRC.py'] + sys.argv[2:] + ['-t', codec]
runpy.run_path(os.path.join(PYLV, 'pylabview', 'readRSRC.py'), run_name='__main__')
