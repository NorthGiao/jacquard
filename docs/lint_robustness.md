# Making "lint clean" predict "builds on-site" as far as possible

Goal: a VI written locally that passes lint should not run into further problems once built via VI Scripting on the LabVIEW machine.
**The scope that can be promised**: builds, isn't broken, no wire silently dropped, 0 mismatches in the wire check -- these are outcomes at the "build" level, which lint can try to predict.
tests / check (whether the logic is right) can only be judged on-site, lint can't cover that. The numbers below are **measurements, not guarantees**.

## Where building a VI can fail

| Failure point | Where | Predictable offline? | Who handles it |
|---|---|---|---|
| spec contradicts itself | `gen.build` (SpecError) | Yes (it's the same code) | lint `GEN` (lint calls gen.build directly) |
| XML rejected by LabVIEW (1063 terminal name, 1374 unknown element) | `lv.vi_create` | Terminal names yes | `TERM` / `UNVERIFIED` / `SUBVI?` (names only come from LabVIEW) |
| Default value can't be set | `SetControlValue` | Partly (value/type mismatches are already caught by gen.build) | `GEN` |
| Event frame registered as a different event | `event_frames` check | Version-dependent | Writer patch (2024 Q1 event code table) |
| **VI broken** | `exec_state` | **Mostly types** → type propagation | `TYPE` / `DIMS` / `AUTOIDX` / `REQ` / `CASE-*` (E); `TYPE?` / `REQ?` / `CASE-*?` (W) |
| Wire silently dropped | Writer `warning`, wire check | Partly (Chinese subVI terminals, wires with mismatched types) | `ZHSUB`; wire_diff of on-site `job_check` |

## Type propagation (`jacq/typecheck.py`)

Starting from the terminal types of controls/constants/this job's subVIs, the coarse type (category + dimensions) of each wire is inferred along the wires: shift registers, case output tunnels,
For loop auto-indexing (−1 dimension going in, +1 going out), event data node NewVal, property node Value, output rules for about 90 common functions.
**If the type of any wired input can't be inferred, this function's output isn't inferred** (e.g. when one input comes from a vi.lib subVI, it isn't judged from the other, scalar input).

Receiving-end checks: indicators, subVI inputs, function inputs (terminals for which the rule table says "only accepts these categories"), the While stop terminal, the For N,
case selectors, property node Value writes, the t/f inputs of Select, the inputs merging into the same tunnel/shift register, enqueued element vs. queue element.

**Level rules**: only category pairs confirmed by on-site probes as "broken once wired" give E (`CONFIRMED`, sources written in the header of typecheck.py,
from 143 single-function probes and `jobs/typeprobe.py`); other mismatches give W (`TYPE?`); functions without rules give I (`TYPE-UNK`).
Counter-intuitive on-site facts (don't add rules by intuition): And/Or/Not/Exclusive Or accept dbl, To Upper/Lower Case accept dbl, Type Cast accepts dbl -- all build.

**Generalization**: a category pair was originally confirmed on one kind of receiving end (e.g. bool→num on an indicator), and E is reported for all receiving ends;
tp09/tp10/tp13/tp14/tp19/tp21/tp27 of `jobs/typeprobe.py` (string/bool indicators, Add terminals, While stop terminal, case selector, both inputs of Select) were all broken on-site, so the generalization holds.

E codes (predicting "will be broken", `lint.PREDICTIVE`): `TYPE`, `DIMS`, `AUTOIDX`, `REQ` (Add.y, Select.f), `CASE-DUP` / `CASE-DEFAULT` (integer selectors), `CASE-VAL` (enum selectors).
Still W (not confirmed on-site): different enum items, different cluster element types (same count), bool↔path, enqueued element ≠ queue element, required inputs of other functions, case problems with string/bool selectors, type differences on graph (Graph) indicators.

## Measurements (all reproducible offline)

| Set | On-site outcome | lint |
|---|---|---|
| Verified jobs (`jobs/verified.json`) | All built, idle, tests passed | 0 E |
| shlim-t upstream `live_vi_create` 28 cases | 28/28 passed on-site | 0 E (the 3 `SUBVI?` are because the reference script doesn't give subVI terminals, not false alarms) |
| 143 single-function probes (48 idle / 95 broken) | See left | **False alarms 0/48**; broken caught 43/95 (E), 1 W. **Of the 99 lint said were fine, 51 were broken on-site**: 42 are probes with no wires at all (required inputs left empty; real jobs won't be like this), 7 already got I `TYPE-UNK` from lint (VISA, Interleave/Decimate 1D, Swap Values, Get File Position), 2 are **completely silent** (`s_cluster_bundle`: dbl,dbl → Bundle → isomorphic cluster indicator; `s_event_stop`: event structure inside While registering stop, `[stop, W.stop]` -- the same form passed on-site in gallery `w10`, cause not found) |
| `broken` of `failtest` | broken | E `DIMS` (`LINT_EXPECT` lets it go to the LabVIEW machine anyway to rehearse the error-report path) |
| Held-out set: two multi-VI tasks (7 VIs) | Built idle, all tests passed, 0 wire mismatches | Frozen prediction 0 E 0 W, agreeing with on-site |
| `typeprobe` 39 (out-of-sample probes designed specifically after the rules were frozen) | 21 broken / 18 idle | With the frozen rules: all 8 tentative E broken, all 17 ok idle, of the 14 W 13 broken 1 idle → **0 misses, 0 false alarms**. After changing rules based on this, 39/39 (this step is in-sample) |

**The single-function probes are in-sample numbers**: the `CONFIRMED` category pairs were induced from exactly these 143 probes; 0 false alarms / 43 caught shows that the rules are consistent with their sources, **not their predictive power for new programs**.
The out-of-sample tests on new programs are the held-out set and `typeprobe` (both have 0 misses and 0 false alarms, but typeprobe consists of "forms covered by existing lint rules", and doesn't represent functions without rules).

`python -m jacq.oracle --probes` reproduces the single-function probe row (needs the on-site reports under `private/lab_corpus/probes/`); `tests/test_typecheck.py::test_probe_oracle_no_false_broken` guards "0 false alarms";
`tests/test_lint_regression.py` guards "verified jobs and the 28 upstream cases 0 E" (the upstream cases are stored verbatim in `tests/data/`, MIT; except `SUBVI?`, see above).

## On-site as the judge (procedure)

0. **First run a job known to pass as a smoke test** (`failtest`: also rehearses `LINT_EXPECT` and the error-report path; or any gallery one), to confirm the tooling itself is fine.
1. **Type probe batch**: the `typeprobe` job (`BUILD_ONLY = True`: only build, exec_state, dump, no running; a VI failing to build/broken
   doesn't stop it, a report is written after each VI is built, so even if it hangs the earlier ones can be collected). Once the results are back, `job_check` automatically hands over to `python -m jacq.oracle typeprobe`, which gives per VI:
   correct / false alarm (change rule) / upgrade (W→E, this VI is the source) / downgrade (remove the W) / miss (add rule). "Clean" on-site = built + idle + no dropped-wire warning + 0 wire-check mismatches.
2. For each change: E is only added with an on-site counterexample; all verified jobs and the 28 upstream cases must stay at 0 E (`tests/test_lint_regression.py`).

## Known blind spots (lint clean can still go wrong on-site)

- Downstream of functions without rules (listed by `TYPE-UNK`) there's no type check; the terminal types of vi.lib subVIs are unknown.
- Clusters only compare element count and element-wise category; enums only compare the item list; numeric representations (i32/dbl) are all treated as automatically convertible.
- Required inputs are only listed for about 40 functions (`REQUIRED`), only Add.y and Select.f are confirmed on-site (E), the rest W.
- Swapped wiring between two nodes of the same function (wire_diff can't see it either, see `docs/lab_pending.md` "To extend").
- Whether logic/behavior is right: only tests / check can judge.
