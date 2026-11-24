# Jacquard — notes for agents

Describe LabVIEW VIs with Python dicts (specs); once lint passes, hand them to the patched shlim-t writer on a Windows computer with LabVIEW 2024 Q1 64-bit installed (hereafter the LabVIEW machine), which builds them via VI Scripting, then tests them automatically, takes screenshots and checks the wires one by one. The human-readable introduction is in `README.md`; this page is the operating guide for agents that write VIs.

There are two ways to reach the LabVIEW machine: the agent runs on that Windows machine itself (same machine), or the agent operates it over SSH from another computer (remote). Below, "on the LabVIEW machine" means actually operating LabVIEW (build, test, run, export), whichever way is used; the rules and the commands for both ways are in the skill `labview-lab-session`.

Writing specs, lint, looking things up in the catalog, reading VIs and running the tests are all pure Python, they run anywhere and don't need LabVIEW. All commands are run from the repo root, and `$PY` is the local Python. The `scripts/*.sh` below are the entry points of the remote mode; on the same machine use `$PY -m jacq.run <job>` (the same round), the other corresponding commands are in the skill.

## Common commands

```bash
$PY -m jacq.catalog search <word>            # find function names
$PY -m jacq.catalog terms "<function>"       # full set of terminals + trust tier (confirmed / learned)
$PY -m jacq.lint <job> --offline             # when LabVIEW can't be operated; when it can, drop --offline (strict mode)
scripts/vi_job.sh <job> [--force] [--only a,b]   # one round on the LabVIEW machine (remote mode: needs SSH, user present)
$PY -m jacq.run <job> [--force] [--only a,b]      # one round on the LabVIEW machine (same-machine mode)
$PY lab/production.py <job> [--plan] [--only a,b]   # on the LabVIEW machine (same machine): long unattended runs of a verified job's PRODUCTION steps, resumable
$PY -m jacq.job_check --status <job>         # whether each VI currently counts as verified, the kind of evidence, whether the job file / writer changed since
scripts/probe_terms.sh --queue              # on the LabVIEW machine: probe all the terminal names accumulated offline for verification at once (remote mode)
$PY -m jacq.oracle <job>                     # lint's prediction × on-site outcome (called automatically for BUILD_ONLY batches)
$PY -m pytest                               # fully offline, about 1 s
$PY -m pytest tests/test_typecheck.py::test_case_value_checks   # a single test
```

## Writing a VI

1. `cp jobs/_template.py jobs/<job>.py`, replacing `<job>` with the file name. Copy the structure from the closest one in `jobs/gallery.py` (all 12 forms verified on-site); for subVI layering see `mcpi.py`. Quick reference of spec keys: `docs/spec_reference.md`.
2. Look up function names and terminal names with `jacq.catalog`. Get one character of a name wrong and the build fails on-site, or the wire is silently dropped.
3. Write the expected values of tests as the return values of a Python reference implementation in the same file (`ref_*`), don't compute them by hand. For VIs with loops or a UI use `runs` + `actions` + `check`; check must cover every behavior of the task, and its return value must have a boolean `"pass"` (without it, it counts as not passed).
4. After changing `jobs/*.py` the hook automatically runs `lint --offline`, and any E is returned to you, each with a "Fix". With an E, don't go to the LabVIEW machine. Also take a look at `TYPE?` / `REQ?` / `CASE-*?` among the W's before going to the LabVIEW machine. What the codes mean: `docs/pitfalls.md`.
5. One round on the LabVIEW machine. Before touching LabVIEW the user must agree in the current conversation (see "Bottom lines"). Remote mode `scripts/vi_job.sh <job>`; same-machine mode `$PY -m jacq.run <job>` (environment variables in the skill `labview-lab-session`). This command does lint → (remote) sync the writer → build in dependency order (rebuild everything if the writer changed) → check for broken → tests → runs → screenshots → dump → pull back/copy back → wire check. It stops at the first failing step, and the report has the original error text. Only if everything passes is it recorded in `jobs/verified.json`; exit code 2 = no verdict possible (didn't finish, only partly ran, the report isn't from this job, the wire check wasn't done), the last `====` line of the output gives the reason.
6. Deliver to the user: the VI path (`D:\agent_sandbox\lv_agentic\write2\jobs\<job>\`), subVI names → their roles, tests/check results, the wire check, screenshot paths (`private/lab_corpus/write2/jobs/<job>/snap*/`). Ask the user to take a look at the front-panel layout in the screenshots.

lint passing only means "very likely to build"; whether the logic is right can only be judged by tests/check on-site. Only VIs in `jobs/verified.json` whose spec hash matches count as verified; `evidence.kind` in the record says which level verification reached (`tests` / `runs+check` / `runs(no assertion)` / `build only`), and when delivering, say exactly that.

## Getting it right the first time: common pitfalls

lint catches most of these, but knowing them up front saves a round:

- Control terminals are outside the loop, and going into a While loop they're read only once: a stop button wired directly to `W.stop` won't stop it. Either register a frame for it in an event structure (the writer moves the terminal into the frame), or put a property node reading `Value` inside the loop (`spec_reference.md` "Where controls are read").
- Shift registers can't pass through event structures, and event structures have no tunnels either. For buttons changing state use front-panel controls + property nodes inside the frames (`spec_reference.md` "Button events change state").
- SubVI control labels can only be ASCII (the writer reports an error); top-level VIs may use Chinese labels. The caller's terminal names = the labels of the subVI's controls. Labels may not repeat within one VI (tests set and read values by label).
- Arrays leaving a For loop gain +1 dimension, entering lose −1 dimension (auto-indexing); for the whole array go through a shift register.
- Element wires of property nodes and Bundle/Unbundle By Name are only drawn in the second pass, and their other end can't be a shift register, tunnel or event data node; put an ordinary function in between.
- Multiple tests cases of the same VI run in turn without resetting controls: each case writes all inputs.
- Newlines in string constants haven't been verified on-site; to compare by bytes use a `u8` constant 10.
- The connector has 12 slots; with more than 12 controls + indicators, write `"connector": false` for the extra ones; the error cluster takes left3/right3 by default.
- The block diagram needs at least one function, constant, subVI or structure; just controls wired directly to indicators can't be built.
- Boolean button text and LED colors can't be set (known limitation).

## Architecture: what you'd only see by reading several files

- **Two machines, one spec.** `jobs/<job>.py` is loaded locally by `jacq.lint.load_job` (with `from lablib import BASE` swapped for a fake lablib), and on the LabVIEW machine by `lab/vi_job.py` (the real `lab/lablib.py`). In jobs, inputs/expect/actions always use the spec name, and `vi_job` automatically swaps in the front-panel label.
- **lint calls the writer's `gen.build` directly** (`writer/labview_mcp/gen.py`), so the `GEN` reported when a spec contradicts itself is the same code as on-site; on top of that `jacq/lint.py` adds structural checks, and `jacq/typecheck.py` infers types along the wires (category + dimensions). A spec with the wrong shape (something that should be a list written as something else, missing keys) reports `SHAPE` and doesn't crash.
- **`lab/jobguard.py` is a single file shared by both ends** (standard library only; the LabVIEW machine imports it directly, locally `jacq/config.py` loads it by path): `spec_hash` (the key shared by reports, dumps, `verified.json`; the algorithm must not change; for VIs that call subVIs of this job, the LabVIEW machine's cache stores `vi_job.cache_key`, which has the subVIs' cache values appended), file hashes, hardware I/O scanning. lint's `HW-IO` / `JOB-OUT` / `JOB-NAME` and the refusals of `lab/vi_job.py` before it starts work use the same code and the same output directory computed from the job name (`jobguard.job_out(BASE, job)`; the `OUT` written in the job file is only checked for equality): subVIs are a whitelist (VIs under this output directory, or VIs registered in `VILIB_ALLOW` of `lab/jobguard.py` located under vi.lib of the default LabVIEW installation directory); functions are a denylist scanned by palette-name prefix (hardware I/O: VISA / GPIB / Serial / DAQmx...; network: TCP / UDP / HTTP...; external code: System Exec / Call Library Function / Python Node...; dynamically calling other VIs: Open VI Reference / Call By Reference...); VI Server property / invoke nodes using `"class"` (such as Run VI of the VI class) are not allowed. To let a job call a new vi.lib VI: currently no tool can register its terminals (`subvis` / `vilib_paths` in `catalog/terminals_confirmed.json` are hook-protected, `probe_terms` only probes functions), so this is the user's business -- list it for the user and ask the user to decide; the agent must not work around the hook by itself.
- **E's predicting "will be broken / won't build" are only given where there's on-site evidence** (the type-related predictions are `lint.PREDICTIVE`: `TYPE`, `DIMS`, `AUTOIDX`, `REQ`, `CASE-DUP`, `CASE-DEFAULT`, `CASE-VAL`, given only for cases confirmed on-site in `typecheck.CONFIRMED` / `REQ_CONFIRMED`. E's not in `PREDICTIVE` have two origins: the tool's own constraints, bottom lines or structural checks, such as `SHAPE`, `HW-IO`, `JOB-OUT`, `JOB-NAME`, `JOB-ARG`, `JOB-SUBVI`, `LABEL-DUP`, `EXPECT-BAD`, `IMPORT`; and pitfalls hit on-site, such as `FOR-N`, `WHILE-STOP`, `TERM`, where the "Symptom (on-site)" column of `docs/pitfalls.md` gives the symptom seen on-site. The full table is in `docs/pitfalls.md`; a new E must either have an on-site symptom to write in the table or be explained as a constraint of the tool itself, and if neither can be written, it gets W). The sources of `typecheck.CONFIRMED` and `REQ_CONFIRMED` are written in the header of `typecheck.py`, from the on-site batches of `private/lab_corpus/probes/` and `jobs/typeprobe.py`; without evidence it's W, and functions without type rules get I `TYPE-UNK`. The procedure for changing rules: write build-only probes (`BUILD_ONLY = True` in the job) → LabVIEW machine → `jacq.oracle` gives the rules to upgrade/downgrade/add (`docs/lint_robustness.md`). `LINT_EXPECT = {VI: [codes]}` in a job downgrades expected E's to I, used to deliberately rehearse errors on the LabVIEW machine (`failtest.py`); it only works for predictive codes (`lint.PREDICTIVE`), and jobs with it aren't recorded as verified.
- **Layered terminal-name catalog** (`jacq/catalog.py`): confirmed (`catalog/terminals_confirmed.json`, wired successfully on-site) > learned (`catalog_public.json`, learned from on-site dumps) > seen (private). Offline lint downgrades names not in confirmed to W and writes them into `catalog/probe_queue.json`, to be verified all at once on the next LabVIEW machine visit (remote mode `probe_terms.sh --queue`, the equivalent command for the same machine is in the skill). Names no longer used by any job are automatically withdrawn from the queue at the next offline lint; those that can't be read in two on-site probes are moved to `rejected`; manual withdrawal with `jacq.catalog queue remove`.
- **Where LabVIEW machine results flow**: the LabVIEW machine writes `job_report.json` + dumps + screenshots → in remote mode pulled by `scripts/pull.sh`, in same-machine mode copied by `jacq.run`, to `private/lab_corpus/write2/jobs/<job>/` → `jacq/job_check.py` summarizes, `jacq/wire_diff.py` compares the spec's wires with the dump's wires one by one → only if everything passes is `jobs/verified.json` written (recording each VI's spec hash, evidence kind, hashes of the job file and the writer). `job_report.json` carries the job file hash and each VI's spec hash, and `job_check` first confirms the report was produced by the current job before judging; a missing dump or a form the wire check doesn't support (lint `WIRE-UNSUP`) isn't recorded. The LabVIEW machine reuses unchanged VIs according to `.spec_hash.json` (each VI records its own spec hash + the cache values of the subVIs of this job it calls; if a subVI was rebuilt with a changed spec and the caller wasn't reached that round, the caller is rebuilt in the next round too; the writer hash is also recorded, and if the writer changed everything is rebuilt; a broken file is treated as no cache), `--force` rebuilds everything.
- **Writer = upstream + patches**: `writer/upstream/` is `gen.py`/`lv.py` before patching, `writer/patches/*.diff` are the patches, `writer/labview_mcp/` is the patched result; `tests/test_repo_hygiene.py` checks the three are consistent.
- **hook** (`hooks/jacq_hook.py`, registered in `.claude/settings.json`): PostToolUse lints a job when it's changed; PreToolUse refuses manual edits to `catalog/terminals_confirmed.json`, `jobs/verified.json` (only written by `probe_terms.sh` / `job_check`).
- **Reading existing VIs** takes another path: `readers/outline_vi.py` does read-only extraction with pylabview (`-x`, latin-1) + `readers/offline/vi_outline.py` produces an outline (without wires); wires come from on-site scan dumps, read by `readers/vi_trace.py` / `vi_diff.py`; `readers/offline/offline_batch.py` pairs the two by node uid to learn the primitive mapping table. Only extract, don't repack VIs with pylabview (the encoding/decoding is lossy and will corrupt them). How to explain, map data and work on the LabVIEW machine is in the corresponding skills in `skills/`.

## Bottom lines

Blocked by code: only VIs the agent newly created under `write2\` are run, existing VIs are only dumped, not run (`lab/lablib.py` `must_runnable`, lint `JOB-OUT`); a job calling subVIs outside the whitelist, hardware I/O / network / external code / dynamic-call functions on the list, VI Server class nodes (`lab/jobguard.py`: lint `HW-IO` + refusal by `vi_job.py` before it starts work; the function part is a denylist scanned by name, hardware functions not on the list slip through); terminal names only come from LabVIEW (lint `TERM` / `UNVERIFIED`); data files maintained by tools are not edited by hand (hook).

Not blocked by code, you must keep these yourself:

- VIs the user says must not be touched are not written, changed, run or replicated; read-only parsing is fine.
- No AO/DO writes. (On the job path `HW-IO` blocks them; when writing LabVIEW-machine scripts directly or calling the writer by hand, nothing does.)
- Before operating LabVIEW (building VIs, running VIs, exporting VIs, querying DAQmx, changing NI MAX), the user must say in the current conversation that it's OK now; what was said in other conversations doesn't count. Over SSH the user must also be present, and when they say it's off, stop; on the same machine, don't touch LabVIEW instances and VIs the user already has open.
- When touching DAQmx, only read AI, only use simulated devices, and only with the user's explicit consent (the default scope is pure-computation VIs under `write2\`).
- The user's VIs, dumps and acquired data go only in `private/` (gitignored); in the conversation give only summaries.
- Files in the sandbox (`LVAGENT_BASE`) are deleted by the user themselves.

## When changing this repo

- A newly hit pitfall: if it can be written as a check, add it to `jacq/lint.py`, add a case to `tests/test_lint_pitfalls.py`, and add a row to `docs/pitfalls.md`; only what can only be remembered by people goes just in the docs.
- After changing `writer/labview_mcp/{gen,lv}.py`: run `scripts/regen_patch.sh`.
- `tests/test_jobs.py` requires every job in `jobs/` to lint with 0 E; `tests/test_lint_regression.py` requires the verified jobs and the 28 upstream on-site cases to have 0 E -- after adding a rule, run these two first.
- The master copy of a skill is `skills/<name>/SKILL.md`; when installed in `.claude/skills/<name>/` of the parent directory, copy it over after changes (the tests compare them; the sync command is in `skills/labview-repo-maintain/SKILL.md`).

## Where to find things

| What | Where |
|---|---|
| spec keys, structures, events, state machines, connectors, how tests compare | `docs/spec_reference.md`, `jobs/gallery.py` |
| Meaning of lint codes and their fixes | `docs/pitfalls.md` |
| How far lint can predict, measurements, blind spots, how to calibrate rules | `docs/lint_robustness.md` |
| To verify and to extend | `docs/lab_pending.md` |
| Why it's designed this way | `docs/harness_design.md` |
| Writer patches, upstream | `writer/UPSTREAM.md` |
| LabVIEW machine scripts | `lab/` |
