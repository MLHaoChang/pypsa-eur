<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# HV-D: decision and robustness advisor

Status: proposed; no new scientific calculations or generic ranking delivered here.
Owning work: native study/adequacy/commercial engines, LC-18/S6, LC-20/LC-21, per-card W10 results and S7 checked explanation.

## User need and experience

“Which design would you choose, and what could change that answer?” is more useful than another chart summary. The advisor should state the decision criterion, identify feasible/comparable options, show a qualified preference only when the native evidence supports it, and expose key assumptions and missing assessments.

Use the card's existing result-explanation action, opening one companion conversation. A compact native comparison displays: base case, compare cases, declared changes, objective and hard constraints, common basis, principal trade-offs, sensitivity evidence, unresolved gaps. The user can follow any fact to its native result/source. “No defensible preference yet” is a successful response when evidence is insufficient. Do not add an extra ranking dashboard or AI confidence badge.

## Functional requirements

1. **Define the decision.** Ask whether the user is minimizing cost subject to a reliability target, comparing investment value, limiting grid constraints, or another supported objective. Separate preferences from constraints. Unknown weights/probabilities/risk appetite cannot be guessed. Show card scope: an IPP commercial study does not silently enable Control; a microgrid frequency question cannot be answered by an adequacy pass.
2. **Build comparable evidence.** Match owner/project permissions and admitted cross-case sources; horizon, stages, snapshot weights, demand/critical-service boundary, units/currency/price year/discount basis, scenario changes, solver mode and fidelity. Some declared design changes are intentional; record them rather than requiring identical inputs. Enforce the actual native comparison contract, including digest and artifacts. Reject incompatible windows, missing baselines or corrupt sections instead of showing deltas.
3. **Compute in native engines only.** Reuse exact existing pairwise comparison or study calculations. BESS-at-site findings, including battery incremental benefit versus PV-only reference and its zero-size rule, are not a generic plant finance verdict. Cost-versus-availability frontier is only meaningful in the native adequacy domain and definitions. A new multi-card rank, Pareto construction, break-even, probability, value-of-information calculation or objective weighting requires a validated native adapter and independent numerical oracle before narrative uses it.
4. **Respect feasibility and failure.** Cost savings do not compensate for a violated hard constraint. Infeasible, incomplete, stale or unavailable scenarios remain visible with reasons but are not ranked as successful cheap options. Linear branch loading, AC convergence, campus advisory checks, contingency screening and dynamic validation are different claims. “No recorded violation in this run” cannot become certified compliance.
5. **Explain robustness within bounds.** Use explicit user or sourced assumption ranges with units/basis. State whether the native sensitivity re-dispatches, re-optimizes sizing, or only revalues fixed decisions. Show a sign flip or crossing only where computed; outside the tested range is unknown. Never invent distributions from min/max values, a probability of success, or independent uncertainty where correlation is unmodelled. No hidden scenario runs or forced sweep when the user only asks a question.
6. **Surface plausibility and evidence gaps.** Fold in LC-20 native rules as they land, including rule IDs, source, threshold basis, evaluated/not-evaluated state and native verdict. Do not resurrect removed stub tool names. An unexpected sensitivity sign is a review cue, not automatically a bug: first establish common assumptions/constraints and whether sizing/dispatch changed. Native rule success does not certify the engineering model.
7. **Publish bounded prose.** Admit at most 12 source facts under the existing explanation contract. Tie numerical and comparative claims to fact IDs and supported calculation IDs, not merely matching digit strings. Units, sign, time and alternative attribution must agree. Withhold unsupported numerical claims and causal explanations. Reuse report number audit as one check, plus semantic/source/basis checks; strict publication refuses on disagreement. Historical exports remain historical.
8. **Make next work optional and inspectable.** Explain which missing input or assessment could affect the decision and why, without fabricating a dollar value of information. A proposal for more studies fills the existing composer/experiment preview, with existing case/edit caps and user ranges. It never starts a solve automatically.

## Evidence map instead of false confidence

Display separate source-linked dimensions: (a) input/source provenance, (b) completeness of the inputs needed for this question, (c) selected model scope/fidelity and exclusions, (d) freshness and comparability, (e) native verification and independent/domain validation status. Each is established / partial / unavailable / outside scope with a reason. These are proposed presentation semantics mapped to native records, not replacements for native solver statuses.

Do not collapse the dimensions into a percentage or count of green checks. A verified calculation can still have weak assumptions; complete inputs can still use a screening model. Preserve independent domain review and real-use calibration as pending unless actual review evidence is supplied.

## Slices and demonstration

- HV-D1 (LC-18/S6/W10): evidence/basis comparison and coverage map using admitted A/B sources. Base + N is a dependency, not assumed shipped.
- HV-D2 (native engines/LC-20): one supported investment or adequacy robustness explanation with independent numeric fixtures; extend domain by domain. Do not generalize BESS verdicts.
- HV-D3 (S7/LC-21): source-bounded recommendation, refusal, optional proposed next study and report coverage. No new standalone scenario-preview module.

Tests D01–D16 include counterexamples, omitted scope, misleading equal numbers, incomparable scenarios and infeasible low-cost options. Product evaluation must measure whether users understand the limits, not merely prefer the assistant's confident wording.
