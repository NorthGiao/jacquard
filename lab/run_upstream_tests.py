"""Run shlim-t's upstream live tests against the patched writer.
The upstream tests build VIs in tempfile.mkdtemp() and run them; here tempfile.tempdir is pointed at write2\\upstream_tests\\,
making sure the VIs they build and run are all under write2 (all pure-computation VIs: CASES were reviewed one by one, no file/DAQ/AO/DO).
The test files are pushed to <code>\\upstream\\ beforehand by scripts. Usage: python run_upstream_tests.py live_vi_create.py live_vi_edit.py"""
import runpy, sys, tempfile, time
from pathlib import Path
from lablib import BASE, SHLIM, say, must_runnable

def main():
    d = BASE / "write2" / "upstream_tests"
    d.mkdir(parents=True, exist_ok=True)
    must_runnable(d / "x.vi")                   # assert it's under write2
    tempfile.tempdir = str(d)
    sys.path.insert(0, str(SHLIM))
    here = Path(__file__).parent / "upstream"
    rc = {}
    for f in sys.argv[1:]:
        t0 = time.time()
        sys.argv = [str(here / f)]
        say(f"==== UPSTREAM {f}")
        try:
            runpy.run_path(str(here / f), run_name="__main__")
            rc[f] = 0
        except SystemExit as e:
            rc[f] = e.code
        except BaseException as e:  # noqa: BLE001
            rc[f] = f"{type(e).__name__}: {str(e)[:300]}"
        say(f"==== UPSTREAM {f} rc={rc[f]} {time.time() - t0:.0f}s")
        sys.stdout.flush()
    say("UPSTREAM RESULT", rc)
    say("UPSTREAM COMPLETE")


if __name__ == "__main__":
    main()
