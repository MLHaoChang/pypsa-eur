<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08c Sub-synchronous scan: the reason order and the screens' note texts

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (follows GC-08 slice B)
Gated: **yes** (GK A2, gridspine)
Model tier: opus
Size: S
Owner decision: GC-TEXT-1
TRGC: 19, 20 (screening; binding studies stay in PSCAD)
Likely files: `gridspine/static/subsync_scan.py`, `gridspine/static/ssti_uif.py`, `pypsa-gui/backend/services/adequacy/grid_readiness.py` (`_subsync_reasons`, `_ssti_notes`, `_subsync_row`), `tests/gridspine/test_subsync_scan.py`, `pypsa-gui/backend/tests/test_grid_readiness_ssti.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-08 slice A shows the engine's own reasons on the scorecard, and slice B shows them in the report pack (GK A2 B7). Review of both found reasons that come out in the wrong order or with leftover wording. GK A2 asked for them to be filed as a follow-up (slice B plan, S7), not fixed in slice B.

## What to fix

Confirm each item against the code before fixing it, and give each a test.

1. **Reason order.** `subsync_scan` writes `NO_SERIES_CAP` ("not evaluated: grid-side series compensation needs the TSP's data") on each case's series row. It does so even when `pcc.sk_normal_mva` is missing, so that case was never scanned. The scorecard row and the pack caption then lead with the TSP, although the first reason the user can act on is the missing normal case. Lead with the input the user can supply.
2. **"demo default".** `NO_CLEARANCE` and `ssti_uif.NO_THRESHOLD` end in "demo default". The text reaches the scorecard's notes and the pack. Say what the value is instead, for example "an assumed screening value".
3. **Blank notes entries.** A meta `notes` list with an empty or blank string joins into the reason as an empty clause ("a; ; b"). Skip blank entries, as `_subsync_reasons` already does for table notes.
4. **The "check" fallback.** When the scan's verdict is `check` and no note gives a reason, `_subsync_row` falls back to `NO_MODES`. Make sure the fallback is only used when the run gave no reason of its own, and pin it with a test.
5. **The "( )" margin.** A note formatted from a missing margin shows empty parentheses. Leave the parenthesis out, or say the value is missing.
6. **The repeated UIF note (GK A2 on #1879, Q1 (A)).** The run writes the same note on every converter-bus row of the UIF table, so the pack's `ssti_uif` Note column repeats it and 20 rows wrap across about 9 PDF pages. Show a note shared by every row once, as a line of the caption, and leave the Note column only for notes that differ between rows.

The items below are GK A2's non-blocking findings on #1879 @1d4d9889 (N1–N5), filed here rather than fixed in slice B:

7. **Raw exceptions before the stage (N1).** `grid_readiness._project_inputs` lets raw exceptions out: a settings file that is not a dict gives an AttributeError, an unknown profile gives HTTPException 422, and a corrupt `.nc` raises too. The pack then reads GC-08 as unavailable with a traceback, even before any stage has run, where it should read pending. Map these to pending or to a plain reason.
8. **A meta path that is a directory (N2)** reads pending. It should read unavailable with a plain reason, as an error path that is a directory already does on the scorecard.
9. **A non-dict `uif` record in a dict meta (N3)** gives `present` with every row "not judged". It should read unavailable.
10. **The Threshold column (N4)** shows the CSV's per-row `threshold`, but the judgement uses the meta's limit. Show the meta's limit, or flag the row where the two differ.
11. **Saved and live networks (N5).** The pack reads the saved network (`ssti_screens_for`), while the scorecard UI reads the live one. Decide which one both should use, or say in the pack which one it read.

## Done when

- Each item has a test that fails before the fix.
- The scorecard notes and the pack captions read the new text. The slice B tests compare captions with the scorecard note, so they follow without edits.
- A release-note fragment is added.
