<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 18 — P–V/Q–V margin as a grid-readiness row and gate fact (G7a wiring)

Status: needs-triage
Type: task
Blocked by: none (10 lists the gate as deferred until this lands; 10 is not blocked by it)
Gated: yes (GK A reviews the plan and the PR)
Model tier: opus
Owner decisions: SAGC-1 (G7a), D3-KEEP, SH-1
Plan rulings: GK A, 28 Oct, on PR #834 (folded in below)
Design: [design.md](../design.md) §9.3 "eh_datacenter" (P–V/Q–V, deferred), §11; the module's own spec, `.scratch/datacenter-grid-readiness/issues/14-pv-qv-voltage-stability.md` (done, PR #714)

**Confidentiality (SAGC-1).** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

`gridspine/static/voltage_stability.py` (DC 14) computes the P–V nose and the Q–V margin at a PoC, intact and over the worst N-1 cases, every row labelled `SCREENING_BASIS`. Nothing outside `tests/gridspine/` imports it: no campus-run stage runs it, no grid-readiness row reads it, so the data-centre profile cannot gate on it (design §9.3 lists G7a as deferred).

## What the code gives today (read before planning)

- `voltage_stability(net, poc_bus, planned_mw, contingencies=None, settings=None) -> (rows, summary)`. It adds a probe load at `poc_bus` (`capacity.apply_connection`) and steps it, so `poc_bus` must sit **behind an impedance** from the slack. On the prepared campus the `ext_grid` sits on the PCC bus itself (`harmonic_scan.campus_cases` reads `s_sc_min_mva`/`rx_min` there), so calling it on `campus.net` at the PCC never finds a nose.
- Each row: `case`, `verdict` (`solved`/`not_solved`), `nose_mw`, `nose_is_lower_bound`, `margin_mw`, `margin_pct`, `nose_below_planned`, `q_margin_mvar`, `qmin_at_sweep_edge`, the retry tallies and `basis`. The module carries **no limit and no pass/fail**; the summary carries `VOLTAGE_STABILITY_LEDGER`.
- The campus run already has the stage pattern to copy: `harmonic_scan` in `services/campus_electrical_service.py` (`RUN_STAGES`, `_harmonic_scan`, `_harmonic_scan_results`), driven by `gridspine/drivers/campus_study.harmonic_scan_campus`. A screen beside the sizing never fails the run: its error goes to its own error file.
- The PCC's P and Q per `(period, hour, case)` are in `campus_lf_pcc.csv` (`campus_study.LF_PCC_CSV`).
- Grid-readiness rows are built in `services/adequacy/grid_readiness.py` (`_row`, `RULE_*`, `_limit_from`, the list in the scorecard builder near `:1278`); `_Inputs` already reads the campus folder (`campus_electrical/island_study.json`).
- Profile keys live in `gridspine/templates/grid_codes.py` (`_SINGLE_LIMITS`, `PROFILE_KEYS`, the numeric checks) and the editor's list in `services/campus_grid_code_service.py:504`.

## What to build

1. **Driver** `campus_study.voltage_stability_campus(run_dir, profile, profile_dirs)` in `gridspine/drivers/campus_study.py`:
   - builds the grid-side screening net: a source bus behind the Thevenin impedance Z = c·Un²/S_k''min, with c = c_min from `island_sc_seq.c_factor(vn, "min")` (1.0 above 1 kV, the standing rule, as in #827) and R/X from `pcc.rx_min`; the PCC is the PoC bus. The source EMF is 1.0 pu, or the PCC's `vm_pu` if that is lower; the ledger says which was used;
   - `planned_mw` per period is the larger of (i) the largest converged PCC import in `campus_lf_pcc.csv` and (ii) the connection's rated import when one is stated; the note names which one bound (a probe below the contracted import would overstate the margin). The probe's power factor is that hour's P/Q (lagging; a leading or zero-Q hour falls back to `VoltageStabilitySettings().pf` and says so in the notes);
   - intact only, one case on S_k''min: S_k''min is the user's weakest grid state, so the intact margin on the equivalent is the post-outage grid margin, and campus-internal outages do not change the grid-side nose at the PCC;
   - writes `campus_voltage_stability.csv` (one row per period) atomically, and returns the summary.
2. **Campus-run stage** `voltage_stability` after `harmonic_scan` in `RUN_STAGES`, as `_harmonic_scan` is: its meta in `campus_voltage_stability.json` (limits, their source, notes, ledger, `SCREENING_BASIS`), its failure in `voltage_stability_error.json`, never a failed run. `get_state` returns it beside `harmonic_scan`.
3. **Profile keys** (optional, numeric) in `grid_codes.py` and the profile editor: `pv_margin_min_pct` (positive) and `qv_margin_min_mvar` (≥ 0). No key that no row reads. D3-KEEP: no profile ships values; the user enters them from their grid code.
4. **Grid-readiness rows** `voltage_stability_pv` and `voltage_stability_qv` in `grid_readiness.py`, section `connection`:
   - limit through `_limit_from` (the user's limit, else the ledger row, else the generic placeholder marked assumed). Placeholders (GK A, 28 Oct): P–V real-power margin ≥ 5 % of `planned_mw`; Q–V reactive margin ≥ 0 Mvar, meaning the Q–V curve's minimum stays at or below zero injection at the operating voltage. Each placeholder's source reads "planning practice placeholder, not a requirement of this project's grid code". This issue asserts no SAGC clause: a figure in the user's own profile replaces the placeholder and the row cites the profile;
   - project value the worst `margin_pct` (P–V) or `q_margin_mvar` (Q–V) over periods, with the period named in the note;
   - verdict by a new `RULE_PV`/`RULE_QV` (`>=`, `otherwise: warn`): screening, so below the limit warns and never fails;
   - a `nose_is_lower_bound` (or `qmin_at_sweep_edge`) value at or above the limit reads `pass`, shown as "≥ x"; one below the limit reads `unknown` ("the sweep did not reach the nose"), never `warn`, since a bound below the limit says nothing about the margin;
   - `unknown` when no run, an error file, a `not_solved` intact case, or no S_k''min entered, each with its reason;
   - every row `assumed` and its note reads "screening; binding answer is the RMS study". Wording: "within the screening margin", never "meets" a code.
5. **Gate fact** for issue 10: `dc.readiness.voltage_stability_pv` and `dc.readiness.voltage_stability_qv` are ordinary readiness rows, so issue 10's row-id list gains them as `warn` (a margin is screening). Update issue 10's acceptance line on P–V/Q–V in the same PR that lands this.
6. **Off-grid.** On an `off_grid` archetype both rows are excluded with the reason, as the S_k''-built rows are (issue 12): with the tie open there is no grid infeed to collapse against.

## Acceptance criteria

- [ ] A campus run on `eh_datacenter` writes `campus_voltage_stability.csv` and its meta; the scorecard shows both rows with a margin, a limit and its source.
- [ ] On the equivalent held to R = 0 (rx = 0) the nose matches the analytic P_max = E² cos φ / (2X (1 + sin φ)) within 1 % (the DC 14 formula, X = c_min·Un²/S_k''min). With rx_min > 0 the nose is only checked to be finite and below the R = 0 value.
- [ ] `planned_mw` is the larger of the LF peak import and the rated import, and the note names which bound.
- [ ] A lower-bound nose below the limit reads `unknown` with "the sweep did not reach the nose"; one at or above reads `pass` shown as "≥ x" (same for `qmin_at_sweep_edge`).
- [ ] No S_k'' min entered → both rows `unknown` with the reason; the run still succeeds.
- [ ] A stage exception writes the error file, the run succeeds, the rows read `unknown` with the error.
- [ ] Below the limit → `warn`, never `fail`; no row text says "compliant" or "meets".
- [ ] With no user limit the rows show the placeholders (5 %, 0 Mvar) with the placeholder wording; a user limit in the profile replaces them and the row cites the profile.
- [ ] `off_grid` → both rows excluded with the reason (test).
- [ ] `tests/gridspine/test_sixty_hz.py` stays green (no 50 Hz literal added).

## Test plan

- `tests/gridspine/test_campus_voltage_stability.py` (new): the driver on a prepared demo campus; the R = 0 analytic-nose check and the rx > 0 finite-and-below check; c_min and the EMF choice in the ledger; the rated-import bound; the lagging-pf fallback note.
- `pypsa-gui/backend/tests/test_grid_readiness_voltage_stability.py` (new): rows from a written CSV/meta fixture for pass, warn, unknown (no run, error, `not_solved`, no S_k''min, lower bound below the limit), a bound at or above the limit passing as "≥ x", placeholders and user limit, `off_grid` exclusion.
- The campus-run stage list test (wherever `RUN_STAGES` is asserted) gains `voltage_stability`.
- `grid_codes.py` profile-key tests for the two new keys (range, optional, unknown key still warns).

## Likely files

`gridspine/drivers/campus_study.py`, `gridspine/templates/grid_codes.py`, `pypsa-gui/backend/services/campus_electrical_service.py`, `pypsa-gui/backend/services/campus_grid_code_service.py`, `pypsa-gui/backend/services/adequacy/grid_readiness.py`, the tests above, a release-note fragment.

## Comments
