<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 60 Hz throughout: line capacitance at the network's frequency

Status: in review (PR on `claude/dc19-line-capacitance-frequency`)
Type: bug
Blocked by: none (follows 09, 60 Hz throughout)
Gated: **yes** (gridspine)
Model tier: sonnet
Size: S
Owner decision: SAGC-1

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

`draft_campus` (`gridspine/producers/campus.py`) converted a PyPSA line's susceptance to the campus cable's capacitance as `c = b / (2π · 50)`, and wrote the campus `f_hz` as a fixed 50. PyPSA's `b` is the susceptance at the system frequency, so on a 60 Hz project the drafted capacitance came out 60/50 = 1.2× too high. That error reaches the campus AC power flow, the short-circuit calculation where C is used, and the harmonic impedance scan (issue 12). The 50 Hz constant scan in `tests/gridspine/test_sixty_hz.py` covered `gridspine/static` and `gridspine/drivers` but not `gridspine/producers`.

## What to build

1. One nominal frequency for the draft: `draft_campus(n, island=None, f_hz=None)` takes the stated `f_hz`, else the network's `meta["f_hz"]` (PyPSA keeps `meta` in the network file), else 50. It is validated by `ingest.campus.campus_f_hz`'s rule (50 or 60).
2. That frequency sets both the campus's `f_hz` and `c_nf_per_km = b / (2π f0) / length`, so the two agree.
3. The 50 Hz constant scan also covers `gridspine/producers`.

## Acceptance

- A line whose `b` is `2π · 60 · C` on a 60 Hz network drafts `c_nf_per_km = C` and `f_hz` 60. The same line at 50 Hz is unchanged.
- A stated `f_hz` wins over the network's.
- A frequency other than 50 or 60 (7, "60", True, 60.5) is refused, naming `f_hz`.
- `test_no_study_path_carries_a_50_hz_constant_outside_a_default` scans `gridspine/producers` and passes.

## Follow-up (not in this issue)

- The GUI's redraft (`campus_electrical_service.draft`) can pass the existing campus file's `f_hz`, or the project can set `meta["f_hz"]`. Until then a GUI draft stays 50 Hz, and a user who edits its `f_hz` to 60 afterwards must also correct each cable's `c_nf_per_km`.
