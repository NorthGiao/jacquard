"""Same-machine mode: run a whole job with one command (agent and LabVIEW on the same Windows machine):
strict lint (no LabVIEW run with any E) → lab/vi_job.py → copy results to private/lab_corpus/write2/jobs/<job>/ → jacq.job_check.
Remote mode (over SSH) uses scripts/vi_job.sh, with the same steps.

Usage: python -m jacq.run <job> [--force] [--only name1,name2] [--dest <result dir>]
When --dest is under private/lab_corpus/ it may only be this job's own result directory (another job's would get files mixed in); --only may be given only once, separate multiple names with commas.
Environment variables: LVAGENT_BASE / LVAGENT_SANDBOX (sandbox, see skill labview-lab-session); when LVAGENT_SHLIM is not set the repo's writer/ is used.
LVAGENT_FAKE=1 is a dry run that doesn't touch LabVIEW (for checking the pipeline): --dest is required, results may not go into private/lab_corpus/, and job_check won't treat it as an on-site result.
Exit codes: 1 = lint has E or on-site failure; 2 = didn't finish / no verdict possible (verified.json is then unchanged); otherwise same as jacq.job_check.
lab/vi_job.py ending with exit code 4 = LabVIEW is hung (most likely an invisible dialog): that LabVIEW instance must be closed and reopened to continue. In same-machine mode it may be
the LabVIEW the user has open; this command doesn't kill the process, it only reports it -- tell the user, and rerun after the user has dealt with it.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import job_check, lint
from .config import DEFAULT_BASE, JOB_REMOTE_DIR, LAB_CORPUS, REPO, job_local_dir, under


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    dest = None
    if "--dest" in a:
        i = a.index("--dest")
        if i + 1 >= len(a) or not a[i + 1].strip() or a[i + 1].startswith("--"):   # missing / empty value (an empty string resolves to the current directory and results get copied into the repo root) / another option
            print("==== --dest must be followed by a result directory, e.g. --dest <dir>\n==== not run on the LabVIEW machine")
            return 2
        dest = Path(a[i + 1]).resolve()
        del a[i:i + 2]
    if not a or a[0].startswith("--"):
        print(__doc__)
        return 2
    try:
        job = lint.job_name(a[0])   # same rule as lint: also accepts jobs/<job>.py; the LabVIEW machine only accepts job names
    except lint.JobArgError as e:
        print(f"==== {e}\n==== not run on the LabVIEW machine")
        return 1
    msg = lint.check_job_args(a[0], a[1:])   # the same code as lab/vi_job.py and scripts/vi_job.sh: misspelled options and --only names not in VIS are reported here, without waiting for the LabVIEW machine to reject
    if msg:
        print(f"==== {msg}\n==== not run on the LabVIEW machine")
        return 2
    fake = os.environ.get("LVAGENT_FAKE") == "1"
    in_corpus = dest is not None and under(dest, LAB_CORPUS)
    if fake and (dest is None or in_corpus):
        print("==== an LVAGENT_FAKE=1 dry run requires --dest <dir>, not under private/lab_corpus/ (only real on-site results go there)")
        return 2
    own = job_local_dir(job)
    if in_corpus and not (dest == own.resolve() or (dest.exists() and own.exists() and os.path.samefile(dest, own))):
        print(f"==== when --dest is under private/lab_corpus/ it may only be this job's own result directory ({job_local_dir(job)}): another job's directory would get this round's files mixed in\n==== not run on the LabVIEW machine")
        return 2
    os.environ.pop("JACQ_OFFLINE", None)   # this pass before going to the LabVIEW machine must be strict lint, unaffected by an offline switch in the environment
    if lint.main([job]):
        print("==== lint has errors, not run on the LabVIEW machine (fix each E above according to its \"Fix\", then come back)")
        return 1
    t0 = time.time()
    env = dict(os.environ)
    env.setdefault("LVAGENT_SHLIM", str(REPO / "writer"))
    env["PYTHONPATH"] = os.pathsep.join([str(REPO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(REPO / "lab" / "vi_job.py"), job, *a[1:]], cwd=REPO, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout, end="")
    if r.stderr.strip():
        print("---- stderr\n" + r.stderr[-3000:])
    src = Path(env.get("LVAGENT_BASE", DEFAULT_BASE)) / JOB_REMOTE_DIR / job
    dest = dest or job_local_dir(job)
    done = "JOB COMPLETE" in r.stdout
    if not done:
        print(f"==== lab/vi_job.py didn't finish (exit code {r.returncode}: timeout / LabVIEW hung / script error, see above)")
        if r.returncode == 4:
            print("     exit code 4 = LabVIEW hung (most likely an invisible dialog). This command doesn't close LabVIEW: ask the user to close that LabVIEW instance and rerun")
    rp = src / "job_report.json"
    if not rp.exists() or rp.stat().st_mtime < t0 - 2:   # this round wrote no report (failed before starting): any report in the sandbox is from the previous round and isn't used for judging
        print("==== this round produced no job_report.json: not copying back, no verdict")
        return 2
    shutil.copytree(src, dest, dirs_exist_ok=True)   # copy back even if it didn't finish: the report has complete=false, and job_check will say truthfully how far it got
    rc = job_check.main(job, dest)
    return rc if done else (rc or 2)


if __name__ == "__main__":
    sys.exit(main())
