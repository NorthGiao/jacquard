# Why it's built this way (harness design notes)

Goal: let the agent **write the VI right the first time**, and keep the number of "LabVIEW machine rounds" to a minimum. Every LabVIEW machine round means operating LabVIEW and needs the user's consent, so every error that can be caught locally must be caught locally, and when caught, tell the agent directly how to fix it.

## Practices borrowed and where they land in this repo

| Practice | Source | Where it lands |
|---|---|---|
| Rules written as **mechanical checks**, not as explanatory text; the error message itself is the fix | OpenAI, "Harness engineering" (2026) | `jacq/lint.py`: each E carries a "Fix", misspellings get the closest name; each pitfall in `docs/pitfalls.md` maps to one code |
| AGENTS.md is a **map**, not an encyclopedia; details go in docs/, read as needed | Same as above | `AGENTS.md` points to `docs/spec_reference.md`, `docs/pitfalls.md`, `jobs/gallery.py` |
| Record "passing/not passing" in JSON, **flip to passing only after real verification**, the agent can't edit it by hand | Anthropic, "Effective harnesses for long-running agents" ("edit this file only by changing the status of a passes field"; "Only mark features as 'passing' after careful testing") | `jobs/verified.json` is only written by `jacq.job_check` after everything passes on-site (stores the spec hash, invalid as soon as the spec changes); a PreToolUse hook refuses manual edits to it and to `terminals_confirmed.json` |
| **End-to-end testing** like a user | Same as above ("all testing as a human user would") | The job's `runs.actions` (set/click/wait/read operating the front panel) + `runsnap` (screenshot of the front panel after running) |
| Do one thing at a time, run a basic end-to-end check at the start of each session | Same as above | `vi_job.sh` / `jacq.run` one job per round; `--only` does part of it; the `gallery` job as a regression battery |
| A loop of generate → compile/syntax check → formal/behavioral verification → feed errors back to the model | LLM4PLC (Fakih et al., ICSE-SEIP 2024, arXiv 2401.05443: ST code through MATIEC syntax check + nuXmv model checking, failure messages fed back) | Three layers: local `gen.build` + lint (≈ syntax/types) → on-site `exec_state` (LabVIEW compilation) → `vi_test` / `runs.check` (behavior) + `wire_diff` (structure); each layer fails fast on failure and writes the reason into the report |
| Run a linter automatically after edits, errors returned to the agent via exit 2 | Claude Code hooks docs (PostToolUse: exit 2 → stderr returned to the agent; hooks also fire on subagents' tool calls) | `hooks/jacq_hook.py`, registered in `.claude/settings.json`; only looks at files in this repo, exits immediately for other paths; **only appends feedback, never rewrites or hides any output** |
| Prevent the agent from changing the check itself to pass it | Common practice with hooks | Write protection on data files (previous row); lint rule changes must come with a `tests/test_lint_pitfalls.py` case |

## What this project adds itself

- **Offline mode + probe queue**: when LabVIEW can't be operated, "unverified terminal names" can't be treated as errors that block everything, nor left to guesswork. `lint --offline` downgrades them to W and writes them into `catalog/probe_queue.json`; obvious misspellings are still E. On-site `scripts/probe_terms.sh --queue` probes them all at once, writes the results back to the catalog and removes them from the queue; wrongly guessed names in the queue get called out.
- **Layered terminal-name catalog** (confirmed / learned / seen): `python -m jacq.catalog terms|search`. Names only come from LabVIEW itself (read back on-site or from dumps), never from model memory.
- **gallery = form examples + regression battery**: the same specs are examples for the agent to copy, on-site regression tests, and also used to find lint false alarms (e.g. `element[k]` of growable functions, controls used only by event branches).
- **Equivalence gatekeeping**: the writer is vendored shlim-t + patches; `tests/test_repo_hygiene.py` checks `upstream + patches == writer/labview_mcp`, and scans for content that shouldn't be in the repo (IPs, home directory paths, `.vi/.dmp/.pdf`, >1 MB).

## Not done

- A local spec interpreter ("running" specs offline): feasible for a dataflow subset, but events, timing and UI can't be done.
- A resident LabVIEW worker (saving the LabVIEW startup each round): see `docs/lab_pending.md`.
