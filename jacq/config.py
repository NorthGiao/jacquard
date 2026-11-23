"""Path configuration of the local tools (jacq/). The shell scripts for remote mode have another copy of the defaults in scripts/_env.sh (REMOTE_BASE etc.),
and the LabVIEW machine side has one in lab/lablib.py (reads the environment variables of the same name); the defaults in all three places must agree, checked by tests/test_pipeline.py.
Personal host configuration goes in private/lab.env (not in the repo)."""
import importlib.util
import os
from pathlib import Path, PureWindowsPath

REPO = Path(__file__).resolve().parent.parent
WRITER_SRC = REPO / "writer"                 # for import labview_mcp (patched shlim-t)
CATALOG = REPO / "catalog"
JOBS = REPO / "jobs"
PRIVATE = REPO / "private"                   # gitignored: the user's VIs, dumps, pulled-back results
LAB_CORPUS = PRIVATE / "lab_corpus"
OFFLINE = PRIVATE / "offline"
DEFAULT_BASE = r"D:\agent_sandbox\lv_agentic"
REMOTE_BASE = PureWindowsPath(os.environ.get("LVAGENT_BASE", DEFAULT_BASE))
JOB_REMOTE_DIR = "write2/jobs"               # job output = REMOTE_BASE\write2\jobs\<job>; pulled back locally to LAB_CORPUS/write2/jobs/<job>


def job_local_dir(job: str) -> Path:
    return LAB_CORPUS / JOB_REMOTE_DIR / job


def under(p: Path, root: Path) -> bool:
    """Is p root itself or under root? Decided by the file system (os.path.samefile), not by comparing how paths are written: on case-insensitive file systems (Windows, macOS,
    some mounted volumes) `private/LAB_CORPUS` and `private/lab_corpus` are the same directory, and resolve() does not normalize them to the same spelling.
    When p doesn't exist yet, look at its nearest existing ancestor; when root doesn't exist yet (a fresh clone has no private/lab_corpus) there is nothing to samefile, fall back to comparing paths component by component,
    ignoring case -- better to block too much. If it can't be compared (permissions etc.) it also counts as "inside": this function provides the grounds for rejecting --dest, and rejecting on error is safer than allowing."""
    if not root.exists():
        rp = [x.casefold() for x in root.resolve().parts]
        return [x.casefold() for x in p.resolve().parts][:len(rp)] == rp
    for q in (p, *p.parents):
        try:
            if q.exists() and os.path.samefile(q, root):
                return True
        except OSError:
            return True
    return False


def _load_guard():
    """lab/jobguard.py (hashes + hardware I/O scan): imported directly on the LabVIEW machine, loaded locally from the same file by path."""
    spec = importlib.util.spec_from_file_location("jacq_jobguard", REPO / "lab" / "jobguard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GUARD = _load_guard()


def writer_sha():
    """Hash of the local writer (writer_sha in lab/jobguard.py: labview_mcp/*.py, the same scope as what sync_writer.sh syncs; the same function on the LabVIEW machine)."""
    return GUARD.writer_sha(WRITER_SRC / "labview_mcp")
