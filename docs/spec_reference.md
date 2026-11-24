# job and spec quick reference

One program = one `jobs/<job>.py`. Start from `jobs/_template.py`; copy the concrete structure from the closest one in `jobs/gallery.py` (all verified on-site).
The authoritative description of the writer is the docstrings of `vi_create` / `vi_run` / `vi_test` in `writer/labview_mcp/server.py` and `TYPE_HELP` / `STRUCTURE_HELP` / `NODE_HELP` in `gen.py`; this page is only a quick reference plus the keys this project adds.

## The job file

```python
from lablib import BASE                       # a fake lablib during local lint, the real one on-site
OUT = BASE / "write2" / "jobs" / "<job>"      # fixed; only VIs under write2 are allowed to run
VIS = [                                       # subVIs first, callers after
  {"name": "sub",  "desc": "one sentence", "spec": SPEC_SUB,
   "tests": [{"name": "example 1", "inputs": {"x": 1}, "expect": {"y": 2}, "tol": 1e-9}]},     # pure computation: vi_test, one run per case
  {"name": "main", "desc": "...", "spec": SPEC_MAIN,
   "runs": {"inputs": {...}, "outputs": ["y"], "repeat": 2, "timeout": 120, "check": "check_main",
            "actions": [{"set": {"x": 21}}, {"click": "go"}, {"wait": 0.5}, {"read": ["y"]}, {"click": "stop"}]},
   "runsnap": {"inputs": {...}}},             # screenshot of the front panel after running (run + screenshot in the same reference)
]
def check_main(v):        # v = {name: value}; with actions, v["_reads"] = the {name: value} read by each read step, v["_completed"]
    return {"pass": ..., "anything to note": ...}   # "pass" must be a bool: without it (or if not a dict) vi_job judges it not passed
PRODUCTION = [            # optional: long runs after the job is verified (lab/production.py <job>; lint checks it as PROD)
  {"key": "calib", "vi": "main", "inputs": {"x": 3}, "timeout_s": 6 * 3600},
  {"key": "use", "vi": "main", "inputs": {"x": {"from": "calib", "out": "y"}}, "check": "check_main"},   # ["k1", "k2"] concatenates
]
```
- inputs / expect / outputs / actions always use the spec **name**; vi_job automatically swaps in the visible label (Chinese works too).
- Write a subVI's `path` as `str(OUT / "sub.vi")` (don't forget `str()`, lint `SHAPE`); VIs in vi.lib use absolute paths (`vilib_paths` in `catalog/terminals_confirmed.json`). A job can only call two kinds of subVIs: VIs under this job's `OUT`, and vi.lib VIs registered in `VILIB_ALLOW` of `lab/jobguard.py` (currently only Uniform White Noise.vi); hardware I/O / network / external code / dynamic-call functions and VI Server nodes using `"class"` can't go into a job (lint `HW-IO`).
- A VI's `name` is its file name: letters, digits, underscores, hyphens. The **job name** (the file name of `jobs/<job>.py`) is stricter: only letters, digits, underscores, no hyphens (the LabVIEW machine uses it as a module name and output directory name, lint `JOB-NAME`); `OUT` must be exactly `BASE / "write2" / "jobs" / "<job>"` (lint `JOB-OUT`). The labels of controls / indicators in one VI all differ (lint `LABEL-DUP`).
- `runs` without `check` only confirms "it runs to completion" (lint `RUN-NOCHECK`); `verified.json` truthfully records it as having no assertions.
- Compute expected values with a Python reference implementation, not by hand.

## spec keys

| Key | Form | Notes |
|---|---|---|
| `controls` / `indicators` | `{"name": "x", "type": "dbl", "label": "增益", "default": 1, "style": "Waveform Graph", "position": [20, 0], "visible": [8, 8], "element_style": "Round LED", "connector": "left0"}` | name = ASCII identifier; label is free; subVI labels must be ASCII |
| `constants` | `{"name": "k", "type": "i32", "value": 3}` | Constants used only inside a loop go into the loop |
| `functions` | `{"name": "a", "function": "Add"}`; growable ones get `"size": n` | Look up names with `python -m jacq.catalog search <word>` |
| `subvis` | `{"name": "s", "path": "<absolute path>.vi"}` | Terminals = labels of the subVI's controls (name if there's no label) |
| `structures` | `for_loop` / `while_loop` / `case` / `event`, containing the same constants/functions/subvis/structures | See below |
| `property_nodes` / `invoke_nodes` | `{"name": "p", "control": "x", "properties": ["Visible"], "write": true}` | `invoke_nodes` isn't supported by the wire check yet (lint `WIRE-UNSUP`, not recorded as verified even if it passes on-site). Terminals `p.Visible`, `p.reference`, `p.error out` |
| `wires` | `[["source", "sink"], ...]` | Controls/constants/tunnels by name only; nodes as `name.terminal` |

Types: `dbl sgl i8..i64 u8..u64 bool string path error`; arrays with a suffix `dbl[]`, `bool[][]`; clusters `{"cluster": [{"name": "a", "type": "dbl"}, ...]}`; enums `{"enum": ["Init", "Run"]}` (values by item name); references `ref:Control`.

## Structures

| Structure | Terminals | Example (gallery) |
|---|---|---|
| For | `L.N` (count), `L.i`; `"shift_registers": ["s"]` → `s.left` (read inside the loop / initial value wired outside), `s.right` (written inside the loop / final value read outside) | `w02_array_io` |
| While | `W.stop`, `W.i`; shift registers as above; `"stop_if_true": false` inverts | `w01_while_sr` |
| Case | `C.selector`; `"cases": [{"value": "Init", "default": true, ...}]`; `"output_tunnels": ["t"]`, each frame wires one to `t` | `w04_enum_case`, `w08_error_case` (error cluster: `"No Error"/"Error"`) |
| Event | `"events": [{"control": "go"}, {"control": "stop"}, {"event": "Timeout", "timeout": 100}]`, frame contents as above; `"data": "d"` → `d.NewVal` | `w10_event_ui`, `ev_insert` |

State machine (common in CLD) = While + enum shift register + Case(selector=sr.left) + each frame gives sr.right the next state; UI-driven = an Event inside the While (the `Timeout` frame does the periodic work).

## Function terminals (common ones, all confirmed; full set `python -m jacq.catalog terms <function>`)

`Add x,y,x+y` · `Subtract x-y` · `Multiply x*y` · `Divide x/y` · `Increment x+1` · `Greater? x > y?` · `Greater Or Equal? x >= y?` · `Equal? x = y?` · `Select s,t,f,s? t:f` · `In Range and Coerce x,lower limit,upper limit,coerced(x)` · `Index Array array,index,element` · `Build Array element[k]→appended array` · `Concatenate Strings element[k]→concatenated string` · `Number To Decimal String number→decimal integer string` · `Unbundle cluster→element[k]` · `Unbundle By Name input cluster→<element name>` · queues `Obtain Queue element data type→queue out`, `Enqueue Element queue,element`, `Dequeue Element queue→element`, `Release Queue queue`.

## Where controls are read (the easiest thing to get wrong)

The writer puts **the terminals of controls/indicators at the top level of the block diagram**, not inside loops. Therefore:
- A control wired into a While loop = read **once** before the loop starts. A stop button wired directly to `W.stop` → pressing it won't stop it (lint `STOP-ONCE`).
- The only exception: if a control has a registered frame in an event structure (`{"control": "stop"}`), the writer moves its terminal into that frame (NI convention); write `[stop, W.stop]` as usual (gallery `w10_event_ui`).
- To read the latest value while running (duration, gain): put a read property node `{"name": "pd", "control": "dur", "properties": ["Value"]}` inside the loop and wire `pd.Value` (lint `READ-ONCE` reminds you).
- Code in event frames can't be wired to shift registers (gen reports an error); computations that need state go **outside** the event structure, in the same While (e.g. a Case framed by `st.left`, with the event structure only handling Stop + `Timeout` for timing).

## Button events change state (writer limitation; the property-node route is verified on-site)

NI's standard form is a shift register passing through the event structure, each frame changing the value and leaving through a tunnel. **This writer can't do that**: event frames can't be wired to shift registers, event structures have no `output_tunnels`; 5 workarounds were tried on-site and all failed (LabVIEW's XML scripting creates a new tunnel for every cross-frame wire, so several frames sharing one output tunnel can't be written; see `docs/lab_pending.md` #11). **Don't try again**, and don't relay through functions outside the event structure either (it builds but is broken).
There are two routes:
- **State advanced only by time** (traffic light): state in a shift register, computation in a Case outside the event structure, in the same While; the event structure only handles Stop + `Timeout` ticks.
- **State changed by buttons** (stopwatch, counter): state in front-panel controls (can be extra indicators), read/written via `Value` property nodes in the frames (verified on-site: 6 event frames, 17 property nodes, including array `Value` reads/writes). Cost: many controls, many property nodes in frames, and "read X" and "write X" in the same frame have no ordering -- to read before writing, make the write depend on the read result (e.g. `Select(value read, K, K)` then write), or chain the error wires `p.error out → q.error in` (unverified).
- A control with `style` loses its link if it's referenced only by property nodes in event frames (lint `STYLE-LINK`); don't wire property-node wires directly to `element[k]` of growable functions (lint `LATE-ELEM`).

## Timing of UI tests

When `check(v)` raises, vi_job records it as not passed (`check_error`) and doesn't crash; with `actions` it also receives: `v["_read_t"]` (time of each read step, seconds, counted from Run), `v["_click_t"]` (`[(button name, time), ...]` in click order, the same button can appear several times), `v["_elapsed"]` (when the VI stopped; 50 ms polling). (Verified on-site: the lights inferred from `_read_t` at the 4 read points of the traffic light were all right; the stopwatch's Lap intervals matched the click intervals to within 0.35 s.)
After each click/set the writer always waits 0.2 s, plus 50 ms polling, so "how long after clicking stop it stopped" can only be judged to the order of `_elapsed - time of the last stop click < 0.5` (measured on-site: 0.20 s = the measurement floor).
Timing tasks (traffic light, car wash): schedule reads in the middle of each phase, leaving ≥ 0.5 s margin; check infers the expected state from `_read_t`, and read points < 0.5 s from a boundary aren't judged. The writer always waits 0.3 s before the first action (this is the writer's sleep, not a measured VI startup time; when the VI actually starts timing has **not been measured on-site**).

## Updating indicators live inside a loop

Indicator terminals are at the top level by default: wired out of a While, **they update only once when the loop ends**. To display while running (LED, count, state):
- Write Value with a property node: put `{"name": "p", "control": "led", "properties": ["Value"], "write": true}` inside the loop (or case frame) and wire `p.Value`. Property nodes **can't be wired directly to tunnels** -- put a separate property node in each case frame, wired to that frame's constant.
- Or put it in an event structure: for controls/indicators wired in only one event frame, the writer moves the terminal into that frame (NI convention); periodic work goes in the `Timeout` frame.
- While tunnels are not auto-indexed by default in LabVIEW (unlike For), `W.i → cnt` out of the loop is the last value -- whether the tunnels the writer generates behave like this is **not verified on-site**; to be safe go through a shift register.

## SubVI connectors (`gen._assign_connector`)

- Assigned automatically in spec order: controls in turn `left0 left1 left2 top0 top1 left3`, indicators in turn `right0 right1 right2 bottom0 bottom1 right3`; once used up, any free slot is taken.
- Controls/indicators of type `error` take `left3` / `right3` first (NI convention, in at bottom left, out at bottom right).
- `"connector": "left1"` pins a slot (duplicates give a SpecError); `"connector": false` keeps it off the connector.
- 4×2×2×4 has 12 slots in total: with more than 12 controls + indicators, the extra ones must be given `false`, otherwise gen.build reports an error (lint `GEN`).
- The caller's terminal name = the **label** of the subVI's control (name if there's no label). Labels with spaces (like `ex.error in`) are written as-is -- lint accepts them, **not yet verified on-site**.

## How tests compare (`lv._matches`)

- Numbers: `|got − want| ≤ tol × max(1, |want|)` (relative tolerance, default `tol` see vi_test).
- Arrays / clusters: same length and element by element as above; strings, booleans, paths: exact equality. Enums: `vi_run` turns the ordinal read back into the item name (`lv.py` vi_run), so expect uses item names, compared exactly as strings.
- Multiple cases run the same VI in turn, `vi_run` only sets the inputs given in the case, and the code has no reset: inputs not written **very likely** keep the previous case's value rather than the default (derived from reading the code, **not verified on-site**). Safe practice: each case writes all inputs.
- Strings in inputs/expectations are passed as-is via `SetControlValue`: Python's `"a\nb"` is LF, for CRLF write `"\r\n"`.
- Whether **string constants** with newlines in the block diagram are built as-is via XML: **not verified on-site**. For a newline use a `u8` constant 10 and compare bytes (or `Byte Array To String`). XML can only contain ASCII (for Chinese labels see `docs/pitfalls.md` "Fixed in the writer").

## By Name and late-drawn wires

The element terminals of `Unbundle By Name` / `Bundle By Name`, like property/invoke nodes, are only wired in the writer's second pass (`gen.py` `_plan_nodes`):
the other end of these wires can't be a shift register, tunnel or event data node -- gen.build reports a SpecError outright, and lint gives E `GEN`. Put an ordinary function (Select, Always Copy...) in between and wire through it.
For `element[k]` of growable functions wired to late-drawn wires see lint `LATE-ELEM`(W).
