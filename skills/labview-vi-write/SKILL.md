---
name: labview-vi-write
description: Write a new LabVIEW VI (front panel + block diagram + subVIs) to the user's requirements, using the Jacquard project: Python spec → jacq.lint (offline) → built on the LabVIEW machine by VI Scripting, tested automatically, screenshotted, wires checked one by one. Use when the user says "write me a VI / a LabVIEW program / make a front panel / a state machine / a CLD task / turn this algorithm into a VI". Not for: explaining an existing VI (labview-vi-explain); modifying an existing VI (existing VIs are not changed, only new ones can be written).
---

# Writing a VI to requirements

**Forms, commands and pitfalls are all in the repo's `AGENTS.md`**; read that page first and follow its six steps under "Writing a VI". This skill only adds the two ends: clarifying the requirements before starting, and how to deliver when done.

## Before starting: ask until tests can be written

Ask everything at once, listing the questions in the order below, each with a default value, so the user only needs to confirm or change:

1. **Inputs/outputs**: the name of each control and indicator (the label shown on the front panel, can be Chinese), type, unit, default value.
2. **Behavior**: one or two sentences + two or three "input → expected output" examples. For anything with UI interaction (buttons, stop, state machines), ask exactly what should be seen after each button is pressed, and how soon.
3. **Layering**: which parts should be subVIs (things to be reused later, things that can be tested separately). SubVI control labels can only be ASCII.
4. **Hardware**: whether this VI touches an acquisition card. Pure computation is written as usual; for acquisition see "Involving acquisition cards" below.

If the requirements are ambiguous (rounding direction, empty arrays, division by zero, boundary values), write it into the examples for the user to confirm; don't decide it yourself.

## Writing

Following `AGENTS.md`: `jobs/_template.py` → copy the closest structure from `jobs/gallery.py` → look up function and terminal names with `jacq.catalog` → generate the expected values of tests with a `ref_*` Python reference implementation → the hook lints automatically → if there's an E, fix it.

Finish everything that can be done offline first (lint 0 E, W's looked at). Building and testing require really operating LabVIEW (run directly on the local Windows machine, or over SSH to the LabVIEW machine), which needs the user's consent in the current conversation; the rules and commands are in **labview-lab-session**. Before starting, tell the user which VIs this round will build, which tests it will run, and roughly how long it takes.

## Involving acquisition cards

Default scope: VIs built under `write2\`, doing only pure computation. Requirements involving acquisition cards go beyond this scope; explain clearly to the user first, then decide:

- The job pipeline doesn't build or run hardware I/O nodes: if the spec contains functions like VISA / GPIB / serial / TCP / UDP / DAQmx / System Exec / Call Library Function / Open VI Reference, or VI Server nodes using `"class"`, lint reports E `HW-IO` (can't be downgraded with `LINT_EXPECT`), and the LabVIEW machine refuses the whole job too. SubVIs can only be VIs built earlier in this job, or registered pure-computation vi.lib VIs (currently only Uniform White Noise.vi; to use another vi.lib VI, like the one that writes spreadsheet files: currently no tool can register its terminals, list it for the user and ask the user to decide, don't work around the hook yourself).
- The only thing in the repo that touches DAQmx is `lab/acq_sim4ch.py`: a hand-written on-site script that builds VIs under `BASE\acq\`, only reads AI, and only dry-runs with simulated devices in NI MAX. Using it needs the user's explicit consent (see **labview-lab-session** "NI MAX simulated devices").
- No AO/DO writes.
- Requirements like "change the existing two-channel acquisition program to four channels": existing VIs are not changed and not run. First explain the original program with **labview-vi-explain** (channels, processing chain, storage), then make "the four-channel processing + storage" a pure-computation VI (inputs are each channel's data array, outputs are the processing results and the 2-D array before writing to file), verified under `write2\`; the acquisition part is wired by the user themselves in LabVIEW, or dry-run separately with simulated devices with the user's consent.

## Delivery

Only say "it's done" when everything passes (recorded in `jobs/verified.json`). `python -m jacq.job_check --status <job>` lists each VI's evidence kind: `tests` and `runs+check` have asserted behavior; `runs(no assertion)` and `build only` only show that it builds, isn't broken and runs to completion, which must be stated truthfully when delivering, not passed off as "tests passed". The delivery message includes:

- The path of the VIs on the LabVIEW machine: `D:\agent_sandbox\lv_agentic\write2\jobs\<job>\`, and which one is the main VI.
- A table of subVIs: name → role.
- tests/check results: how many cases, which behaviors are covered; for UIs, which operations the actions simulated.
- The wire check result (0 mismatches). When the check wasn't done (missing dump, `WIRE-UNSUP`), `job_check` won't record verified; don't treat it as passed yourself.
- Screenshot path `private/lab_corpus/write2/jobs/<job>/snap*/`, ask the user to look at the front-panel layout (boolean button text and LED colors can't be set and have to be changed by the user by hand).
- What isn't covered: which behaviors tests can't judge and the user needs to click through by hand in LabVIEW.

If it didn't pass, say truthfully which step it stopped at, the original error text, and how you plan to change it in the next round. Passing lint doesn't mean it's written right.
