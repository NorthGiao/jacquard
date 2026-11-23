"""LabVIEW machine self-check (read-only + writes doctor.json under BASE). Usage: python lab_doctor.py"""
import shutil, sys
from pathlib import Path
from lablib import BASE, SHLIM, FAKE, LV, timed, dump_json, say, session_info, daqmx_devices, main_guard


def main():
    r = {"session": session_info(), "fake": FAKE}
    BASE.mkdir(parents=True, exist_ok=True)
    r["disk_free_GB"] = round(shutil.disk_usage(BASE).free / 2**30, 1)
    r["shlim_pkg"] = str(SHLIM)
    vis = SHLIM / "labview_mcp" / "vis"
    r["helper_vis"] = sorted(p.name for p in vis.glob("*.vi")) if vis.exists() else "missing"
    try:
        from importlib.metadata import version
        r["pywin32"] = timed(version, "pywin32")
    except Exception as e:  # noqa: BLE001
        r["pywin32"] = str(e)
    if not FAKE:
        sys.path.insert(0, str(SHLIM))
        r["shlim_version"] = timed(lambda: __import__("labview_mcp").__dict__.get("__version__", "?"))
    lv = LV()
    r["lv_status"] = timed(lv.status)
    r["daqmx"] = timed(daqmx_devices)
    r["session_after"] = session_info()
    dump_json(BASE / "doctor.json", r)
    st = r["lv_status"]
    say("session:", r["session"].get("session_id"), "LabVIEW processes:", r["session_after"].get("labview_procs"))
    say("LabVIEW:", st.get("result", {}).get("version") if st["ok"] else st["error"])
    say("DAQmx:", r["daqmx"].get("result"))
    say("number of helper VIs:", len(r["helper_vis"]) if isinstance(r["helper_vis"], list) else r["helper_vis"])
    say("disk free GB:", r["disk_free_GB"])


if __name__ == "__main__":
    main_guard(main)
