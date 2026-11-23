# To verify and to extend

Items that need to be verified or completed on the LabVIEW machine. Prerequisites as in `AGENTS.md` "Bottom lines": the user agrees in the current conversation before LabVIEW is operated; only VIs the agent newly created under `write2\` are run; no AO/DO writes.

## To verify

| Item | Current state | How to verify |
|---|---|---|
| LabVIEW machine pipeline | Loading, caching, argument checks, hardware I/O scanning of `lab/vi_job.py` and `lab/jobguard.py` tested with the fake LabVIEW (`tests/test_pipeline.py`) | After changing `lab/*.py`, on the LabVIEW machine first run the `failtest` job (broken as expected, exit code 1), then `gallery` (should be 12/12), with `scripts/vi_job.sh <job>` or `python -m jacq.run <job>` |
| Same-machine mode | `python -m jacq.run <job>` tested with the fake LabVIEW, never run on real Windows + LabVIEW; `readers/outline_vi.py` never run on Windows either | Open a terminal directly on the LabVIEW machine: run `python -m jacq.run failtest` and `python readers\outline_vi.py <some VI under write2> --public --out <somewhere>` once each |
| Read-only export and simulated-device scripts | `lab/lab_dump_corpus.py`, `lab/simdev_cfg.py`, `lab/acq_sim4ch.py` have rarely been run on-site | Watch the output the first time, try on a small scale first: `state_check` → `lab_dump_corpus` with `--only` exporting one or two small VIs → `simdev_cfg backup` + `verify` (read-only) → run `acq_sim4ch` only after the user has created the simulated device by hand in NI MAX |
| Hardware scan lists | The function denylist in `lab/jobguard.py` is written with English palette names, the names were not checked one by one in LabVIEW; `VILIB_ROOT` only accepts the default LabVIEW installation directory | Probe a few names from the list with `lab/probe_terms.py` to confirm the spelling |
| Non-ASCII blind spots | Chinese enum items, string constants in Chinese or outside the code page, Chinese VI file names (lint gives W `JOB-NAME?`), subVI paths containing Chinese: lint stays silent on all of them; "Chinese constants survive" is an inference | Write one `BUILD_ONLY` probe job to test them all at once, and add lint rules according to the `jacq.oracle` comparison |
| On-site coverage of control `default` | Offline there's lint `WRITER` and `test_writer_keeps_style_and_default`; no existing job has a control that relies only on `default` without being set via inputs | When writing a new job, leave one control that relies only on `default`, and judge it in check |

## To extend

| Item | Current state | Approach |
|---|---|---|
| `wire_diff` supports `invoke_nodes` | Not supported: lint gives W `WIRE-UNSUP`, `job_check` doesn't record verified | Take an on-site scan dump containing invoke nodes to fix the representation |
| `wire_diff` sees wrong wiring between two nodes of the same function | case / event frames are supported; swapping nodes of the same kind can't be seen | Match spec nodes to dump uids by creation order (the writer builds in spec order with increasing uids; this assumption needs to be verified with an on-site dump) |
| Narrow the runnable directories | `RUN_DIRS` in `lab/lablib.py` lets through the whole `write2\` | Narrow it to specific directories like `write2\jobs`; `lab_ground_truth.py` and `run_upstream_tests.py` build and run VIs directly under `write2\`, they must be changed together and verified on the LabVIEW machine |
| Shorten each round | LabVIEW cold start about 9 s; `scripts/lab_run.sh` waits a fixed 8 s at the end | Change the fixed wait to polling; or build a resident worker (in remote mode this must be considered together with "connect over SSH only when the user is present") |
| Add a `contexthelp` layer to the terminal-name catalog | Unverified names are only split into confirmed / learned | Parse LabVIEW's own `lvstring.rsc`, subdividing "unverified" into "LabVIEW has this name" and "doesn't", and add tests |
| Shift registers through event structures | The writer can't do it: neither the shift register binding inside event frames nor the XML form of output tunnels succeeded | Not doing it; for buttons changing state use front-panel controls + property nodes inside the frames (`docs/spec_reference.md` "Button events change state") |
