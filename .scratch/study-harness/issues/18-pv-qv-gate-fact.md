<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 18 — P–V/Q–V margin as a grid-readiness row and gate fact (G7a wiring)

Status: needs-triage
Type: task
Blocked by: none (10 lists the gate as deferred until this lands; 10 is not blocked by it)
Gated: yes (GK A reviews the plan and the PR)
Model tier: opus
Owner decisions: SAGC-1 (G7a), D3-KEEP, SH-1
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
   - builds the grid-side screening net: a source bus (the PCC's `vm_pu`) behind the Thevenin impedance from `pcc.sk_min_mva` and `rx_min` at the PCC voltage (the weakest grid is the binding case), the PCC as the PoC bus;
   - `planned_mw` per period is the largest converged PCC import in `campus_lf_pcc.csv`, and the probe's power factor is that hour's P/Q (lagging; a leading or zero-Q hour falls back to `VoltageStabilitySettings().pf` and says so in the notes);
   - intact only on the single-infeed equivalent (no grid branches to outage); the plan says whether a campus-internal N-1 adds anything and, if it does, uses `contingency_set.branch_contingencies` on the campus net with the PoC moved behind the equivalent;
   - writes `campus_voltage_stability.csv` (one row per period and case) atomically, and returns the summary.
2. **Campus-run stage** `voltage_stability` after `harmonic_scan` in `RUN_STAGES`, as `_harmonic_scan` is: its meta in `campus_voltage_stability.json` (limits, their source, notes, ledger, `SCREENING_BASIS`), its failure in `voltage_stability_error.json`, never a failed run. `get_state` returns it beside `harmonic_scan`.
3. **Profile keys** (optional, numeric, positive) in `grid_codes.py` and the profile editor: `pv_margin_min_pct` (intact), `pv_margin_min_n1_pct` (after an outage) and `qv_margin_min_mvar`. D3-KEEP: no profile ships values; the user enters them from their grid code.
4. **Grid-readiness rows** `voltage_stability_pv` and `voltage_stability_qv` in `grid_readiness.py`, section `connection`:
   - limit through `_limit_from` (the user's limit, else the ledger row, else a generic placeholder marked assumed). The placeholder is proposed in the plan from generic planning practice and ruled by GK A; this issue asserts no SAGC clause. If the user's uploaded SAGC profile carries a figure, its clause is cited from the profile, never from this repo;
   - project value the worst `margin_pct` (P–V) or `q_margin_mvar` (Q–V) over periods and cases, with the case named in the note;
   - verdict by a new `RULE_PV`/`RULE_QV` (`>=`, `otherwise: warn`): screening, so below the limit warns and never fails;
   - `unknown` when no run, an error file, a `not_solved` intact case, or no S_k'' min entered, each with its reason; a `nose_is_lower_bound` or `qmin_at_sweep_edge` value is shown as a bound ("≥ …") and still judged;
   - every row `assumed` and its note reads "screening; binding answer is the RMS study". Wording: "within the screening margin", never "meets" a code.
5. **Gate fact** for issue 10: `dc.readiness.voltage_stability_pv` and `dc.readiness.voltage_stability_qv` are ordinary readiness rows, so issue 10's row-id list gains them as `warn` (a margin is screening). Update issue 10's acceptance line on P–V/Q–V in the same PR that lands this.
6. **Off-grid.** On an `off_grid` archetype both rows are excluded with the reason, as the S_k''-built rows are (issue 12): with the tie open there is no grid infeed to collapse against.

## Acceptance criteria

- [ ] A campus run on `eh_datacenter` writes `campus_voltage_stability.csv` and its meta; the scorecard shows both rows with a margin, a limit and its source.
- [ ] On the single-infeed equivalent the nose matches the analytic P_max = V² cos φ / (2X (1 + sin φ)) within 1 % (the DC 14 formula, X from S_k'' min).
- [ ] No S_k'' min entered → both rows `unknown` with the reason; the run still succeeds.
- [ ] A stage exception writes the error file, the run succeeds, the rows read `unknown` with the error.
- [ ] Below the limit → `warn`, never `fail`; no row text says "compliant" or "meets".
- [ ] A user limit in the profile replaces the placeholder and the row cites the profile.
- [ ] `off_grid` → both rows excluded with the reason (test).
- [ ] `tests/gridspine/test_sixty_hz.py` stays green (no 50 Hz literal added).

## Test plan

- `tests/gridspine/test_campus_voltage_stability.py` (new): the driver on a prepared demo campus; the analytic-nose check on the equivalent; the lagging-pf fallback note.
- `pypsa-gui/backend/tests/test_grid_readiness_voltage_stability.py` (new): rows from a written CSV/meta fixture for pass, warn, unknown (no run, error, `not_solved`, no S_k''), bound display, user limit, `off_grid` exclusion.
- The campus-run stage list test (wherever `RUN_STAGES` is asserted) gains `voltage_stability`.
- `grid_codes.py` profile-key tests for the three new keys (positive, optional, unknown key still warns).

## Likely files

`gridspine/drivers/campus_study.py`, `gridspine/templates/grid_codes.py`, `pypsa-gui/backend/services/campus_electrical_service.py`, `pypsa-gui/backend/services/campus_grid_code_service.py`, `pypsa-gui/backend/services/adequacy/grid_readiness.py`, the tests above, a release-note fragment.

## Comments
