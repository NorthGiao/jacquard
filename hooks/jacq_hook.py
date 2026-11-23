"""Claude Code hook (registered in the workspace's .claude/settings.json; only acts on files in the Jacquard repo, exits immediately for other paths).

PostToolUse(Write|Edit)  jobs/<job>.py changed → run `jacq.lint --offline`:
    with E → exit 2, stderr goes to Claude (each with a fix); otherwise → additionalContext returns a one-line result (including W/I; also returned when clean, so nobody thinks the hook didn't fire).
PreToolUse(Write|Edit)   refuses manual edits to data files maintained by tools (catalog/terminals_confirmed.json, jobs/verified.json):
    they may only be written by scripts/probe_terms.sh / jacq.job_check; manual edits would make lint and "verified" meaningless.
Doesn't rewrite or hide any tool output (only appends feedback). Self-test: pytest tests/test_hook.py
"""
import hashlib
import json
import os
import time
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROTECTED = {REPO / "catalog" / "terminals_confirmed.json": "scripts/probe_terms.sh (read back on-site) or `python -m jacq.catalog queue add` (queue offline)",
             REPO / "jobs" / "verified.json": "written automatically by jacq.job_check after scripts/vi_job.sh runs through on-site"}


def protected(p):
    """Whether p is one of the files in PROTECTED: compare paths first, then by file system (os.path.samefile) -- on case-insensitive file systems (macOS, Windows,
    some mounted volumes) `Jobs/VERIFIED.json` resolves to a path written differently from here, yet is the same file; hard links likewise. Returns that file's path, or None if not."""
    for q in PROTECTED:
        if p == q:
            return q
        try:
            if p.exists() and os.path.samefile(p, q):
                return q
        except FileNotFoundError:
            pass   # q was deleted: not the same file as it (the same-spelling case was already compared above)
        except OSError:
            return q   # can't compare (permissions etc.): treat as protected -- this provides the grounds for refusing manual edits, and refusing on error is safer than allowing
    return None


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    fp = (ev.get("tool_input") or {}).get("file_path") or ""
    if not fp:
        return 0
    p = Path(fp).resolve()
    if ev.get("hook_event_name") == "PreToolUse":   # before the "is it in the repo" test: hard links and differently-cased spellings may resolve outside REPO
        q = protected(p)
        if q:
            print(f"Refusing manual edit of {q.relative_to(REPO)}: this file is only written by tools -- {PROTECTED[q]}.", file=sys.stderr)
            return 2
        return 0
    try:
        rel = p.relative_to(REPO)
    except ValueError:
        return 0
    if ev.get("hook_event_name") != "PostToolUse":
        return 0
    if len(rel.parts) != 2 or rel.parts[0] != "jobs" or p.suffix != ".py" or p.name.startswith("_"):   # directly under jobs/<job>.py; things like jobs/old/x.py are not jobs, linting it would only lint another job of the same name
        return 0
    py = os.environ.get("JACQ_PY") or sys.executable
    r = subprocess.run([py, "-m", "jacq.lint", str(p), "--offline"], cwd=REPO, capture_output=True, text=True, timeout=60)
    out = (r.stdout + r.stderr).strip()
    # several consecutive Edits each trigger the hook once: include "which version of the file", so the previous version's E and the next version's 0 E don't look contradictory
    try:
        ver = f"file version {hashlib.sha1(p.read_bytes()).hexdigest()[:8]}, {time.strftime('%H:%M:%S')}"
    except OSError:
        ver = time.strftime("%H:%M:%S")
    if r.returncode:
        print(f"[jacq lint --offline] {rel} ({ver}) has E (vi_job.sh will block it from the LabVIEW machine):\n{out}", file=sys.stderr)
        return 2
    keep = ("W [", "I [", "Fix:", "==")
    ws = "\n".join(l for l in out.splitlines() if l.lstrip().startswith(keep))
    head = "0 E, with W/I" if ("W [" in out or "I [" in out) else "0 E 0 W (lint clean; does not mean the VI is correct)"
    # also return a line when clean, so the agent knows the hook fired
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": f"[jacq lint --offline] {rel} ({ver}): {head}\n{ws[:3000]}"}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
