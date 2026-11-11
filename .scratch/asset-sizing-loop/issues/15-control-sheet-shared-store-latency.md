<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-15 — Control-settings sheet reads the shared store latency

Status: done (built on master before this triage; PR number not traced)
Type: task
Blocked by: AS-07, AS-09

Spec: [spec.md](../spec.md). Raised by GK A on the AS-09 review (#983).

## Scope

AS-09's tier split takes a smoothing store's latency from `services.adequacy.spectrum.store_latency(n, name, override)`. It reads, in order, a given override, the store's own `control` column, and the `storage_technology`'s assumed control. AS-07's sheet (`control_settings.tiers_from_solved`) reads only the `control` column and falls back to grid following (0.2 s). So with no `control` column, the sheet flags a high-C BESS that AS-09 put in the tier at 0.02 s. The sheet should call `store_latency` so the two cannot drift.

While there: a trace-sourced oscillation (`source: "trace"`, from `spectrum.replace_oscillation`) carries an energy-weighted period T, and `frequency_hz = 1/T` sits below the trace's f_c on a broadband trace. When the source is "trace", the sheet's latency band should check against the trace's f_c, not 1/period_s.

## Acceptance

- [x] Tests first: a split store with no `control` column gets the same latency on the sheet as in AS-09's `tier_split`.
- [x] A trace-sourced oscillation's latency check uses the trace's f_c.

## PRs

## Comments

### 2026-11-11: triage

Checked both acceptance items on master: (1) `services/control_settings.py:48` imports `store_latency` and `tiers_from_solved` fills `latency_s` and `latency_basis` from it (line ~277); `tests/test_control_settings*.py:308` asserts the sheet equals `S.store_latency(n, "fast")[0]` and AS-09's `tier_split`. (2) The sheet's latency band uses `max(1/T, f_c_hz)` for a trace-sourced oscillation (lines 111-122). Both boxes were already ticked. The Status said ready-for-human; nothing is left for a human. Verdict: done.
