<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Checked results: audit of every result surface

Status: ready-for-agent
Type: task
Baseline: master `8b5c707` (2026-10-20)

## Goal

The owner's request (2026-10-20): with **Check answers** on, the copilot states, shows or charts no result that the harness has not verified against the source data. Anything it cannot verify is withheld, or marked unchecked, and never published as if checked.

The package is done when:
- every registered result view (`services/result_views.py`) has a checked profile, or an explicit and tested "not checkable" disposition that withholds it or marks it unchecked;
- every copilot read, chart and export that carries numbers has the same;
- the last slice PR is merged.

Owner decisions in force:
- The live-model tier is manual, has a cost cap and is never a merge gate.
- Checking defaults off for new pilot chats; strict server mode requires it.
- The finite suite never confers production or domain approval.

## What checked mode does today

Read `harness/README.md` ("Checked-answer contracts") and `harness/assurance.py`. In short:

- **A hard allowlist.** In a checked turn, `Turn.tool_guard` (`harness/assurance.py:485`) admits only three tools:
  - `get_component`, for the static inputs in `INPUT_METRICS`;
  - `ui_open_panel`;
  - `update_component`, under an exact effect contract.

  Every other tool is refused with `capability_unsupported` before it runs (`harness/loop.py:2456`). That includes every results read, `create_chart` and every export.
- **Five bounded profiles answer results**, from a typed `RequestScope` (`harness/assurance_scope.py`) that software computes. No tool call is involved:
  - `generator-dispatch/1`, `generator-cost/1` and `network-linear-cost/1` (`harness/calculations.py`);
  - `storage-unit-dispatch/1` (`harness/storage_calculations.py`);
  - `storage-unit-cost/1` (`harness/storage_costs.py`).

  Each requested metric gets a slot: `answered`, `unavailable` or `needs_clarification`. `request_complete` is true only when every slot is `answered`.
- **Model prose is withheld.** Software renders the exact checked values. A draft that does not match evidence publishes as limited or unchecked, never as checked.
- **Navigation is acknowledged, not verified.** Version-1 ACKs prove only that a view mounted. Only Load flow has a version-2 ACK, which proves the data displayed (`harness/navigation.py:99`).

So, **structurally, no unverified result number publishes as checked today.** No test proves that for the whole catalogue, though, and almost every results question ends as "Requested results unavailable". The gap is coverage plus a missing proof, not a leak.

## The display contract

Agreed with the workspace-reconciliation session; recorded in PR #448 §5.4.

- **Only copilot-authored content carries a check state:** a stated value, a copilot chart, a narrative over Compare, or an "Explain this" note.
- **Native result views** carry freshness, never a check badge, and freshness is not verification.
- **"Showing" versus "opened".** The copilot says "showing" these numbers only with a displayed-data ACK (today, Load flow). Elsewhere it says "opened".
- **Chart series.** Each series has a disposition from the receipt (series slots, never the model). A series that is not `answered` is listed as "Not drawn: <series> — <reason>".
- **Out-of-scope requests** read "Requested results unavailable", with reason `outside_checked_scope`.
- **Labels.** No new labels. Any new label goes to the UX reviewer first.

## Inventory

### Results tabs (`RESULT_VIEWS`)

| View (tab) | Copilot read | Assurance coverage today | Gap | Plan (profile · module · oracle) | Slice |
| --- | --- | --- | --- | --- | --- |
| Summary (`summary`) | `get_results(statistics, cost_breakdown)` | Partial: per-generator energy/expenditure (generator-dispatch/1); network total on a declared basis (network-linear-cost/1) | No per-carrier totals; reads refused | `carrier-energy/1`: Σ_t w_g(t)·p_g(t) grouped by carrier · `harness/calculations.py` · hand-summed two-carrier fixture | S2 |
| Dispatch (`dispatch`) | `get_results(generators, storage_dispatch)` | Per entity: generator-dispatch/1, storage-unit-dispatch/1 | No tab-level or multi-entity scope | Multi-entity scope over the existing two profiles (≤ 20 entities) · `assurance_scope.py` · existing fixtures, summed | S2 |
| Load flow (`loadflow`) | `get_loadflow_summary` | None for values; displayed-data ACK (v2) only | Values unchecked | `branch-loading/1`: peak utilisation = max_t \|flow\|/(rating·s_max_pu(t)), rating = `s_nom_opt` if extendable else `s_nom` (as `loadflow_summary._rating`), flow = larger terminal magnitude; weighted hours above 100 %; active-power proxy disclosed · new `harness/branch_calculations.py` · independent raw `p0`/`p1` fixture | S3 |
| Lost load (`lostload`) | `get_results(lost_load)` | None | Refused | `lost-load/1`: ENS = Σ_t w(t)·p_shed(t) per load bus; peak shed MW · new module · seeded shed fixture | S4 |
| Adequacy (`adequacy`) | `get_adequacy_results(adequacy)` | None | Refused; stochastic study output | `adequacy-indices/1`: LOLE and EENS recomputed from stored per-sample hourly ENS when the samples are retained, else unavailable (`samples_not_retained`) · new module · fixed-sample fixture | S4 |
| Prices (`prices`) | `get_results(prices, price_drivers)` | None | Refused | `bus-price/1`: observed `marginal_price` per bus; weighted mean, min, max; hours at VoLL. No price-setter claim · new module · raw-dual fixture | S5 |
| Emissions (`emissions`) | `get_results(emissions)` | None | Refused | `emissions/1`: Σ w·p/η·co2_emissions per carrier; cap dual observed, never inferred · new module · two-fuel fixture | S5 |
| Curtailment (`curtailment`) | `get_results(curtailment)` | None | Refused | `curtailment/1`: Σ w·(p_max_pu·p_nom_opt − p) per generator ≥ 0, share of available · new module · capped-PV fixture | S5 |
| Energy flow (`flows`, #423) | `get_energy_flows` (#438, open) | None; gated until #438 merges | Refused | `energy-flow/1`: per-source and per-destination MWh rebuilt independently; conservation residual ≤ tolerance or unavailable · new module · three-node hub fixture | S7 |
| Load shape (`loadshape`, #440, open) | `get_load_shape` (#440) | None | Refused | `load-shape/1`: peak, energy, load factor, duration-curve percentiles over a window · new module · synthetic load fixture | S7 |
| Capacity expansion (`capex`) | `get_results(cost_breakdown, economics_by_carrier)` | Partial: annual and allocated fixed cost per entity on a declared basis | Optimised capacities (`p_nom_opt`) not a profile | `capacity/1`: observed `*_nom_opt` and Δ against `*_nom`, binding flag at `*_nom_max` · new module · bound-hit fixture | S6 |
| Economics (`economics`) | `get_results(asset_economics)` | None | Revenue and LCOE semantics undecided | Gatekeeper decides: `asset-economics/1` (revenue = Σ w·p·λ_bus; LCOE = cost on a declared basis ÷ energy), or withhold | S8 |
| Investment case (`investment`) | `get_investment_case` | None | IRR, NPV and cash flows | `investment-case/1`: NPV rebuilt from stored cash-flow rows at the stated rate; IRR checked as a root (\|NPV(IRR)\| ≤ tol) · gatekeeper · hand-built cash-flow fixture | S8 |
| Storage cycling (`storage`) | `get_results(storage)` | Partial: storage-unit-dispatch/1 energy and final state | Cycle count not a profile | `storage-cycles/1`: discharged energy ÷ usable capacity · extends `storage_calculations.py` · existing fixture | S6 |
| FMEA (`fmea`) | `get_adequacy_results(fmea_modes)` | None | Ranking semantics belong to the adequacy engine | Withheld: `outside_checked_scope`, tested | S1 |
| Asset detail (`asset`) | `get_asset_results` | Partial via the per-entity profiles | Arbitrary series | Routed to the per-entity profiles where one exists; otherwise withheld, tested | S1 |
| Overview (`overview`, multi-period) | `get_vintage_results` | None; every profile refuses investment periods | Multi-period | Withheld: `unsupported_snapshot_scope`, tested | S1 |
| Profile studio (`profiles_pending`, `profile_preview`) | `list_pending_profiles`, `preview_renewable_profile` | None | Synthetic estimate | Withheld: `synthetic_estimate`, tested | S1 |
| Grid-readiness scorecard (#435, #442; open) | `campus_grid_readiness` | None | Advisory verdicts | `scorecard-row/1`: each verdict recomputed from `project_value` against `limit`; `unknown` stays `unknown` · after #435 merges | S9 |

### Copilot reads outside the tabs

The sweep found 87 of the 226 catalogue tools on master whose results carry numbers. All except `get_component` are refused in a checked turn. Their dispositions:

| Group | Tools | Plan |
| --- | --- | --- |
| Results reads above | `get_results`, `get_loadflow_summary`, `get_adequacy_results`, `get_asset_results`, `get_investment_case`, `get_vintage_results`, `get_energy_flows`, `get_load_shape` | Answered through the profiles above, never by admitting the raw read into a checked turn |
| Inputs | `list_components`, `get_timeseries`, `get_aggregate_load`, `list_vintage_bounds` | `get_component` static inputs stay; time series withheld (`timeseries_not_checked`) until a profile exists |
| Comparisons | `compare_scenarios`, `compare_project_runs`, `compare_experiments`, `get_project_compare_state` | Same-basis `cost_delta` and `cost_change_percent` already checked through retained evidence; any other delta withheld |
| Run evidence | `get_run_evidence`, `get_study_evidence`, `read_result` | Retained run references reuse the profiles; the raw reads stay refused |
| Finance | `solve_ppa_price`, `explain_cashflow`, `get_study_findings`, `explain_verdict`, `explain_investment` | S8, or withheld |
| GridSpine, campus, island | `gridspine_*`, `campus_*` reads | Withheld (`outside_checked_scope`); separate engines with their own reviews |
| Reports | `get_report`, `get_report_table`, `build_study_report` | Withheld; report numeric audit is separate (ledger A4.4) |
| Other projects | `get_project_results_*`, `get_project_statistics`, `list_project_network_component` | Withheld (`wrong_project`); checked scope is one bound project |
| Writes and exports | `create_chart`, `export_*`, `run_sensitivity_sweep` | Charts: S10. Exports withheld; an export's bytes are not a published claim |

### Charts the copilot can open or generate

| Path | Numbers from | Today, checked | Plan |
| --- | --- | --- | --- |
| `ui_open_panel(results_tab=…)` | The tab's own API | Admitted; "submitted" until the v1 mounted ACK | Unchanged; "showing" only with v2 (Load flow; each new profile view may add its own v2 binding) |
| `ui_open_asset_detail(chart=true)` | `get_asset_results`, read by the tab | Refused | Admit as navigation, like `ui_open_panel`, with no value claim (S1) |
| `create_chart` (PNG download, server-rendered) | Server tables; the model passes identifiers only | Refused | S10: a checked chart draws only series whose points come from a profile's evidence; series slots go in the receipt; the PNG manifest hash is bound to the answer (extends P4 known-chart hashing) |

No chat frame carries chart numbers, and the model cannot supply chart values (`services/assistant_tools.py:414`).

## Slices, ordered by user impact

Each slice is one PR on its own `claude/*` branch, with a release-note fragment.
- The gatekeeper OKs the calculation and oracle semantics before each profile is built.
- The UX reviewer OKs any change to badges, the CompanionOutcome or the chart footer.

| Slice | What | Gate |
| --- | --- | --- |
| S1 | **Disposition registry and fail-closed proof.** Every `ResultView` gets an `assurance` field (`profile` or `withheld:<reason>`). A catalogue-wide test runs a checked turn against every tool and asserts the refusal, apart from the three allowlisted. A request for a withheld view yields an `unavailable` slot with `outside_checked_scope` and the view's label. `ui_open_asset_detail` is admitted as navigation. | Gatekeeper (dispositions) |
| S2 | Summary and Dispatch: `carrier-energy/1` and multi-entity scope over the existing profiles | Gatekeeper |
| S3 | Load flow: `branch-loading/1`, joined to its v2 displayed-data ACK. The first view checked end to end, value plus display | Gatekeeper |
| S4 | Lost load and adequacy: `lost-load/1`, `adequacy-indices/1` | Gatekeeper (adequacy engine) |
| S5 | Prices, emissions, curtailment | Gatekeeper |
| S6 | Capacity expansion and storage cycles | Gatekeeper |
| S7 | Energy flow and Load shape (after #438 and #440 merge) | Gatekeeper |
| S8 | Economics and investment case, or recorded withholding | Gatekeeper (finance) |
| S9 | Grid-readiness scorecard rows (after #435 merges) | Gatekeeper |
| S10 | Checked copilot charts: series slots, "Not drawn" list | Gatekeeper and UX reviewer, on the same head SHA |

Every new profile:
- joins the 133-case pilot evaluation with positive and adversarial cases (wrong value, unit, entity, window and sign; extra claim);
- is off by default behind `PYPSA_GUI_ANSWER_ASSURANCE_PROFILES`;
- gets an independent numerical oracle under `domain-review/`.

Tier B (live model) stays manual and is never a merge gate.

## Out of scope

- Checking with Check answers off.
- Certifying the engineering domain.
- AC thermal or N-1 claims; load flow stays an active-power proxy.
- LC-04's edit card and CompanionLauncher. Changes to CompanionOutcome are coordinated with LC-04.
- Hand-merging `capability-inventory.json`. It is regenerated with `smoke/assistant_inventory.py --write`.

## Open questions

- **Economics and investment case (S8):** build profiles, or withhold permanently? Revenue and IRR semantics are finance-engine decisions.
- **Adequacy indices (S4):** are per-sample ENS series retained by the adequacy store? If not, does S4 add retention, or withhold adequacy?
