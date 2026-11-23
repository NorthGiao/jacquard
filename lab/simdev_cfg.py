"""NI MAX simulated devices: back up → create → delete → verify restoration. All files in BASE\\simdev\\.
Interfaces and constants taken from the local nisyscfg.h (C:\\Program Files (x86)\\National Instruments\\Shared\\ExternalCompilerSupport\\C\\include).
Usage: python simdev_cfg.py backup | create <name> <template device> | delete <name> | verify
- create: copy the section of the template device (must be a simulated device) from the backup, rename it and import with Merge|PreserveConflictItems -- existing items always keep the local version, real devices are not changed.
- delete: only deletes resources with "name=<name> and IsSimulated=True".
- verify: export again and compare with backup (should be identical except timestamp lines).
"""
import ctypes, json, re, sys
from pathlib import Path
from lablib import BASE, say, dump_json, daqmx_devices, main_guard

OUT = BASE / "simdev"
MERGE_PRESERVE = 0x200000
FILTER_IS_DEVICE, FILTER_IS_SIM, FILTER_ALIAS = 16781312, 16814080, 16904192
IDX_EXPERT_ALIAS, IDX_EXPERT_NAME = 16904192, 16900096
DELETE_IF_NO_DEPS = 0
V = ctypes.c_void_p

dll = ctypes.WinDLL("nisyscfg.dll")


def ck(st, what):
    if st < 0:
        raise RuntimeError(f"{what}: NISysCfg status {st}")
    return st


def session():
    s = V()
    ck(dll.NISysCfgInitializeSession(None, None, None, 0, 1, 10000, None, ctypes.byref(s)), "InitializeSession")
    return s


def export(s, path, experts):
    ck(dll.NISysCfgExportConfiguration(s, str(path).encode("mbcs"), experts, 1), f"Export {experts}")


def backup():
    OUT.mkdir(parents=True, exist_ok=True)
    s = session()
    export(s, OUT / "backup_daqmx.nce", b"daqmx")
    export(s, OUT / "backup_all.nce", None)
    t = (OUT / "backup_daqmx.nce").read_bytes()
    say("backup ok:", len(t), "bytes daqmx;", (OUT / "backup_all.nce").stat().st_size, "bytes all")
    txt = t.decode("mbcs", errors="replace")
    say("sections:", re.findall(r"^\[[^\]]+\]", txt, re.M)[:40])
    m = re.search(r"^\[DAQmxDevice SimDev\][^\[]*", txt, re.M)
    say("SimDev section:\n" + (m.group(0) if m else "(not found)"))


def section(txt, name):
    m = re.search(r"^\[DAQmxDevice " + re.escape(name) + r"\]\r?\n[^\[]*", txt, re.M)
    return m.group(0) if m else None


def create(name, tmpl):
    """.nce is a binary container with length fields, embedding DAQmx INI text. To keep the length fields intact: copy the backup, only rename the section of the template (simulated) device
    to a new name of the same length, then import with Merge|PreserveConflictItems -- existing devices keep the local version and are not changed."""
    assert len(name) == len(tmpl), "the new name must have the same length as the template name"
    b = (OUT / "backup_daqmx.nce").read_bytes()
    old_h, new_h = f"[DAQmxDevice {tmpl}]".encode(), f"[DAQmxDevice {name}]".encode()
    assert b.count(old_h) == 1 and b.count(new_h) == 0
    i = b.index(old_h)
    sec = b[i:b.index(b"BusType", i)]
    assert b"DevIsSimulated = 1" in sec, "the template must be a simulated device"
    f = OUT / f"create_{name}.nce"
    f.write_bytes(b.replace(old_h, new_h, 1))
    s = session()
    det = ctypes.c_char_p()
    st = dll.NISysCfgImportConfiguration(s, str(f).encode("mbcs"), b"daqmx", MERGE_PRESERVE, ctypes.byref(det))
    say("import status", st, (det.value or b"").decode("mbcs", errors="replace")[:2000])
    ck(st, "Import")
    say("devices:", [(d["name"], d["product"], d["simulated"]) for d in daqmx_devices()["devices"]])


def find(s, name):
    flt = V()
    ck(dll.NISysCfgCreateFilter(s, ctypes.byref(flt)), "CreateFilter")
    dll.NISysCfgSetFilterProperty.argtypes = None
    ck(dll.NISysCfgSetFilterProperty(flt, FILTER_IS_DEVICE, ctypes.c_int(1)), "filter IsDevice")
    ck(dll.NISysCfgSetFilterProperty(flt, FILTER_IS_SIM, ctypes.c_int(1)), "filter IsSimulated")
    ck(dll.NISysCfgSetFilterProperty(flt, FILTER_ALIAS, ctypes.c_char_p(name.encode())), "filter alias")
    en = V()
    ck(dll.NISysCfgFindHardware(s, 1, flt, b"daqmx", ctypes.byref(en)), "FindHardware")
    out = []
    while True:
        r = V()
        if dll.NISysCfgNextResource(s, en, ctypes.byref(r)) != 0 or not r.value:
            break
        buf = ctypes.create_string_buffer(1024)
        dll.NISysCfgGetResourceIndexedProperty(r, IDX_EXPERT_ALIAS, 0, buf)
        out.append((r, buf.value.decode("mbcs", errors="replace")))
    return out


def delete(name):
    devs = {d["name"]: d for d in daqmx_devices()["devices"]}
    assert name in devs and devs[name]["simulated"], f"{name} does not exist or is not a simulated device: not deleting"
    s = session()
    hits = [(r, a) for r, a in find(s, name) if a == name]
    say("found:", [a for _, a in hits])
    assert len(hits) == 1, "exactly one should be found"
    det = ctypes.c_char_p()
    st = dll.NISysCfgDeleteResource(hits[0][0], DELETE_IF_NO_DEPS, None, ctypes.byref(det))
    say("delete status", st, (det.value or b"").decode("mbcs", errors="replace")[:1000])
    ck(st, "Delete")
    say("devices:", [(d["name"], d["product"], d["simulated"]) for d in daqmx_devices()["devices"]])


def norm(b):
    return [l for l in b.decode("mbcs", errors="replace").splitlines() if l.strip() and not re.match(r"^\s*(Date|Time|.*Timestamp)\s*=", l)]


def verify():
    s = session()
    export(s, OUT / "after_daqmx.nce", b"daqmx")
    a, b = norm((OUT / "backup_daqmx.nce").read_bytes()), norm((OUT / "after_daqmx.nce").read_bytes())
    import difflib
    d = [l for l in difflib.unified_diff(a, b, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    dump_json(OUT / "verify.json", {"diff": d})
    say("diff lines vs backup:", len(d), d[:40])
    say("devices:", [(x["name"], x["product"], x["simulated"]) for x in daqmx_devices()["devices"]])


def snapshot(tag):
    s = session()
    export(s, OUT / f"snap_{tag}.nce", b"daqmx")
    b = (OUT / f"snap_{tag}.nce").read_bytes()
    i = b.find(b"[DAQmx]"); j = b.rfind(b"BusType")
    say(b[i:b.find(b"\n", j) + 1].decode("mbcs", errors="replace"))


def main():
    a = sys.argv[1:]
    {"backup": lambda: backup(), "create": lambda: create(a[1], a[2]), "delete": lambda: delete(a[1]),
     "verify": lambda: verify(), "snapshot": lambda: snapshot(a[1])}[a[0]]()
    say("SIMDEV CFG DONE")


if __name__ == "__main__":
    main_guard(main)
