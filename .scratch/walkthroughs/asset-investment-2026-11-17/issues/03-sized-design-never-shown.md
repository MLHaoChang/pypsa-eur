<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The study sizes the new units but no screen shows the sizes

Status: ready-for-agent
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J1 Site set-up → sized design (Guided and Expert)
Severity: high
Send to: Guided results legibility; EH reference design panel (`pages/results/EhReferenceDesignPanel.tsx`)
Duplicate check: extends `.scratch/ux-walkthrough-2026-11/issues/01-guided-expert-result-mismatch.md` (#1618). That issue covers Expert Results reading "Not run yet" after a Guided run. This one is about the sized capacities, which neither mode shows. #1611 (Guided results headline, in flight) adds the verdict, NPV and LCOE, not the sizes.

## What happens

1. Create "Island Microgrid". The template has two candidates, `diesel_new` (0–12 MW) and `bess_new` (0–15 MW, 4 h). Guided › 5 Assets & sizing shows each as "Capacity: sized by the study".
2. Run the Guided study (9 › Run study, 20 s). It certifies and reports a yearly cost of €3.47 M.
3. Guided › 5 Assets & sizing still reads "Capacity: sized by the study" for both (`shots/assets-after-run.small.png`).
4. Section 10 shows the verdict, the cost and the biggest risk, but no capacities.
5. Show the engineering detail › Capacity expansion reads "Not run yet". Reliability › Energy Hub reference design lists the pipeline stage "sizing: ok" and no MW.
6. Expert › Results › Summary and Capacity expansion read "Not run yet".

The backend computes and stores the sizes: `st.sections["sizing"]` is `sizing_summary_from_network(network)` (`services/adequacy/eh_study.py:300-302`). The front end only renders the stage name (`EhReferenceDesignPanel.tsx:217`).

## Expected

The user's question in this group is "what should I build". After a run:

- Guided › 5 Assets & sizing shows each candidate's sized MW (and MWh for storage), marked as from the study.
- Section 10 lists the sized new units under the cost.
- The engineering detail renders the sizing section as a table: asset, built MW/MWh, bounds.
- Expert shows the same numbers.

## Breaks

WALK-E2E-1: "from site set-up to the sized design". EXPERT-GUIDED-1. review-ux.md: results are traceable to inputs. A cost of €3.47 M can't be checked against assets the user can't see.

## Done when

After the Island Microgrid run, the sized MW of `diesel_new` and `bess_new` appear in Guided step 5, in section 10 and in the engineering detail, and Expert shows the same numbers. A test renders the sizing section from a stored EH report.
