<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06d Test the generated mhi.pscad automation stub

Status: needs-triage
Type: task
Parent: `06-pscad-emt-pack.md` (GC-06 follow-up, GK A2 on #1596)
Gated: **yes** (GK A2, grid-compliance, GC-PACE-1)
Size: S
Likely files: `gridspine/handoff/emt_pack.py` (`stub_text`), `tests/gridspine/test_emt_pack.py`

## What

`stub_text` writes the mhi.pscad automation script, which is never run here or in CI and has no test beyond its presence. Add at least: it compiles (`compile(text, ...)`), it reads the schedule the pack writes (`case_schedule.json`; the CSV header is checked against the same columns), and every column it sets exists in `SCHEDULE_COLUMNS`, with `mhi.pscad` stubbed by a fake module so the loop runs once per case.

## Comments

GK A2 (4) plan GO, one PR with 06c. E5: test what the stub really reads. It reads `case_schedule.json`, not the CSV (wording above corrected): every key it reads is in `SCHEDULE_COLUMNS`; the CSV header equals `SCHEDULE_COLUMNS` in order; with a fake `mhi.pscad` in `sys.modules` the loop runs once per case with that case's values.
Follow-up for this PR once contract v2 is on master: add the `connected` sentence to `stub_text`'s instruction paragraph (sibling GC-12/06a/06b asked; it may not edit `stub_text`).
