<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Value, priorities and implementation baseline

## Answer to the product question

The earlier three suggestions are good first investments in usability: explain the next step, explain a result, and suggest a small number of useful scenarios. They are not necessarily the three largest sources of total value. They mostly explain or propose; a user still assembles inputs, manages study execution, and decides what the evidence supports.

My next priorities, judging total benefit rather than lowest implementation cost, are:

| Priority | Capability | User outcome | Why it adds value beyond the first three | Relative effort / exposure |
|---|---|---|---|---|
| 1 | Goal-to-study copilot | Complete a supported, reviewed study from objective to evidence | Removes repeated navigation, setup coordination and job babysitting rather than only saying what to do | High; execution and recovery require native receipts |
| 2 | Decision study explanations and robustness | Understand which feasible option fits the objective, what changes the answer, and what is still unproved | Turns several results into a defensible decision instead of explaining each chart | High; domain-specific comparability and validated calculations |
| 3 | Data-to-model onboarding assistant | Turn user files into traceable, reviewed model inputs | Removes mapping, unit and time-basis work instead of merely identifying missing fields | Medium to high for structured files; higher for documents |

This ranking is a product judgment, not measured ROI, model-quality evidence, or a price estimate. For an importer with messy meter data, onboarding may have the largest benefit and can precede the other capabilities. For a first-time user, the original next-step explanation may remain the single most useful change. Validate the ranking with task completion, decision comprehension and error measurements before widening rollout.

The recommended portfolio is therefore the three quick wins **plus** these three deeper capabilities, delivered through the existing packages. Do not replace quick wins with an all-or-nothing autonomous system. One configured provider is enough to start; multiple models, reviewer agents or model voting are not prerequisites and do not prove correctness.

## Current code: reuse versus missing work

The evidence catalogue pins every file below to the assessed master. Code availability is narrower than the product promise.

| Capability | Existing foundation | Actual limit / missing integration |
|---|---|---|
| Study execution | `services/assistant_tasks.py`, `harness/continuation.py`, ordinary loop, job adapters and approvals | Tasks are 1–12 ordered ordinary tool steps with earlier-step references, not an arbitrary DAG. Controls, `ask_user`, and project-rebinding tools are excluded. A goal brief must compile only to admitted steps; unsupported steps remain blocked. Task reconciliation accepts caller-supplied results after confirmation; that does not independently prove an effect or engineering result |
| Bounded experiments | Experiment preflight, single/two-network-case execution, native submission receipts and reconciliation | Plan schema allows up to 8 cases; current execution is narrower. Do not interpret schema capacity as permission to execute 8 cases or commercial/control studies |
| Solve budgets | `services/adequacy/campaign.py` and existing engine/turn/session caps | Campaign budgets cover supported agent adequacy solves, not every engine or dollars. Monte Carlo charges zero LP solves because it samples dispatch, not zero time/CPU/model usage |
| Robustness | `services/study/findings.py`, tornado runner, assumptions ledger; adequacy sweeps/frontiers; native comparisons | BESS decision logic is specific to its supported question. There is no evidenced universal multi-card ranking, calibrated confidence score or generic multiobjective optimizer |
| Result confidence | Source-bound result readers, checked publication, native report evidence and number audit | Number-token agreement is not proof of causal correctness. LC-20 native plausibility rules and LC-21 general network reporting are planned work; do not call removed result-tool stubs |
| File onboarding | Owned bounded upload store, Library CSV/XLSX series parser, measured-load importer, native profile apply and QA | Uploading a PDF does not extract a valid asset model. Load and Library parsers have different time/coverage/repair semantics. Schema matching, per-cell lineage, reviewed cross-family mapping and document adapters remain work |
| Workspace | Existing six-card model and workflow packages | At the original baseline the card-workspace design was pending; #566 later merged the reviewed design. This spec follows it and does not assert all six workspaces/sub-options are already implemented |


Before publication, master advanced to `49791da3e1197f3872b9273d37ca97c47c57b13d` (#557). The complete incoming diff was reviewed: native flow tool description now exposes item titles/blocker detail, and a fixed-nameplate title is clearer. These do not change tool schemas or execution. Use the native title/detail in walkthroughs. There are no file overlaps with this spec; the baseline 161-test result is not relabeled as a run on the newer source. See native-evidence.json for the exact comparison.

## Reconcile with the current product decisions

Master's [six-card contract](../project-workflow/modular-cards.md) has Capacity expansion, AC power flow & grid, Control, Reliability, Commercial & finance, and Hand-off. There are at most three sub-options per card. The five study domains do not eliminate the sixth Hand-off card. Scope and support are server-authoritative; an off-card result is Outside scope, not a failed or zero result. Inputs actually read by a selected optimization remain visible even if another card is off.

[PR #558](https://github.com/MLHaoChang/pypsa-eur/pull/558) is the prior research, not code shipped to master. Its reviewed source is `a08317290edf7746c2928f4650a467bb881403ae`. [PR #559](https://github.com/MLHaoChang/pypsa-eur/pull/559), open when inspected, relays PM-558, RES-CARDS, CARD-WS and AI-558 at `3d457a58a3971f58d3c67e252fb128f60af8d037`. Those newer recorded decisions constrain this proposal even though they were not yet in the assessed master:

- Keep #558 research-only. Fix F01/F02/F03/F10 as the separate gated Safe restore package. Do not restore the five-tab Manage proposal, PM00/PM14, or deferred journal/pins/tags/archive work here.
- Fold project summary into W2, shared edits/base + N/version runs into S5/S6, companion context into W9/S7/LC-05. Use Scenario, Saved snapshot, base case, compare case and declared changes.
- W10 is design-first card workspaces: inputs, native card assets, run, results, walkthrough; same contract in Guided and Expert. UX and domain review and owner design review precede build under the repository rules. This session does not claim those reviews occurred.
- The original next-step help belongs to W9/W10: one card entry, native reason first, model only on click, composer filled but never sent automatically. Result explanation stays within 12 admitted facts and withholds unmatched numbers. Suggested scenarios belong to LC-17/S7: max 4, at most 2 changes each, sourced/user ranges, existing experiment/edit-card preview.
- Do not add a third sensitivity preview path, task-profile endpoint, feature-specific cap system, answer cache or new price-based cost accounting. Those were deferred. Reuse configured limits and ordinary approvals.

This separate PR is explicitly authorized research for subsequent value creation. The identifiers HV-S/HV-D/HV-I below are traceability labels for acceptance slices, not new top-level implementation packages. Each lands, if accepted, in the owning existing package and its normal review sequence. They do not authorize starting early.

## Alternatives and why they come later

| Candidate | Useful application | Why below these three for now |
|---|---|---|
| Document/RAG knowledge helper | Explain tool terms and native methods | Add contextual documentation retrieval to existing help; correctness still needs native evidence, and it removes less study work |
| More models / agent committee | Optional independent interpretation | Additional latency/cost and correlated mistakes; cannot replace native numeric oracles or domain review |
| Autonomous optimization of all assets | Search more designs | Native optimization already handles admitted problems. First expose goals, support boundaries, provenance and comparable studies; do not make an LLM the optimizer |
| Automatic commercial contract negotiation | Contract review/extraction | Legal context, rights, tariff coverage and obligations require narrow adapters and review; first enable sourced inputs |
| Live operational control | Send setpoints to equipment | Different product and safety boundary. This proposal is planning/simulation; no production control, activation or equipment commands |

Source, missing inputs, model scope, freshness and verification belong inline in existing native result explanations and supported Hand-off chapters. Per AI-567, do not build a separate evidence map or advisor surface.

## Subsequent owner decisions

[OWNER-DISPOSITION.md](OWNER-DISPOSITION.md) records the newer AI-567/AI-567b/COP-1/RC1–RC4 decisions and supersedes any conflicting original proposal. The cross-card brief uses the existing plan card, every write/run and card boundary is separately gated, decision study explanations reuse existing surfaces, and document extraction follows spreadsheet mapping. #566 has since merged its card-workspace design; the runtime is not implied complete.
