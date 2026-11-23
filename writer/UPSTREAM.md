# The writer = shlim-t/labview-mcp + local patches

- Upstream: https://github.com/shlim-t/labview-mcp , v0.27.0, commit `03895d2d8fc00986f0bf271af877acfab9e48ac6`, MIT (`LICENSE.shlim-t`).
- `labview_mcp/`: upstream `src/labview_mcp/` copied as-is + `patches/*.diff`. `upstream/` holds `gen.py`/`lv.py` before patching.
- After changing `labview_mcp/gen.py` or `lv.py`: `scripts/regen_patch.sh`; `tests/test_repo_hygiene.py` checks upstream + patches == the current writer.
- Syncing in remote mode: `scripts/sync_writer.sh` (called automatically by `vi_job.sh`; any change triggers a full rebuild). Default location on the LabVIEW machine: `D:\agent_sandbox\shlim_pkg\labview_mcp\`.

## Patch contents (0001, verified on-site with LabVIEW 2024 Q1)
1. Event codes by version (Value Change = 66 in 2024); 2. the index of Insert/Replace/Delete Array goes through a second wiring pass; 3. KeyError of By Name; 4. padding the number of enum items;
5. Non-ASCII labels: build with name first, relabel at the end; 6. `position` (precise placement with setpos after changing style), `visible` (generates `lvmcp_arrsize.vi`), `element_style` (generates `lvmcp_arrelem.vi`);
7. Non-ASCII subVI terminal names → SpecError (LabVIEW silently drops wires); vi_edit doesn't accept the keys of 5/6.

8. Bundle `element[k]` → script position k + 1 (`_ELEMENT_POS_OFFSET` in `gen.py`): Gen Script reads `element[k]` as terminal position k, and Bundle's position 1 is the `cluster` input, so specs (and the scan dump) number elements from 1 while the script needs k + 1. On-site 2026-11-21 (LabVIEW 2024 Q1, `crds_core`): without it `element[1]` landed on `cluster`, the last element input stayed unwired and the VI was broken. General fix, could go upstream.

9. Optional DFIR reads in `lv.py` (`_sink_count`, `_enum_items`, `_connector_labels`) also catch `IndexError`: on-site 2026-11-21 (`crds_li2026_rest` / `gum_unc`) `dfir.parse` raised `IndexError` at `dfir.py:352` (`t.tds[r]`), which failed the build and then the tests although these reads are documented as "None / empty when unreadable". Made by the lab agent, reviewed 2026-11-22. The `dfir.py` bug itself is still open.
   (Not upstream: our `scan.py` now also numbers unnamed Bundle / Unbundle element terminals `element[k]`; before, the dump wrote `[uid …]` and `wire_diff` stopped with `KeyError`. Same on-site round.)

## Results of the upstream tests after patching

`lab/run_upstream_tests.py` runs upstream's own live tests on LabVIEW 2024 Q1 (VIs built in `write2\upstream_tests\`):

- `live_vi_create` 28/28.
- `live_vi_edit` 22/24: the two "constant values" ones fail, because the Korean `안녕` in the tests becomes `??` in code page 936 (the tests assume the ANSI code page can represent it).
- `live_project` (building an EXE), `smoke_mcp` (needs the mcp package) weren't run.

## Findings related to upstream behavior

- NI's XML scripting parser parses and stores label bytes as UTF-8; under non-UTF-8 code pages (like 936) non-ASCII labels fail entirely or come out garbled, and can only be relabeled afterwards (see `docs/pitfalls.md`).
- Event frames can't share output tunnels, shift registers can't pass through event structures (see "To extend" in `docs/lab_pending.md`).
