# Sourced by the other scripts. The LabVIEW machine's host name/paths for remote mode are set here; local values go in private/lab.env (not in the repo).
# REMOTE_BASE has a default in jacq/config.py (DEFAULT_BASE) and in lab/lablib.py as well; all three must agree (checked by tests/test_pipeline.py). Example:
#   LAB_HOST=lab-windows
#   LAB_PY='C:\Users\<LAB_USER>\AppData\Local\Programs\Python\Python313\python.exe'
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$REPO/private/lab.env" ] && . "$REPO/private/lab.env"
LAB_HOST="${LAB_HOST:-lab-windows}"
LAB_PY="${LAB_PY:?first write LAB_PY=<path of python.exe on the LabVIEW machine> in private/lab.env}"
PY="${PY:-python3}"
WRITER="$REPO/writer/labview_mcp"
REMOTE_BASE='D:\agent_sandbox\lv_agentic'
REMOTE_BASE_FWD='D:/agent_sandbox/lv_agentic'
REMOTE_SHLIM_FWD='D:/agent_sandbox/shlim_pkg/labview_mcp'
REMOTE_SHLIM='D:\agent_sandbox\shlim_pkg\labview_mcp'
LAB_CORPUS="$REPO/private/lab_corpus"
SSH=(ssh -o BatchMode=yes "$LAB_HOST")
