---
name: labview-vi-explain
description: Explain to someone what an existing LabVIEW VI (a .vi file or a whole program folder) does: what each front-panel control means, the block-diagram flow, how the subVIs divide the work, where data comes from and goes to; also used to compare what changed between two versions of the same VI. Works fully offline locally (Jacquard's pylabview read-only extraction + outline); with an on-site scan dump, the wires can also be checked. Use when the user says "explain this VI / what does this program do / I can't follow this block diagram / what's the difference between these two versions". Not for: writing a new VI (labview-vi-write); matching a VI against the acquired data files column by column (labview-data-mapping).
---

# Explaining an existing VI

All commands are run from the repo root, `$PY` is the local Python. Everything is offline and read-only, no LabVIEW needed.

## First settle two things

- **Where the files go.** VIs the user copies over always go in `private/inbox/<date or program name>/` (gitignored), with the main VI and its subVIs together. VIs, outlines and explanation drafts go only in `private/`; in the conversation give only summaries, don't paste whole block diagrams.
- **How detailed to be.** Ask whether the user wants "roughly what it does", "what to fill into each control", or "how a particular number is computed". The last one needs wires, see step 3.

## 1. Produce the outline (offline, a few seconds)

```bash
$PY readers/outline_vi.py private/inbox/<dir>/            # recursively processes all .vi in the directory, keeping the subdirectory structure
$PY readers/outline_vi.py <a.vi> --print                    # a single VI, also printing the outline
```

Extraction needs a patched pylabview clone (the environment variable `PYLV` points to it; when unset, `../pylabview` and `../../pylabview` next to the repo are tried in turn; see "Requirements" in the repo README, and when it can't be found the command says directly where to put it).

Outlines are written to `private/offline/outlines/<input dir name>/<relative subdir>/<VI name>.outline.txt`, one per VI (different VIs with the same name don't overwrite each other; the end of each line of the command output says where it was written), with contents:

- `[dependencies]` linked subVIs, vi.lib functions, control types.
- `[defaults DFDS]` default values of controls (the program runs with these when the user hasn't changed them).
- `[front panel]` each line starts with `[control]` = input control, `[indicator]` = indicator (read from the flag bits of the front-panel object, checked against on-site dumps, can be taken as [read]); then category, label, dropdown items. Common categories: `stdNum` numeric, `stdBool` boolean, `stdRing` ring/enum, `stdString` string, `stdPath` path, `indArr` array, `stdClust` cluster (error clusters too), `stdGraph` waveform graph and waveform chart, `stdMeasureData` waveform data, `stdTag` DAQmx physical channel/task name, `stdRefNum` refnum, `tabControl` tab control, `typeDef` type definition. Chinese is converted back via code page 936; if it's still mojibake, add `--text none` to see the raw bytes.
- `[block diagram]` indented by structure nesting: loops, each case of case structures, sequence frames, event frames, and inside them subVIs, property nodes, primitives, constant values, front-panel terminals, comments.

Primitive names marked " (learned)" come from `catalog/catalog_public.json` (learned by pairing offline extraction with on-site dumps by uid, trustworthy); those with `?` are guesses; those with only a number `#nnnn` are unknown, infer them from terminal types and context, and mark this in the explanation.

**The outline has no wires.** Which structure and which case a node is in is read; whose output is wired to whom is inferred.

## 2. Write the explanation

Bottom-up: first explain the subVIs (usually short, their role fits in one sentence), then the main VI. Save to `private/offline/explain/<program name>/explanation.md`, structured as:

1. **One sentence**: what this program does, who it's for, how to use it (what to fill in before running, what to click, where it stops).
2. **Front-panel table**: control → meaning → default value → unit/range. Indicators are listed too.
3. **Flow**: explained in structure order (initialization → main loop → each case/event frame → cleanup). For state machines, list the states and transition conditions in a table.
4. **SubVI table**: name → role → key inputs/outputs.
5. **Where data goes**: acquisition channels (DAQmx physical channel constants), file-writing nodes (how the file name is assembled, append or overwrite, delimiter), what the graphs display.
6. **What's unclear**: list the places relying on inference and what it would take to confirm them (an on-site dump, user confirmation, comparison with data).

Mark the source of each conclusion: **[read]** (present as-is in the outline/dump), **[inferred]** (guessed from node order, names, types). Data flow whose wires haven't been checked is always [inferred].

When explaining to people use the user's words: controls by their labels on the front panel (in Chinese if they're Chinese), and the first time a function appears give its Chinese name + English name (e.g. "数组子集 Array Subset").

## 3. When wires are needed

To check data flow ("where does this value actually come from", "were the two channels wired swapped"), an on-site scan dump is needed:

- Already there: existing VIs exported read-only are in `private/lab_corpus/lab_dump/*.json`, VIs built by the agent in `private/lab_corpus/write2/jobs/<job>/dumps/*.json`.
- Not there: it has to be exported once on a computer with LabVIEW installed (export only, no running), see **labview-lab-session** "Exporting existing VIs". This needs the user's consent first; don't connect over SSH or start LabVIEW yourself for this step.

```bash
$PY readers/vi_trace.py <dump.json> nodes [substring]          # find node ids (filtered by label, function name)
$PY readers/vi_trace.py <dump.json> back <node id> [depth]   # where this value comes from
$PY readers/vi_trace.py <dump.json> fwd <node id> [depth] --stop write_to_spreadsheet,daqmx_
$PY readers/vi_trace.py <dump.json> unwired               # unwired terminals recorded in the dump (terminals unused in subVIs are in there too, not necessarily missing wires)
```

Constant values missing from the dump are found in the outline (same uid). Wires that have been traced are re-marked [read] in the explanation.

## 4. Comparing two versions

- Both have on-site dumps: `$PY readers/vi_diff.py <old.json> <new.json>`, pairs by LabVIEW object uid, lists added/deleted nodes, constant value changes, type changes, moves into other structures, added/removed wires.
- Only .vi files: produce outlines for both (in different directories, or first copy each to `private/inbox/<old>/`, `private/inbox/<new>/`), and `diff -u` the two `.outline.txt`. Changes to controls, default values, constants, nodes and structures can be seen, wire changes can't; say so when explaining.
- First run `sha256sum`: if they're byte-identical there's nothing to compare.

## Bottom lines

- **VIs the user says must not be touched are only explained.** No Python replicas, no runnable equivalent VIs or scripts, and no directly executable control sequences in the explanation either.
- Only extract with pylabview `-x` (`outline_vi.py` already does this). Don't repack (`-c`) any VI with pylabview: its encoding/decoding is lossy and will corrupt the VI.
