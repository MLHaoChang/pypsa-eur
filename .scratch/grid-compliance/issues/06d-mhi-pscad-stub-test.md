<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06d Test the generated mhi.pscad automation stub

Status: ready-for-agent
Model tier: sonnet
Blocked by: none
Type: task
Parent: `06-pscad-emt-pack.md` (GC-06 follow-up, GK A2 on #1596)
Gated: **yes** (GK A, gridspine)
Size: S
Likely files: `gridspine/handoff/emt_pack.py` (`stub_text`), `tests/gridspine/test_emt_pack.py`

## What

`stub_text` writes the mhi.pscad automation script, which is never run here or in CI and has no test beyond its presence. Add at least: it compiles (`compile(text, ...)`), it reads the schedule CSV the pack writes, and every column it sets exists in `SCHEDULE_COLUMNS`, with `mhi.pscad` stubbed by a fake module so the loop runs once per case.

## Comments

**2026-11-17 triage (SCALE-1; GK A2 4 priority view).** Bundle with 06c (one builder).
