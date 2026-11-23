---
name: labview-data-mapping
description: Given a LabVIEW acquisition/storage VI and the data files it saved (.dat/.txt/.csv/.lvm/.tdms), work out "which file and which column is which channel after what processing", then reproduce the processing chain in Python and compare it point by point with the data, and finally deliver a Python read/processing script ready to use. Use when the user says "what is each column of this data / how do I read the files this VI saves / turn the LabVIEW processing into Python / were the two channels saved swapped". Not for: explaining a VI without involving data (labview-vi-explain).
---

# VI + data → what each column is → Python

All commands are run from the repo root, `$PY` is the local Python. Everything is offline and read-only. VIs, data and comparison results go only in `private/`; in the conversation give only the conclusions.

## 1. Read the storage logic out of the VI

First produce the outline as in **labview-vi-explain**. Look especially for:

- **Acquisition end**: DAQmx physical channel constants (like `DevN/ai0:1`), sample rate, number of samples, triggering; which channel `Index Waveform Array` / `Index Array` takes.
- **Processing chain**: the functions and subVIs each channel passes through between being read and being written to file (filtering, averaging, lock-in, normalization), along with their parameter controls and default values.
- **File-writing end**: `Write To Spreadsheet File`, `Write To Measurement File`, TDMS, `Write Text File` etc.; how the file path is assembled (base path control + loop counter + extension), `transpose` (true = one column per quantity), `append` (true = restarting the program continues writing onto the old file), the format string (`%.8f` etc. determines precision), the delimiter.

Which channel goes into which column of which file depends on the wires. If there's an on-site scan dump, trace back from the file-writing node:

```bash
$PY readers/vi_trace.py <dump.json> nodes write              # find the id of the file-writing node
$PY readers/vi_trace.py <dump.json> back <file-writing node id> 12 --stop daqmx_
```

Without a dump, "column k = which channel" can only be [inferred], and must be confirmed by the data comparison in step 3, or by asking the user to arrange an on-site export (**labview-lab-session** "Exporting existing VIs").

## 2. Write the mapping table

Save to `private/offline/explain/<program name>/data_mapping.md`:

| Physical channel | Processing in the VI | File (path control → file-name rule) | Column | Physical quantity/unit | Source |
|---|---|---|---|---|---|

In the "Source" column write [read] (wires/constants), [inferred], [confirmed by comparison]. A control's label and the channel it's actually wired to may not match; go by the wires, and point it out separately below the table.

## 3. Reproduce in Python and compare

1. Read a few real data files: confirm the number of rows, columns and precision agree with the inferences of step 1.
2. Write the computation of each column in Python (the input is the rawest column, or a rawer file). scipy/numpy filters, windows and rounding must match LabVIEW: Butterworth uses `sos`, and confirm whether the `fs` terminal is wired (unwired = normalized frequency); array indices and lengths follow LabVIEW's "start + length".
3. Compare point by point: the error should be on the order of the file's storage precision (`%.8f` → around 1e-8). If it doesn't match, go back to parameter defaults and branches; don't tune parameters to make it fit.
4. When a parameter can't be inferred (the same parameter takes different values in several processing chains), reproduce once with each candidate value: whichever reproduces it, the data comes from the processing chain with that value. Such a conclusion only shows which processing chain the data went through, it doesn't directly prove the physical channel; keep them separate when writing the explanation.

## 4. Delivery

- Under `private/offline/explain/<program name>/`: the mapping table, `read_<program name>.py` (file reading + processing functions + comparison in `__main__`), the comparison record (which files were used, maximum error).
- In the conversation give: a table of what each column means, the comparison conclusion (how many files, maximum error), the places still relying on inference.
- Problems like misleading labels, swapped channels, or `append` mixing new and old data in one file are listed separately and prominently.

## Bottom lines

- VIs the user says must not be touched are only explained, not replicated in Python (neither processing chains nor control sequences are written as anything executable).
- Don't run existing VIs to confirm the mapping; for new data ask the user to acquire it, or write a new VI under `write2\` and dry-run it with simulated devices (**labview-vi-write**).
