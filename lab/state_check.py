"""Read-only: list DAQmx devices + leftover processes."""
import subprocess
from lablib import daqmx_devices, say
say("devices:", [(d["name"], d["product"], d["simulated"]) for d in daqmx_devices().get("devices", [])])
out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="replace").stdout
say("procs:", [l.split()[0] for l in out.splitlines() if l.lower().startswith(("labview", "python"))])
say("STATE DONE")
