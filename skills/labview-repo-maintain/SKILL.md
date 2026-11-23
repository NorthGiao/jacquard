---
name: labview-repo-maintain
description: Use when changing the Jacquard repo itself: adding/upgrading/downgrading rules in jacq.lint, turning a newly hit pitfall into a check, changing the writer patches, updating the terminal-name catalog, rerunning the offline pairing, changing the VI-reading tools, changing these skills. Use when the user says "add this pitfall to lint / should this rule be upgraded to E / the writer needs changing / update the catalog / change the skill". Not for: just writing a VI (labview-vi-write) or explaining a VI (labview-vi-explain).
---

# Maintaining Jacquard

All commands are run from the repo root, `$PY` is a Python with pytest installed. First read the "Architecture" and "When changing this repo" sections of `AGENTS.md`. Before big changes, back up the files you're about to touch.

## Run after every change

```bash
$PY -m pytest -q                       # fully offline, a few seconds
```

It covers: every job lints with 0 E (`test_jobs.py`), verified jobs + the 28 upstream on-site cases 0 E (`test_lint_regression.py`), upstream + patches == writer (`test_repo_hygiene.py`), the skills in the repo match the installed copies, the type probe predictions match the on-site outcomes, the on-site result → verdict → record verified stage (`test_pipeline.py`: synthetic reports + the fake LabVIEW of `LVAGENT_FAKE=1`).

**Don't run `python -m jacq.job_check <job>` directly in the repo to "give it a try"**: it rewrites `jobs/verified.json` (date, evidence fields). To try, copy the repo elsewhere and run it there, or use `--status` (read-only).

## Newly hit pitfall → check

1. If it can be written as a check: add it in `jacq/lint.py` (structure) or `jacq/typecheck.py` (types), with a code name in the existing style (short uppercase word, add `?` when unsure).
2. Add a minimal case to `tests/test_lint_pitfalls.py`: the bad spec reports this code, the fixed one doesn't.
3. Add a row to `docs/pitfalls.md`: code → meaning → fix. Every E of lint must carry a "Fix".
4. Only what can only be remembered by people goes just into `docs/pitfalls.md` and "Getting it right the first time" in `AGENTS.md`.

## Levels: an E needs on-site evidence

Predictive E's (`lint.PREDICTIVE`) are only given for cases confirmed on-site as "built like this it's broken / won't build"; without evidence it's W, without a rule it's I. E's not in `lint.PREDICTIVE` have two origins: the tool's own constraints, bottom lines or structural checks (such as `SHAPE`, `HW-IO`, `JOB-OUT`, `JOB-NAME`, `JOB-ARG`, `JOB-SUBVI`, `LABEL-DUP`, `EXPECT-BAD`, `IMPORT`), and pitfalls hit on-site (such as `FOR-N`, `WHILE-STOP`, `TERM`; the "Symptom (on-site)" column of `docs/pitfalls.md` gives the symptom seen on-site). The full table is in `docs/pitfalls.md`; a new E must either have an on-site symptom to write in the table or be explained as a constraint of the tool itself, and if neither can be written, it gets W. To upgrade a W to E (or add a new type pair):

1. Write a build-only probe batch in `jobs/` (`BUILD_ONLY = True`), each VI testing one case, with both positive and negative examples (see `jobs/typeprobe.py`).
2. Run a round on the LabVIEW machine (**labview-lab-session**); for a build-only job, `job_check` at the end of the round calls `jacq.oracle` automatically, giving the comparison of lint prediction × on-site outcome and the rules to upgrade/downgrade/add.
3. Change `CONFIRMED` / `REQ_CONFIRMED` / `PREDICTIVE` according to the comparison, and write the source clearly in the header of `typecheck.py` (which probe batch, which VIs).
4. After the change, run oracle again (offline, reading the pulled-back report); the predictions must all agree with on-site; the corresponding tests in `tests/test_typecheck.py` check this.

These probe numbers mean "no misses or false alarms in this batch", not a guarantee; say so when writing docs (the wording of `docs/lint_robustness.md`).

## `lab/jobguard.py`, shared by both ends

- Standard library only. `lab/vi_job.py` on the LabVIEW machine imports it directly, locally `jacq/config.py` loads the same file by path (`GUARD`).
- `spec_hash` is the key shared by `job_report.json`, dumps and `jobs/verified.json` (the LabVIEW machine's cache `.spec_hash.json` stores exactly it for VIs without subVIs of this job; for those with subVIs it stores the value assembled by `vi_job.cache_key`, which can't be used as a spec_hash): **the algorithm must not change**, a change invalidates all on-site records (pinned by `test_pipeline.py::test_spec_hash_is_the_field_algorithm`). To add information to records, add new fields.
- Hardware I/O scanning is a bottom line, not a "will be broken" prediction. The function denylist `DENY_FUNC`: adding names needs no on-site source, removing names needs the user's consent. The subVI whitelist `VILIB_ALLOW` (relative paths after vi.lib, lowercase): letting a job call a new vi.lib VI = adding a line here + registering its terminals in `subvis` / `vilib_paths` of `catalog/terminals_confirmed.json` (`test_pipeline.py::test_vilib_allowlist_matches_catalog` checks both sides agree). No tool writes these two keys (`probe_terms` only probes functions), and the file is hook-protected: the user has to change it themselves (or, with the user's consent, the user temporarily turns off the hook); the agent just lists the VIs to register for the user. Only pure-computation VIs are accepted; those touching files, hardware, network, the registry or external processes aren't. `VILIB_ROOT` only accepts the default LabVIEW installation directory; change it when LabVIEW is installed elsewhere. If `lab/*.py` was changed, run `failtest` first as a smoke test on the next LabVIEW machine visit.

## The writer

- `writer/upstream/` are the upstream files before patching, not changed. After changing `writer/labview_mcp/{gen,lv}.py` run `scripts/regen_patch.sh` to regenerate `writer/patches/*.diff`.
- When the writer changes, the next round rebuilds everything automatically (`vi_job.sh` syncs it to the LabVIEW machine first); record a line in `writer/UPSTREAM.md` on why it was changed.
- List general fixes that could go back upstream for the user, and let the user decide whether to open a PR.

## The terminal-name catalog

- `catalog/terminals_confirmed.json` and `jobs/verified.json` can only be written by tools (the hook refuses manual edits): the former by `scripts/probe_terms.sh`, the latter by `jacq/job_check.py`.
- `catalog/probe_queue.json` (the probe queue) is maintained by offline lint: names no longer used by any job are withdrawn automatically at the next lint, and those failing two on-site probes are moved to `rejected`. Manual adds/removes with `python -m jacq.catalog queue add|remove`; don't edit the file directly.
- `catalog/catalog_public.json` is generated by `readers/offline/offline_batch.py` (offline extraction × on-site dumps paired by uid, with one group held out for cross-validation). Rerunning rewrites this file and takes tens of minutes: back it up first, and after the run compare the number of entries and the cross-validation score before replacing it. User-written subVIs (starting with `user:`) are removed during generation, leaving only NI's names.

## The VI-reading tools

- `readers/outline_vi.py` (single VI/directory → outline) calls `readers/offline/offline_batch.extract` and `vi_outline.py`; `readers/vi_trace.py` and `readers/vi_diff.py` read on-site dumps. Tests are in `tests/test_readers.py`, with synthetic data; don't reference files under `private/` in tests.
- Extraction is always `-x` + latin-1; text is converted back via code page 936 in the outline. Don't add any feature that repacks (`-c`) with pylabview.

## Changing skills

The master copy of a skill is `skills/<name>/SKILL.md`; when installed in `.claude/skills/<name>/` of the repo's parent directory, sync after changing (`tests/test_repo_hygiene.py` compares them):

```bash
for d in skills/*/; do n=$(basename "$d"); mkdir -p ../.claude/skills/$n && cp "$d"SKILL.md ../.claude/skills/$n/; done
```

When a command referenced in a skill is renamed or its arguments change, run each one in a fresh shell to confirm it works.
