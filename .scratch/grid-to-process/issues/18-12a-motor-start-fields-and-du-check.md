# 12a (issue 18): motor-start fields and the gridspine ΔU check

Status: ready-for-agent (owner G2P-12-SPLIT, 2026-10-28: split, 12a now on a fixture network)
Type: task
Blocked by: none
Gated: **yes** (gridspine: send GK A the plan before code)
Model tier: opus
Size: M

Split from [issue 12](12-minutes-scale-motor-start-restart.md) by the owner (G2P-12-SPLIT). It is numbered 18 because `frontier.py` reads only `NN-` file names. Read
[the design](../design.md) §4.3 item 1 first. 12b (the rest of 12: dips, restart sequence,
results, the B acceptance) stays in issue 12 and waits for 10.

## Agent brief

**Category:** enhancement (gated gridspine)
**Summary:** motor-start fields on the process unit and a static ΔU check at the PoC against the grid code's rapid-voltage-change limit, built and tested on a fixture network (no B template needed).

**Desired behaviour:**
- **Process-unit fields:**
  - `motor_kw`;
  - `start_method` ∈ {`dol`, `soft`, `vfd`}, with a default start-current multiple of 6 / 3 / 1 that a per-unit field overrides.

  These follow 17a's pattern: the field list, the Link-form reveal under `eh_process`, the attrs doc and the inventory. An unset field means "no input": the check reads unknown, never pass.
- **A gridspine static check** beside `static/capacity.py` and `static/flicker.py` (GK A, 2026-10-28):
  - **Source strength.** S_sc = S_k''/c, i.e. Z = c·Un²/S_k'' (the standing c-factor rule). Judge on the PoC's **minimum** short-circuit case when it has one; else use the entered S_k'' with c, plus a note saying so. Reuse flicker's S_k *lookup* (`_sk_of`) only, not its "S_k as is" ledger line.
  - **Formula.** ΔU% ≈ (S_start·sin φ_start − Q_gfm) / S_sc × 100.
    - S_start = √3·U·I_start from the start method.
    - The starting power factor is ledgered per start method (demo, assumed).
    - Q_gfm is the grid-forming storage's available reactive power at that PoC.
    - If Q_gfm cannot be stated from the network, set Q_gfm = 0 (conservative) and say so in the row.
  - **Limit.** Read the profile's existing rapid-voltage-change key (`rvc_limit_pct`, as the scorecard's `rapid_voltage_change` rows do). Add no new profile key. A shipped demo 3 % carries the clause "planning level, not an emission limit allocated to this installation". The project's grid code governs.
  - **Scorecard.** One row per unit and start method on the grid-readiness scorecard. This changes the row count, so the PR updates the pilot-evaluation golden (`not_checked` count in `pypsa-gui/backend/tests/test_pilot_evaluation.py`) and names that update.

**Acceptance (on a fixture network: one PoC at 150 MVA, one process unit with a motor):**
- [ ] Hand-calculated: a DOL start of the fixture motor's kW at its ledgered start pf on 150 MVA (c applied) gives ΔU = the hand value in % (written in the test) > the limit, so it fails. The VFD start's hand value is ≤ the limit, so it passes.
- [ ] Q_gfm = 0 and Q_gfm > 0 give the hand values of the formula above.
- [ ] The minimum short-circuit case is used when the PoC has one. Without one, the row notes that the entered S_k'' was used.
- [ ] A unit with no motor fields reads unknown on the scorecard, not pass.
- [ ] The assistant inventory and the attrs-doc compact cap test pass. If the cap is exceeded, ask the owner, as G2P-17A-DOCCAP did; do not raise it unasked.

**Out of scope:** ride-through curves, dip statistics, the restart sequence and results (12b, issue 12); the B template's motor-start check (12b, after 10); dynamic (RMS/EMT) motor start.

**Likely files:** `pypsa-gui/backend/models/process_unit.py`, `pypsa-gui/backend/services/asset_schema/fields.py`, `pypsa-gui/backend/services/asset_schema/attrs_doc.py`, new `gridspine/static/motor_start.py`, `gridspine/static/flicker.py` (shared S_k lookup only), `pypsa-gui/backend/services/adequacy/grid_readiness.py`, `pypsa-gui/backend/tests/test_pilot_evaluation.py`, the Link form under `pypsa-gui/frontend/src/`, tests under `tests/gridspine/` and `pypsa-gui/backend/tests/`.
**Blocked by:** none.
**Model:** Opus (gridspine, gated GK A). The Link-form reveal needs the UX reviewer's OK.
