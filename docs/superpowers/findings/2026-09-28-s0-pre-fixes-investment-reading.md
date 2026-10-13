# S0: three things that misled an investment reading of the expert view

**Status:** FIXED on `claude/edge-tool-ux-research-n0n2l6` (commits `5203809`, `b9288f8`). Gate: NO-GO on the first commit (two blockers, both real), re-gate requested on the second.
**Date:** 2026-09-28
**Plan:** `docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md` § S0
**Spec:** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md`

## The gap, as verified on master

1. **Every `Capital cost` badge named an upfront unit for an annual field.** The Properties panel showed `€/MW` (Store `€/MWh`, Line and Transformer `€/MVA`) beside a field whose own tooltip says `€/MW/yr`, and the `Overnight cost` row beside it carried the same badge. Five creation forms (electrolyser, fuel cell, power-to-heat, CHP, thermal storage) and the quick-add label had the same badge; the bottom-panel column labels said `CC ($/MW)`.
2. **The generator creation form priced in dollars** (`$/MWh`, `$/MW`) while every other surface is in EUR.
3. **The zero-profit-by-construction caveat reached only the model.** "An extendable asset at an interior optimum earns approximately zero net profit BY CONSTRUCTION" lived in `chat_tools._reading_notes`, which the copilot sees and the user does not; the Economics tab and Asset Detail showed the near-zero net profit bare.
4. **Two loop panels rendered the literal placeholder `max_solves`** in their pre-run cost sentence.

## What changed

| Where | Change |
|---|---|
| `services/results/sizing.py` (new) | `classify_sizing`, `BINDING_EXPLANATIONS`, `finite`, `is_at` lifted out of `chat_tools`; one classifier for the chat tool and the two surfaces (byte-identical: the assessor ran old and new on 63,888 inputs with 0 differences) |
| `services/results/economics_caveats.py` (new) | `ZERO_PROFIT_BY_CONSTRUCTION`, one string; `interior_optimum_notes(n, names_by_class)` decides "solved" with `dispatch_status_detail(n)["state"] == "fresh"`, the predicate `explain_investment` uses, then classifies each named asset over the six sizeable classes |
| `services/results/asset_economics.py` | payload gains `reading_notes` |
| `services/asset_results/service.py` | the summary response gains `reading_notes` |
| `services/chat_tools.py` | imports the classifier and the string; no second copy |
| `frontend/src/pages/results/Economics.tsx`, `pages/results/asset/AssetSummary.tsx` | render the note beside Net profit |
| `frontend/src/layout/PropertiesPanel.tsx`, `CreationForm.tsx`, `BottomPanel.tsx` | every annuity badge carries `/yr`; EUR everywhere |
| `frontend/src/pages/results/LoopPanel.tsx`, `MarginLoopPanel.tsx` | `MAX_LOOP_SOLVES` renders; `tests/test_loop_solve_ceiling_parity.py` pins it to `coupling.MAX_LOOP_SOLVES` |
| `pages/results/__fixtures__/asset-economics.golden.json` | records the new key (the golden `gas` and `electrolyzer` are interior) |

## Evidence

- **Red first.** The badge test expected `€/MW/yr` and saw `€/MW`; the creation-form test saw `$/`; the Economics, Asset Summary and loop-panel tests found no note element or the literal `max_solves`; the backend test failed on `ImportError: cannot import name 'sizing'`.
- **Mutations.** Silencing the note turned exactly the "carries the note" tests red. Emitting the note for any extendable asset turned exactly the four ceiling/not-built tests red. Treating every network as solved turned exactly the unsolved and dispatch-cleared tests red. Nothing else moved in any of the three.
- **Suites after `b9288f8`.** Full GUI backend suite: exit 0, all passed. Full frontend suite: 180 files, 1985 tests passed. `tsc --noEmit` clean. Ruff adds no finding (two pre-existing in `chat_tools.py`).

## What the first gate caught, and why it was right

- The first commit's message claimed `test_chat_explain_investment.py` passed. It had passed before the final removal of two unused import aliases, not after: one test read `chat_tools._BINDING_EXPLANATIONS`, which the removal took away. Four failures. Corrected in `b9288f8`; the lesson is to re-run every named suite after the last edit, not after the last meaningful one.
- The first "solved" rule read the presence of a `p_nom_opt` column. PyPSA 1.1.2 carries that column on every network (default 0) and it survives `clear_dispatch` after an edit. The assessor reproduced two false notes on Asset Detail. Corrected by using dispatch freshness.
- The follow-up commit was made while the full suites were still running, at the user's standing instruction to keep the branch pushed. The suites then passed unchanged. Recorded here so the sequence is visible.

## Still open / deliberately not done

- The Economics chip does not name which assets are interior; on a mixed fleet the portfolio net profit is not near zero and the chip can read as applying to the total. Left for the Study verdict page (S5/S6), which reports per option.
- The Asset Detail XLSX export shows headline net profit without the caveat. Left for the report work (S7).
- Two pre-existing ruff findings in `chat_tools.py` (a docstring style and an aliased `TimeoutError`) are untouched.
