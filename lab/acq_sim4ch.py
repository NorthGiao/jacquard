"""SimDev (a simulated device in NI MAX) 4-channel AI finite acquisition + save switch, full dry run.
The VI is written from scratch under BASE\\acq\\, no existing VI is modified. AI read only (no AO/DO), SimDev only.
Data flow: Create Channel(AI-Voltage-Basic) → Timing(Sample Clock, finite) → Start → Read(2D DBL NChan NSamp)
       → Clear; data → indicator; in case(save) Write To Spreadsheet File (DBL) (transpose=True → one column per channel).
Errors are all wired to the err indicator (no General Error Handler, to avoid invisible pop-up dialogs).
Before each run: daqmx_devices() confirms SimDev is a simulated device and the channel string starts with "SimDev/"; check path wiring before running.
Check: data is 4×N; with save=True the file has N rows, 4 columns, and column k == row k of data (%.3f precision); save=False writes no file.
Usage: python acq_sim4ch.py
"""
import os
from pathlib import Path
from lablib import BASE, LV, timed, dump_json, say, must_sandbox, main_guard, daqmx_devices

OUT = BASE / "acq"
DEV = os.environ.get("LVAGENT_SIMDEV", "SimDev")
PHYS = f"{DEV}/ai0:3"
N, RATE = 100, 1000.0
FP = "file path (dialog if empty)"


DAQMX_ROOTS = [r"C:\Program Files\NI\LVAddons\nidaqmx\1\vi.lib"]   # since 2024 Q1 DAQmx is an LVAddon (measured on-site)


def vilib(app, *parts):
    if parts[0] == "DAQmx":
        for r in DAQMX_ROOTS + [str(Path(app) / "vi.lib")]:
            if (Path(r) / "DAQmx").exists():
                return str(Path(r) / Path(*parts))
    return str(Path(app) / "vi.lib" / Path(*parts))


def spec(app):
    d = lambda n, t, **kw: {"name": n, "type": t, **kw}
    return {
        "controls": [d("phys", "string", default=PHYS), d("rate", "dbl", default=RATE), d("n", "i32", default=N),
                     d("save", "bool"), d("fpath", "path")],
        "indicators": [d("data", "dbl[][]"), d("err", "error")],
        "subvis": [
            {"name": "cc", "path": vilib(app, "DAQmx", "create", "channels.llb", "DAQmx Create Channel (AI-Voltage-Basic).vi")},
            {"name": "tm", "path": vilib(app, "DAQmx", "configure", "timing.llb", "DAQmx Timing (Sample Clock).vi")},
            {"name": "st", "path": vilib(app, "DAQmx", "configure", "task.llb", "DAQmx Start Task.vi")},
            {"name": "rd", "path": vilib(app, "DAQmx", "read.llb", "DAQmx Read (Analog 2D DBL NChan NSamp).vi")},
            {"name": "cl", "path": vilib(app, "DAQmx", "configure", "task.llb", "DAQmx Clear Task.vi")}],
        "structures": [{"name": "C", "type": "case", "cases": [
            {"value": True, "subvis": [{"name": "w", "path": vilib(app, "Utility", "file.llb", "Write To Spreadsheet File (DBL).vi")}],
             "constants": [{"name": "tr", "type": "bool", "value": True}]},
            {"value": False}]}],
        "wires": [
            ["phys", "cc.physical channels"],
            ["cc.task out", "tm.task/channels in"], ["cc.error out", "tm.error in"],
            ["rate", "tm.rate"], ["n", "tm.samples per channel"],
            ["tm.task out", "st.task/channels in"], ["tm.error out", "st.error in"],
            ["st.task out", "rd.task/channels in"], ["st.error out", "rd.error in"],
            ["n", "rd.number of samples per channel"],
            ["rd.task out", "cl.task in"], ["rd.error out", "cl.error in"], ["cl.error out", "err"],
            ["rd.data", "data"],
            ["save", "C.selector"], ["rd.data", "w.2D data"], ["tr", "w.transpose? (no:F)"], ["fpath", f"w.{FP}"]],
    }


def sim_ok():
    devs = daqmx_devices().get("devices", [])
    return any(x["name"] == DEV and x["simulated"] for x in devs), devs


def vals(res):
    r = res.get("result") or {}
    return r.get("values") or r.get("outputs") or r.get("indicators") or r


def check(data, fpath):
    out = {"data_shape": [len(data), len(data[0]) if data else 0]}
    if fpath is None:
        return out
    rows = [ln.split("\t") for ln in Path(fpath).read_text(encoding="mbcs", errors="replace").splitlines() if ln.strip()]
    t = [[float(x) for x in r] for r in rows]
    out["file_shape"] = [len(t), len(t[0]) if t else 0]
    bad = [(i, k) for i in range(min(len(t), N)) for k in range(min(4, len(t[i]), len(data)))
           if abs(t[i][k] - data[k][i]) > 6e-4]
    out["col_mismatch"] = bad[:10]
    out["pass"] = out["data_shape"] == [4, N] and out["file_shape"] == [N, 4] and not bad
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok, devs = sim_ok()
    rep = {"phys": PHYS, "devices": [(x["name"], x["product"], x["simulated"]) for x in devs]}
    say("phys:", PHYS)
    if not ok:
        dump_json(OUT / "report.json", rep)
        raise SystemExit(f"Refused: {DEV} does not exist or is not a simulated device")
    assert PHYS.startswith(DEV + "/")
    lv = LV()
    app = lv.status().get("app_dir", "")
    vi = OUT / "acq_sim4ch.vi"
    rep["create"] = timed(lv.create, vi, spec(app), "SimDev 4-channel AI finite acquisition + save switch (simulated dry run)")
    say("create:", "OK" if rep["create"]["ok"] else rep["create"]["error"][:400])
    if not rep["create"]["ok"]:
        dump_json(OUT / "report.json", rep)
        return
    d = timed(lv.dump, vi, "scan")
    nfp = sum(1 for w in d["result"]["wires"] for e in w["to"] if FP in e) if d["ok"] else 0
    nw = len(d["result"]["wires"]) if d["ok"] else 0
    rep["wirecheck"] = {"path_wires": nfp, "wires": nw, "spec_wires": len(spec(app)["wires"])}
    say("wirecheck:", rep["wirecheck"])
    if nfp < 1:
        dump_json(OUT / "report.json", rep)
        say("path not wired: not running")
        return
    f_on, f_off = must_sandbox(OUT / "acq_on.txt"), must_sandbox(OUT / "acq_off.txt")
    for f in (f_on, f_off):
        f.unlink(missing_ok=True)
    for tag, save, f in (("on", True, f_on), ("off", False, f_off)):
        if not sim_ok()[0]:
            raise SystemExit("Re-check before running: SimDev is no longer a simulated device, stopping")
        r = timed(lv.run, vi, {"phys": PHYS, "rate": RATE, "n": N, "save": save, "fpath": str(f)}, ["data", "err"], 30.0)
        rep[f"run_{tag}"] = {"ok": r["ok"], "error": r.get("error"), "err": (vals(r) or {}).get("err") if r["ok"] else None}
        if r["ok"]:
            data = (vals(r) or {}).get("data") or []
            rep[f"check_{tag}"] = check(data, f if save and f.exists() else None)
            if save and not f.exists():
                rep[f"check_{tag}"]["pass"] = False
                rep[f"check_{tag}"]["note"] = "save=True but no file was written"
        say(f"run_{tag}:", rep[f"run_{tag}"], rep.get(f"check_{tag}"))
    rep["off_wrote_nothing"] = not f_off.exists()
    dump_json(OUT / "report.json", rep)
    say("off_wrote_nothing:", rep["off_wrote_nothing"])
    say("ACQ4 COMPLETE")


if __name__ == "__main__":
    main_guard(main)
