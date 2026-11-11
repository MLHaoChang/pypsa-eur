<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# UX follow-ups from the 10 Nov reviews

Status: done (#1381)
Type: polish (wording, consistency)
Found by: UX (15) and UX (16) reviews on 2026-11-10, relayed by the coordinator.

## Items

- Microgrid ledger defaults (horizon, dates) have no Expert home (#1313).
- The Expert EditCard table shows raw `s_nom` where Guided says "Rating" (#1325).
- The Guided grid-study jump shows two Back controls (#1328).
- `ic_counterfactual.reason_words` joins with "; ", not " · " (#1333).

## Already owned elsewhere

- Results header says "optimal" / "period all" / "window …T00:00" while chart source lines say "solved" / "all periods" (#1336), and Build KPI tiles say "(annualised)" while charts say "per year": both are in ui-modern 03 slice 2.
- `.scratch/live-copilot/issues/44` is done by #1325 (Status line updated here).

## Comments

### 2026-11-11: triage

Checked on master: `ic_counterfactual.reason_words` still joins with "; " (`services/ic_counterfactual.py:119`). The other three items are UI wording and were not found fixed. live-copilot 44 is `done (#1325)`. Verdict: ready-for-agent, one small PR.

- Blocked by: none.
- Likely files: `services/ic_counterfactual.py` (+ its tests), the Expert EditCard table (`frontend/src/components/EditCard*`), the Guided grid-study jump, the microgrid ledger defaults view. Reviewer: UX.

Acceptance:
- [x] `reason_words` joins with " · "; its test says so.
- [x] The Expert EditCard table reads "Rating" where Guided does, with the unit.
- [x] The Guided grid-study jump shows one Back control.
- [x] Microgrid ledger horizon and dates have an Expert home, or the file says why they stay Guided-only.

### 2026-11-11: built

- `reason_words` joins with " · " (`test_reason_words_joins_reasons_with_a_middle_dot`).
- Expert EditCard shows the field label ("Rating"); the raw field (`s_nom`) is the cell's tooltip and the Unit column already carries "MVA".
- `FullPageTab` hides the header "Back to Results" when the jump set its own `flowReturn`, so the floating "Back to <origin>" is the only Back.
- Microgrid ledger "single-year horizon" and "dates": they already file to section 1 (`flow_service._LEDGER_SECTIONS`) and are read-only descriptions of how the template combines inputs that have Expert homes: the modelled week and years in Model Horizon, financial close, COD per asset and analysis years in Results › Investment case › finance inputs. No new Expert field, because no stored input exists behind the row; no engine change.
