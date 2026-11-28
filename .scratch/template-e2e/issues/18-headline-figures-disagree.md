<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 18 — Cost and capacity headline figures disagree or are missing

Status: ready-for-agent
Impact: high (raised 2026-11-20, UX 3 review of #1777)
Templates: DC staged, H2 staged, microgrid (both), ammonia, DC
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

LOPF toast "Solved · €120.29 M" vs Summary "€195 M" (H2 staged); €382.67M vs €576M (DC staged), no basis stated. Summary "No cost breakdown / No capacities for this solve" and "Loading the headline figures…" stuck (microgrid staged, ammonia). Off-grid microgrid lists "grid 100 MW" in Key capacities; DC lists "grid 500 MW" against a 40/55 MW connection. Ammonia: delivered 785 kt exceeds made 672 kt, unexplained. Study card: "Achieved 0.00‱" beside "LOLE 4.58 h/yr Not certified".

Mode-by-mode figures, same H2 project, same state (template walk 2026-11-20, `walkthroughs/templates-2026-11-20/`): Guided Results 'Yearly cost of this design (before any shortfall costs)' EUR 7.61 M; Expert Run LOPF > Results > Summary 'Total system cost' EUR 9.02 M (new-build capital EUR 6.32 M per year), status bar 'Obj: € 9 m'; the report's cost at target 7,608,649 EUR. DC and microgrid agree across modes (34.7 M and 3.47 M).

## Why it matters

Compare and decide depend on numbers that contradict each other.

## Suggested fix

One cost number with a stated basis (NPV or LP objective); fix empty summary; hide irrelevant assets.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Still true: `HeadlineStrip.tsx` still has the 'Loading the headline figures' state. Acceptance: every cost figure states its basis (LP objective or NPV) wherever it shows; the toast and the Summary agree or say why they differ; the empty-summary and stuck-loading states are fixed; assets that do not apply to the template (grid on an off-grid microgrid) are not listed in Key capacities. Gated: GK A and UX. Blocked by: none.

## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): H2: Guided 'Yearly cost' EUR 7.61 M vs Expert Run LOPF Summary 'Total system cost' EUR 9.02 M (status bar 'Obj: € 9 m'), no basis stated on either. DC (34.7 M) and microgrid (3.47 M) agree.

- Seen again 2026-11-21 (template walk, master 6b421b06): Ammonia (`eh_ammonia_staged`): Guided "Cost over the plan, 2027–2035 (discounted, before any shortfall costs)" EUR 116 M; Expert Results Summary "Total system cost" EUR 188 M (capital 92.9 M, operating 95.5 M); status bar "Obj: € 120 m". The Guided label names a discounted basis, the Expert card says only "in this solve". Sizes agree (electrolyser_new_2030 18.4 MW, electrolyser_new_2035 10.5 MW, h2_storage 22.6 MWh, nh3_tank 446.9 MWh). IPP agrees (Guided and Expert -4.55 M). Concentrator: no Guided figure (run not finished).

## Notes (2026-11-28, te 18 build)

- Run buttons (coordinator answer A): in Expert, when a project has a study, the header's Run is the study (TE2E-17-RUN-1). The Summary's "Run the solve ›" and the out-of-date banner's "Run the solve" run the dispatch solve themselves (`requestDispatchSolve`). The solve toast names its figure the way the status bar does ("LP objective €X (discounted to 2027)", "System cost €X").
- Follow-up B (not in this issue): when there is no LOPF solve, the results routes could read the study's own solve instead of showing "No cost breakdown / No capacities". Left out because it changes which network the results routes read. That is engine-side work for a GK B plan.
- Follow-up (logging, UX 34): move the Summary section's cost formatting from `fmtCurrency` onto `utils/money.ts` `money()`, so the Summary and Guided format money the same way.
- "Loading the headline figures…" stuck: the te 18 build session (session_01G7qCummqEbKc27vW74JbWt) could not reproduce it on microgrid staged or ammonia. The empty-summary half is covered by the breakdown-unreadable sentence. Reopen with a project file if it is seen again.
- Outside the acceptance and still open: ammonia delivered > made (785 kt vs 672 kt), and "Achieved 0.00‱" on the study card.
