<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 60 Hz throughout (nominal frequency carried, not assumed)

Status: done
Type: task
Blocked by: none
Gated: **yes** (gridspine)
Model tier: opus
Size: S
Owner decision: SAGC-1 (G2), 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

Correctness bug. Several study paths assume a 50 Hz system, so a 60 Hz network (SAGC, Americas,
parts of Asia) gets wrong absolute frequency results. Examples: `F0 = 50.0` at
`gridspine/static/frequency.py:63` (droop gain P_n / (R·f0) and every Hz limit read against it),
`gridspine/static/gfm_tuning.py`, the island frequency step in `gridspine/drivers/campus_study.py`
(~l.458) and the RAW export.

## What to build

1. Carry the nominal frequency `f_hz` from the network / campus (several modules already read an
   `f_hz`: `gridspine/schema/island.py`, `static/island_*.py`, `producers/campus.py`) into
   `frequency.py`, `gfm_tuning.py`, `drivers/campus_study.py` and the RAW export, instead of a
   module constant. Default stays 50 Hz where nothing states it.
2. Grep for other hard-coded 50 / 50.0 / 314.159 / 2π·50 in gridspine and fix those in study paths.

## Done when

- A 60 Hz island and the same island at 50 Hz give the same **per-unit** results (frequency
  deviation / f0, RoCoF / f0, droop response), with a test.
- The RAW export writes the network's base frequency.
- No study path reads a 50 Hz constant when the network states otherwise (test or grep check).

## Triage (gatekeeper, 2026-10-23)

**Status `ready-for-agent`, gated, size S.** It ships first because it is the owner's
priority, not because the others depend on it (see their triage).

What the code shows:

- **`frequency.py` already takes `f0` as a parameter** that defaults to `F0 = 50.0` (l.63), and
  so does `gfm_tuning.py` (`f0=F.F0`, l.146 and l.190). The bug is in the callers: they never pass
  it. Thread the network's `f_hz` from the campus driver and the island path down to every
  `simulate` / `judge` / `trip_floor` / `emt_flags` / `equipment` call. Keep `F0` only as the
  default for callers that state nothing.
- **The RAW export is already parameterised.** `raw_writer.write_raw(..., f_hz=50.0)` takes it,
  `handoff/campus_bundle.py` passes `net.f_hz` and the year study passes `CASE39_F_HZ`. This
  acceptance item becomes a test that a 60 Hz campus writes `60.00` in the RAW header. No rewrite.
- **Watch these:** `ingest/campus.py` already accepts only 50 or 60 (l.463), and `handoff/bundle.py`
  has `f_hz: float = 50.0` as a dataclass default. `producers/campus.py` writes `"f_hz": 50` into
  demo campuses. That is fine as a default, but no study may re-read it in place of `net.f_hz`.
- **Grep check for the acceptance.** No `50.0`, `314.15` or `2*pi*50` may sit in `gridspine/static`
  or `gridspine/drivers` outside a default argument. A test can assert it by scanning those files
  with the default-argument lines allowed.
- **The per-unit equality test** runs the same island at 50 and 60 Hz and compares Δf/f0,
  RoCoF/f0 and the droop response. Droop gain is P_n / (R · f0), so the two cases match only once
  f0 is threaded through.
