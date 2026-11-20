<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 — IPP run retires both existing plants and reports a negative cost

Status: ready-for-agent
Impact: high
Templates: eh_ipp_hybrid
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

"2 assets retired · saves €0.00" (wind_farm 60 MW and pv_plant 41.7 MW to 0), "Total system cost -€4.55 M", Key capacities "No capacities for this solve". Readiness warning shows the raw id "commercial.arbitrage_loop_via_storage" (import -31.44, export 184.46 per MWh). Guided says "sized by the study".

## Why it matters

The user's owned plants vanish from the result; a profit is labelled a cost.

## Suggested fix

Do not retire fixed plants; label a profit as profit; plain-language arbitrage warning.

## Triage (2026-11-19, master 9f850d36)

Checked by code reading only; the app was not re-run. `commercial/preflight.py` still emits the raw `commercial.arbitrage_loop_via_storage` id. Acceptance: an existing fixed plant is never retired by the run; a negative total cost reads as a profit; the arbitrage readiness warning is plain words with the import and export prices; a test on the IPP template. Gated: GK A. Blocked by: none.

## Comments

- 2026-11-20, builder (session_01G7qCujGZJwSJnXomzhsDbG): reproduced through the app routes. The LP keeps wind_farm 60 MW and pv_plant 41.7 MW (p_nom_min = p_nom_max), and they stay at that size after save and reopen. Before a solve, /network/generators serves p_nom_opt = 0; CapacityExpansion counts extendable p_nom_opt < p_nom as retired without a solved or p_nom_min check. holdWords says "sized by the study" for any extendable asset. The raw id came from issue_title()'s fallback.
- 2026-11-20, GK B2 (3) plan GO, two slices. PR 1: (a) the title test fails first; (b) an exact-set test pins ISSUE_TITLES against the commercial.* ids the engine raises; (c) the live IPP test checks p_nom_opt == p_nom after the solve and after save and reopen. PR 2: (d) retired ⇔ solved ∧ extendable ∧ p_nom_opt < p_nom − eps ∧ p_nom_min < p_nom − eps, keyed on the page's solved flag, with a test for each part (unsolved, pinned, p_nom_min = 0 solved retirement, p_nom_min = 0 unsolved); (e) no "Net profit" for a negative objective. Use "Total system cost −€X (revenue exceeds cost)" or wording UX 2 picks that avoids profit and NPV; (f) holdWords reads "fixed at X MW" when p_nom_max is finite and |p_nom_max − p_nom_min| ≤ eps, and keeps "sized by the study" when p_nom_max = inf, with a test for each.
