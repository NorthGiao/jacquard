---
name: labview-lab-session
description: The rules and steps for the agent to actually operate LabVIEW: build/test/run VIs via VI Scripting, probe terminal names, export existing VIs read-only, dry-run with NI MAX simulated devices, collect results. There are two ways to reach LabVIEW -- the agent runs directly on the same Windows computer as LabVIEW, or the agent operates a Windows machine with LabVIEW installed over SSH from a Linux/Mac container. Read this before any step that touches LabVIEW; use when the user says "go to the LabVIEW machine / build it in LabVIEW / run it / export those VIs / SSH is on / run it on this computer". Not for: only reading VIs offline locally (labview-vi-explain).
---

# Operating LabVIEW

All commands are run from the repo root. Below, the Windows computer with LabVIEW installed is called the "LabVIEW machine", and the sandbox directory on it is called BASE (default `D:\agent_sandbox\lv_agentic\`, can be changed with the environment variable `LVAGENT_BASE`).

**This skill governs actions**: building VIs, running VIs, exporting VIs, querying DAQmx devices, changing the NI MAX configuration. Whether the agent does them over SSH or directly on the LabVIEW machine, the rules for these actions are the same (see "Rules"). SSH is just one way of reaching LabVIEW.

## The two ways

| | Local Windows | Remote (SSH) |
|---|---|---|
| Where the agent is | On the same Windows machine as LabVIEW | Linux/Mac container (no LabVIEW) → SSH → LabVIEW machine |
| How LabVIEW-machine scripts are run | Directly `python lab\<script>.py`; a full round of a job with `python -m jacq.run <job>` | `scripts/lab_run.sh` (bash, pushes `lab/*.py` and `jobs/` up each time before running); a full round with `scripts/vi_job.sh <job>` |
| Where the results are | Under BASE; `jacq.run` copies a job's results to `private\lab_corpus\`, other scripts' results have to be copied yourself | `scripts/pull.sh` pulls them back to `private/lab_corpus/` |
| Maturity | The `jacq.run` flow was tested with the fake LabVIEW (`LVAGENT_FAKE=1`), not yet verified on real Windows + LabVIEW; watch the output the first time | Verified on-site; after changing `lab/*.py` first run `failtest` as a smoke test |

Which one: if the agent runs in a Linux/Mac container it can only be remote; if it runs on the Windows machine with LabVIEW installed it's local. If you can't tell, ask the user.

## Before touching LabVIEW (same for both ways)

1. **The user said in this conversation that LabVIEW may be touched now** ("SSH is on", "you can run it on this computer" both count). What was said in other conversations doesn't count; when the user says stop, stop; if unsure, ask, don't try.
2. **First list for the user what this session will do**: how many jobs, which VIs will be built/run, roughly how long, whether DAQmx or NI MAX will be touched. Only proceed once the user says OK.
3. **First look at the current state, don't overwrite**: note the LabVIEW processes already open (`Get-Process LabVIEW`, in remote mode `state_check.py`), and at the end close only those you started.
4. Extra rules depending on the way:
   - **Remote**: the user must be present and SSH on; when the user says it's off, stop.
   - **Local**: on this computer the user may have LabVIEW open, with unsaved VIs. Don't touch VIs the user has open, don't close the user's LabVIEW, don't write into the user's working directories (only write BASE). LabVIEW started via COM by VI Scripting is a new instance, and VIs it runs can't pop up file dialogs (always give non-empty path parameters).

## Environment setup

- **Remote**: the host name and Python path of the LabVIEW machine are written in `private/lab.env` (read by `scripts/_env.sh`).
- **Local**: LabVIEW 2024 Q1 64-bit (ActiveX enabled under Tools ▸ Options ▸ VI Server), Python 3.13 + `pywin32`. In each terminal first set:

```powershell
$env:LVAGENT_BASE    = "D:\agent_sandbox\lv_agentic"   # without a D: drive use somewhere else, must be under LVAGENT_SANDBOX
$env:LVAGENT_SANDBOX = "D:\agent_sandbox"
$env:LVAGENT_SHLIM   = "<repo>\writer"                  # use the writer in the repo directly, no syncing needed (it's the default when jacq.run doesn't set it)
$env:PYTHONPATH      = "<repo>"                         # needed when running lab\vi_job.py directly (for troubleshooting; it doesn't run lint); jacq.run sets it itself
```

`must_runnable` / `must_sandbox` in `lab/lablib.py` only accept paths under `LVAGENT_SANDBOX` (`must_runnable` also requires being in the runnable directories of `LVAGENT_BASE`), everything else is refused.

## LabVIEW-machine scripts and completion markers

| Script | What it does | Completion marker |
|---|---|---|
| `state_check.py` | Lists DAQmx devices (including whether simulated) + leftover LabVIEW/python processes, read-only | `STATE DONE` |
| `lab_doctor.py` | Session, LabVIEW version, DAQmx, helper VIs, disk | `ALL DONE` |
| `vi_job.py <job> [--force] [--only a,b]` | Builds/tests/runs/screenshots/dumps a job (the output directory is set by the job name: `BASE\write2\jobs\<job>`). Before starting work it checks VI names (paths / drive letters not allowed), scans for hardware I/O nodes and external subVIs, and checks whether the job's `OUT` is written right; on any problem it refuses the whole job (`JOB COMPLETE REFUSED`). Normally don't run it directly: remotely use `scripts/vi_job.sh`, on the same machine `jacq.run`, which run strict lint first | `JOB COMPLETE` |
| `production.py <job> [--plan] [--only a,b]` | Long unattended runs of a verified job: the steps in the job's `PRODUCTION`, one VI call each, one result JSON per step under `BASE\write2\jobs\<job>\production\` (`production_fake\` under `LVAGENT_FAKE=1`), plus `production.log` and `heartbeat.json`. Refuses unless every VI is verified (those the steps run beyond build-only), the files on disk were built from the current spec and writer, and nothing else of the job is running (the same lock is taken by every jacq.run round, so a rebuild can't swap VIs under a production); reruns resume (a finished step is redone only if its VI as built, the step, or a step it takes from changed, or its check now fails on the stored values), a step interrupted twice stops for a person. While it runs, run no other job on that LabVIEW (one VI namespace in memory; the lock is per job). Same machine only (it runs for hours), with the user's OK like any LabVIEW run; nothing restarts it by itself (a scheduled task is the user's decision; notes in the script's docstring) | `PRODUCTION COMPLETE`, with `--only` `PRODUCTION ONLY DONE` (else `PRODUCTION STOPPED` / `PRODUCTION REFUSED`) |
| `probe_terms.py <function name>...` | Reads back the terminal names of functions | `PROBE TERMS COMPLETE` |
| `dump_one.py <path relative to BASE>` | Scan-dumps one VI in the sandbox, read-only | `DUMP1 DONE` |
| `class_props.py <class> [query]` | Looks up the property names of a class, read-only | `PROPS DONE` |
| `lab_dump_corpus.py copy …` / `dump …` | Exports existing VIs read-only (see below) | `ALL DONE` / `CORPUS COMPLETE` |
| `simdev_cfg.py …` | NI MAX simulated devices (see below) | `SIMDEV CFG DONE` |
| `acq_sim4ch.py` | Four-channel AI + storage dry run with a simulated device (needs the user's explicit consent) | `ACQ4 COMPLETE` |

If the output has no completion marker, it didn't finish (LabVIEW hung, timeout); the scripts resume from checkpoints, just rerun once.

## Common operations

**Self-check** (do this first)

```bash
scripts/lab_run.sh state_check.py 120 1 "STATE DONE"          # remote
```
```powershell
python lab\state_check.py                                       # local
```

**One round finishing a job** (build → broken check → tests → runs → screenshots → dump → wire check)

```bash
scripts/vi_job.sh <job> [--force] [--only a,b]                  # remote: lint → sync writer → LabVIEW machine → pull back → job_check, one command
```
```powershell
python -m jacq.run <job> [--force] [--only a,b]                  # local: strict lint (with an E, no LabVIEW machine run) → lab\vi_job.py → results copied to private\lab_corpus\ → job_check
```

The outcome of both commands is in the last `====` line and the exit code: 0 = passed and recorded in `jobs/verified.json` (rehearsal jobs with `LINT_EXPECT` pass but are not recorded); 1 = lint has E's, on-site failure, wire or style mismatch, or the LabVIEW machine refused this whole job (`refused` non-empty in the report: hardware I/O nodes, subVIs outside this job's directory / vi.lib, wrong OUT -- lint's HW-IO / JOB-OUT use the same criteria, so with lint 0 E it won't get this far); 2 = no verdict possible (didn't finish, `--only` ran only part, the report isn't from the current job, some VI is missing a dump or the wire check wasn't done, a result file can't be read); in that case `verified.json` isn't changed, rerun as that line says. `jacq.run` reporting "exit code 4" = LabVIEW hung (most likely an invisible dialog): in same-machine mode that may be the LabVIEW the user has open, so don't kill the process yourself; tell the user and rerun after the user has dealt with it (in remote mode `lab_run.sh` only stops the instance it started itself in session 0). After the writer changes there's no need to add `--force` by hand: the LabVIEW machine's cache records the writer hash (`labview_mcp/*.py`, the same scope as `sync_writer.sh` syncs), and when it changes everything is rebuilt automatically; `vi_classes.json` and the helper VIs in `vis/` aren't in the hash, so after changing them deploy by hand and add `--force`.

`jacq.job_check`, `jacq.oracle` and `readers/` all read results from `private/lab_corpus/`. To walk through the flow without touching LabVIEW: `LVAGENT_FAKE=1` + `--dest <a directory elsewhere>` (fake results may not go into `private/lab_corpus/`, and `job_check` won't treat them as on-site results either).

**Verifying terminal names** (the `catalog/probe_queue.json` accumulated by offline lint)

```bash
scripts/probe_terms.sh --queue                                  # remote
```
```powershell
# local: the same pipeline, and what writes back to the catalog is still only jacq.catalog record
$names = python -m jacq.catalog queue --names
python lab\probe_terms.py $names | Select-String '^TERMS ' | % { $_.Line -replace '^TERMS ','' } | python -m jacq.catalog record
```

Names that can't be probed don't stay in the queue forever: the second failed probe of the same function moves it to `rejected`, and `record` prints the jobs using it. If the function really exists (the name is right, LabVIEW just hung at the time), `python -m jacq.catalog queue remove "<function name>"` and queue it again.

## Exporting existing VIs (read-only)

Used to supply wires for **labview-vi-explain** / **labview-data-mapping**. Existing VIs (not newly created by the agent under `write2\`) are only copied and exported, **never run, never saved**.

```bash
scripts/lab_run.sh lab_dump_corpus.py 600 1 "ALL DONE" copy "<source directory on the LabVIEW machine>" <name>   # copied to BASE\lab_copy\<name>\
scripts/lab_run.sh lab_dump_corpus.py 1800 12 "CORPUS COMPLETE" dump --only <name>
scripts/pull.sh lab_dump && scripts/pull.sh lab_copy/<name>                                  # pull back in remote mode
```
```powershell
python lab\lab_dump_corpus.py copy "<source directory>" <name>               # local: the same two steps, the source directory is given by the user
python lab\lab_dump_corpus.py dump --only <name>
Copy-Item -Recurse -Force "$env:LVAGENT_BASE\lab_dump" "<repo>\private\lab_corpus\"
Copy-Item -Recurse -Force "$env:LVAGENT_BASE\lab_copy\<name>" "<repo>\private\lab_corpus\lab_copy\"
```

The source directory is given by the user; don't go looking through the user's desktop or drives yourself. VIs that hang are recorded as hung and skipped. The first time, use `--only` to export one or two small VIs, and once the flow is confirmed to work, export the whole batch.

## NI MAX simulated devices (beyond the default scope, needs the user's explicit consent)

The default scope is: only build VIs under `write2\`, only pure computation. Touching DAQmx (even just reading simulated devices) needs the user's explicit consent in this conversation.

Importing a whole package with NCE changes devices already in MAX (e.g. turning the placeholder of a real card that isn't plugged in into a simulated device), and can't be undone. Therefore:

1. First `state_check.py`. **If MAX already has a simulated device (the resident one), just use it, create nothing.**
2. Before touching the MAX configuration: `simdev_cfg.py backup`, then `simdev_cfg.py verify` (read-only).
3. When there really is no simulated device, **prefer asking the user to create it by hand in the NI MAX UI** (right-click "Devices and Interfaces" → Create New → Simulated NI-DAQmx Device), and once created confirm with `state_check.py` that it's `simulated=True`. Only when the user explicitly agrees to using a script, `simdev_cfg.py create <name> <template device>`; the template must be a simulated device.
4. Dry runs only use simulated devices (`acq_sim4ch.py` first confirms the device is simulated and that the channel string starts with its name).
5. `simdev_cfg.py delete <name>` **is only for deleting devices you created with the script during this session**. Resident simulated devices and devices created by the user by hand are not deleted; list them for the user to deal with. If MAX was touched, at the end compare with the backup using `simdev_cfg.py verify` to confirm the configuration is back as it was.

In local mode MAX is the very configuration the user is using, so backing up first matters even more.

## Rules

Listed by action, independent of the way LabVIEW is reached:

- **Running VIs**: by default only pure-computation VIs newly created by the agent under `write2\` are run. `must_runnable` also lets through `save4ch\` and `acq\` (directories used for the four-channel storage self-test and simulated-device dry runs), using them needs the user's explicit consent; everything else is refused. Existing VIs are only exported. `must_runnable` only looks at paths: **don't copy copies of existing VIs into `write2\`** (once copied in, they become "runnable").
- **VIs the user says must not be touched**: not written, changed, run or replicated; read-only export is fine.
- **Hardware**: no AO/DO writes; when touching DAQmx only read AI, only use simulated devices, and only with the user's consent. The subVIs a job can call are a whitelist (those under this job's directory, or vi.lib VIs registered in `VILIB_ALLOW` of `lab/jobguard.py`); for hardware I/O / network / external code / dynamic-call functions on the list (VISA, GPIB, serial, TCP/UDP, DAQmx, System Exec, Call Library Function, Open VI Reference...) and VI Server class nodes, lint reports `HW-IO` and `vi_job.py` refuses the whole job. The function part is a denylist scanned by name, hardware functions not on the list slip through; this gate is also only on the job pipeline, and scripts calling the writer directly must guard themselves. So "no hardware writes" is still yours to keep; don't count on this as a safety net.
- **Writing files**: only write under BASE; what's pulled back/copied back goes only in `private/` (gitignored).
- **Deleting things**: files in the sandbox are deleted by the user themselves, the agent doesn't delete them; list what needs cleaning up for the user.
- **LabVIEW processes**: only close those you started, don't touch those the user already has open.

## Wrapping up

1. Look at the processes once more: no LabVIEW/python left over from this session (compared with what was noted at the start). Remote mode `state_check.py`, local `Get-Process LabVIEW`.
2. If NI MAX was touched: `simdev_cfg.py verify` passes.
3. List for the user what this session did, the results, and the sandbox directories that can be deleted.
4. Tell the user this session is done; in remote mode remind them SSH can be turned off.
