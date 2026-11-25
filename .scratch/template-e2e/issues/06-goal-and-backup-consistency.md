<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Reliability goal shows "3 h/yr (assumed)" and "No goal yet" at once; backup inputs grouped under Scenarios in Expert only

Status: ready-for-agent; audit 2026-11-25: #1645 merged (backend refuses a run with no VOLL), #1735 merged (goal editor Assumed chip); left: backup and control inputs with section 5 as their single home
Owner: builder session_01G7qCuo15kNsNqjAaBLjvmL (backend first; reliability_goal.py after #1604)
Size: S
Gated: UX; GK B (goal value source)
Likely files: pypsa-gui/frontend/src/pages/hubDesign/cards/GoalCard.tsx; pypsa-gui/frontend/src/components/reliabilityGoal/ReliabilityGoalEditor.tsx; pypsa-gui/backend/services/reliability_goal.py (~l.87-108, pack value fallback); pypsa-gui/frontend/src/pages/SolverSettings.tsx (~l.1595)
Journey fit: set up the question (section 8) → run → results header ("the reliability goal" is shown in the Reliability results header, §2.2).

## What

- On eh_datacenter and eh_microgrid, Guided section 8's assumption list says "Reliability goal (LOLE): 3 h/yr (assumed)". The goal editor on the same card reads "Allowed shortfall (hours per year)" **empty**, with the hint "No goal yet". The run uses the pack's 3 h/yr (reliability_goal.py falls back to the pack value). The user cannot tell which value applies. §2.3 makes the DC goal **R**.
- eh_concentrator_staged has no goal ("not known yet"). It runs the reliability study all the same, and the run gate does not ask.
- Backend VOLL defaults to 0.0 when missing (copt_endpoint.py:54). Only the frontend blocks Run on a missing VOLL, so the assistant or API path is not protected.
- EXPERT-GUIDED-1: Expert Scenarios → "Grid study" holds the DC backup values (sustained ride-through, UPS bridge, fuel autonomy, genset N+k) and the converter control modes. §2.1 puts backup mode and control presets in section 5. Guided shows neither after creation.

## Acceptance

- The goal editor shows the value the run will use, labelled "assumed" while it comes from the template, in both modes.
- A template without a goal either gets one marked assumed or section 8 reads `incomplete`, per §2.3. (H2 hub: "the run asks".)
- The backend refuses a run with no VOLL as the frontend does.
- Backup and control-mode inputs live in section 5 in both modes. Section 8 may show them read-only with a link.
- (UX) The goal editor shows the effective value with an "Assumed" chip until the user sets it.
- (UX) Backup and control inputs have one editing home, section 5. Section 8 shows a read-only copy with a `withReturn` link that requests the section, then scrolls to the field.

## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): H2 Guided Goal card still reads 'No goal yet' (no default goal); DC and microgrid show 'No goal yet' in the field while the Assumptions row says '3 h/yr (assumed)'. The verdict text now correctly names 'section 8, Scenarios & optimisation'.

- Seen again 2026-11-21 (template walk, master 6b421b06): Ammonia: verdict "No reliability goal is set for this site, so the study did not certify it", with a Set a goal button; Guided cost shown. Concentrator and IPP: Reliability not offered or not reached.

- 2026-11-23 status sweep: landed in #1645 (41318c60); #1735 (ae6ea750): goal editor shows the assumed value with the Assumed chip; the backend refuses a run with no VOLL. Remains: backup inputs having section 5 as their single home.

## Audit (2026-11-25)

- 2026-11-25 audit: PR merge state checked against origin/master log; acceptance items grepped on master code.
