<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Checked results: audit of every result surface

Status: done (2026-10-22): every slice below is merged; the follow-ups are listed at the end.
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
| Summary (`summary`) | `get_results(statistics, cost_breakdown)` | Partial: per-generator energy/expenditure (generator-dispatch/1); network total on a declared basis (network-linear-cost/1) | No per-carrier totals; reads refused | `carrier-energy/1`: Σ_t w(t)·p_g(t) by carrier over generators and storage units, discharge and charge reported separately · `harness/calculations.py` · hand-summed two-carrier fixture | S2 |
| Dispatch (`dispatch`) | `get_results(generators, storage_dispatch)` | Per entity: generator-dispatch/1, storage-unit-dispatch/1 | No tab-level scope | One carrier per request (`carrier-energy/1`); multi-entity scope dropped: the browser receipt keys slots by metric (≤ 20) | S2 |
| Load flow (`loadflow`) | `get_loadflow_summary` | None for values; displayed-data ACK (v2) only | Values unchecked | `branch-loading/1`: matches `loadflow_summary` (see the approved semantics below) · new `harness/branch_calculations.py` · independent raw `p0`/`p1` fixture | S3 |
| Lost load (`lostload`) | `get_results(lost_load)` | None | Refused | `lost-load/1`: ENS = Σ_t w(t)·p_shed(t) per shedding unit; peak shed MW · new module · seeded shed fixture | S4 |
| Adequacy (`adequacy`) | `get_adequacy_results(adequacy)` | None | Refused; stochastic study output | COPT LOLE/EUE: an independent two-state convolution pinning copt.py's conventions (S4b); withheld until then. Monte Carlo and the EH per-year LOLE: withheld, `samples_not_retained` (no per-sample series is kept) | S4b |
| Prices (`prices`) | `get_results(prices, price_drivers)` | None | Refused | `bus-price/1`: observed `marginal_price` per bus; time-weighted mean, min, max; hours at VoLL. No price-setter claim · new module · raw-dual fixture | S5 |
| Emissions (`emissions`) | `get_results(emissions)` | None | Refused | `emissions/1`: Σ w·p/η(t)·co2_emissions over generators; `emissions_outside_scope` when links or storage carry CO2; cap dual observed, never inferred · new module · two-fuel fixture | S5 |
| Curtailment (`curtailment`) | `get_results(curtailment)` | None | Refused | `curtailment/1`: Σ w·max(0, p_max_pu(t)·p_nom_opt − p) for generators with time-varying `p_max_pu` only; share of available · new module · capped-PV fixture | S5 |
| Energy flow (`flows`, #423) | `get_energy_flows` (#438, open) | None; gated until #438 merges | Refused | `energy-flow/1`: per-source and per-destination MWh rebuilt independently; conservation residual ≤ tolerance or unavailable · new module · three-node hub fixture | S7 |
| Load shape (`loadshape`, #440, open) | `get_load_shape` (#440) | None | Refused | `load-shape/1`: peak, energy, load factor, weight-aware duration-curve percentiles over a window · new module · synthetic load fixture | S7 |
| Capacity expansion (`capex`) | `get_results(cost_breakdown, economics_by_carrier)` | Partial: annual and allocated fixed cost per entity on a declared basis | Optimised capacities (`p_nom_opt`) not a profile | `capacity/1`: observed `*_nom_opt` and Δ against `*_nom`, binding flag at `*_nom_max` · new module · bound-hit fixture | S6 |
| Economics (`economics`) | `get_results(asset_economics)` | None | Revenue and LCOE semantics undecided | LCOE by the engine's definition (S8); nodal revenue withheld, or labelled "market value at model prices" and never compared with investment-case rows | S8 |
| Investment case (`investment`) | `get_investment_case` | None | IRR, NPV and cash flows | `investment-case/1`: NPV and a unique IRR root, rebuilt from the stored cash flows (see below) · `finance/engine.py` semantics · hand-built cash-flow fixture | S8 |
| Storage cycling (`storage`) | `get_results(storage)` | Partial: storage-unit-dispatch/1 energy and final state | Cycle count not a profile | `storage-cycles/1`: Σ w·discharge ÷ energy capacity, cycles over the window, StorageUnits and Stores · extends `storage_calculations.py` · existing fixture | S6 |
| FMEA (`fmea`) | `get_adequacy_results(fmea_modes)` | None | Ranking semantics belong to the adequacy engine | Withheld: `outside_checked_scope`, tested | S1 |
| Asset detail (`asset`) | `get_asset_results` | Partial via the per-entity profiles | Arbitrary series | Routed to the per-entity profiles where one exists; otherwise withheld, tested | S1 |
| Overview (`overview`, multi-period) | `get_vintage_results` | None; every profile refuses investment periods | Multi-period | Withheld: `unsupported_snapshot_scope`, tested | S1 |
| Profile studio (`profiles_pending`, `profile_preview`) | `list_pending_profiles`, `preview_renewable_profile` | None | Synthetic estimate | Withheld: `synthetic_estimate`, tested | S1 |
| Grid-readiness scorecard (#435, #442; open) | `campus_grid_readiness` | None | Advisory verdicts | `scorecard-row/1`: each verdict recomputed from `project_value` against `limit` with the row's declared comparator; `unknown` stays `unknown` · after #435 merges | S9 |

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
| S1 (#452) | **Disposition registry and fail-closed proof.** Each `ResultView` declares `checked_profiles` and a `checked_gap`; a catalogue-wide test runs a checked turn against every tool and asserts the refusal, apart from `CHECKED_TOOLS`. Deferred: an `outside_checked_scope` slot naming the view, and admitting `ui_open_asset_detail` as navigation | Gatekeeper (dispositions) |
| S2 (#457) | Summary and Dispatch: `carrier-energy/1`, one carrier per request; new profiles opt-in (`OPT_IN_PROFILES`) | Gatekeeper |
| S3 (#460) | Load flow: `branch-loading/1` and `network-loading/1` (LOPF, P only). The v2 displayed-data ACK join is a later slice | Gatekeeper |
| Fingerprint (#462) | Every profile requires the exact solve-input fingerprint as well as the recorded one | Gatekeeper |
| S4 (#465) | Lost load: `lost-load/1` from the solve's VoLL capture | Gatekeeper (adequacy engine) |
| S4b (#483) | COPT LOLE/EUE: independent two-state convolution (`copt-screening/1`) | Gatekeeper (adequacy engine) |
| S3b (#488) | Load flow v2 ACK join: a checked loading answer binds the shown window to its evidence | Gatekeeper and UX reviewer |
| S5 (#472) | Prices, emissions, curtailment | Gatekeeper |
| S6 (#475) | Capacity expansion and storage cycles | Gatekeeper |
| S7 (#476, #477) | Energy flow (`energy-balance/1`) and Load shape (`site-import/1`) | Gatekeeper |
| S8 (#480, #481, #482, #484) | Economics and investment case: NPV and IRR (`investment-case/1`), retained LCOE series, nominal and real LCOE (`investment-lcoe/1`), the real-LCOE guard | Gatekeeper (finance) |
| S9 (#493, #500) | Grid-readiness scorecard rows: declared rules (#493), then `scorecard-row/1` (#500) | Gatekeeper |
| S10 (#486, #487) | Checked copilot charts: series slots, "Not drawn" list; the chart UI (#487) | Gatekeeper and UX reviewer, on the same head SHA |

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

## Approved calculation semantics

The gatekeeper reviewed these at `0a40442` and said "go with changes"; the changes are applied here. The owner's answer of 2026-10-20 (~19:23 CEST): under Check answers, finance stays withheld as "not checkable yet" until S8 builds a verified profile, in the planned order.

**Everywhere:**
- **Weights.** Call `period_utils.snapshot_weights(n, "generators")`, never `snapshot_weightings` directly. It is the basis `loadflow_summary` and `copt` use.
- **Sums.** MWh, money and capacity sums must match within |a − b| ≤ 1e-6·max(1, |b|).

**Per slice:**

- **S2 carrier energy.** Storage discharge (max(p, 0)) and charge (max(−p, 0)) are reported separately, because a net sum hides cycling. The scope is labelled "generators and storage units"; Link outputs (CHP, electrolysers) are outside it.
- **S3 branch loading** must match `loadflow_summary` as it stands:
  - rating = `s_nom_opt` if extendable, else `s_nom`;
  - flow = max(hypot(p0, q0), hypot(p1, q1)) where q is populated, else max(|p0|, |p1|);
  - u(t) = flow ÷ (rating·s_max_pu(t)), and "above 100 %" means u > 1 + 1e-6;
  - a rating ≤ 1e-9 makes the branch unavailable;
  - the result is disclosed as an active-power proxy (no N-1, no voltage) and joined to the existing v2 displayed-data ACK.
- **S4 adequacy:**
  - **Method.** LOLE is rebuilt by the producing engine's method. For COPT that is the analytical Σ_t w·LOLP(t), summed over period blocks; for Monte Carlo it is the sample-mean indicator. Anything else is `method_mismatch`.
  - **Basis.** The check matches the displayed basis: `lole_h_per_year = lole ÷ horizon_years`, not the per-horizon `lole`, and the receipt says which.
  - **Threshold.** Use the engine's ENS > ε threshold. Without one, use 1e-6 MW and say so.
- **S5:**
  - **Emissions.** Σ w·p/η·co2, with η from `get_switchable_as_dense` (time-varying efficiency). The result is unavailable as `emissions_outside_scope` when any Link feeds a CO2-carrier bus, or a StorageUnit or Store carrier has co2_emissions ≠ 0. It never under-reports.
  - **Curtailment.** Only generators with a time-varying `p_max_pu`, read dense. Committable units are refused, and a negative residual beyond 1e-6·p_nom_opt makes the generator unavailable.
  - **Prices.** The mean is labelled "time-weighted". "At VoLL" means λ ≥ VoLL·(1 − 1e-6), with VoLL taken from the run's config.
- **S6:**
  - **Capacity.** The bound flag never fires when `*_nom_max` = inf. When max = 0, the tolerance is an absolute 1e-6.
  - **Storage cycles.** Stores are included (`e_nom_opt`, discharged = Σ w·max(p, 0)). The denominator is the energy capacity, and the result is "cycles over the window", with no per-year scaling. Zero capacity makes it unavailable.
- **S7:**
  - **Load shape.** Percentiles are weight-aware when the weights are not uniform; otherwise the request is refused.
  - **Energy flow.** The conservation residual must be ≤ 1e-6·throughput.
- **S8 finance (restricted):**
  - **NPV** is rebuilt from the stored cash-flow rows at the run's stated rate and money basis, within 1e-6·Σ|CF|.
  - **IRR** is accepted when |NPV(IRR)| ≤ 1e-6·Σ|CF|. It is refused as `irr_not_unique` when the flows change sign more than once.
  - **LCOE** follows the engine's definition (SAM generalised, `finance/engine.py`): [PV(value) − PV(equity post tax)] ÷ PV(energy), all at the cost of equity. The real LCOE is the same at (1 + r)/(1 + i) − 1, following the engine's flags; when inflation is missing, it is unavailable.
  - **Nodal revenue** Σ w·p·λ_bus is withheld, or labelled "market value at model prices". It is never compared with investment-case rows, which are tariff and contract based.
- **S9 scorecard.** Each verdict is recomputed with the row's declared comparator (≤, < or ≥), never an inferred direction.

## Decisions since the first audit

- **S2.** One carrier per request. An empty sub-population is exactly 0, and the answer always counts the excluded Links and Stores. Storage energy reuses the StorageUnit profile's store weights; generator energy uses generator weights, disclosed. `ambiguous_carrier` applies only when the exact name and a case variant both exist; a case variant alone is `unknown_carrier` with a hint.
- **S3.** LOPF only, with P-only flow max(|p0|, |p1|). The check is refused whenever AC PF results exist. A missing time-varying `s_max_pu` falls back to the static value, as the tab does; any missing flow or zero limit makes the branch unavailable, which is stricter than the tab and disclosed. A class word in the request (line or transformer) must match. A branch named "network" is checkable only by naming its class.
- **Fingerprint.** Projects solved before the exact fingerprint was recorded read their checked answers as unavailable until they are re-solved.
- **S4.**
  - The source is `last_lost_load`, which every solve claim wipes and the same solve rewrites beside both fingerprints.
  - The demand bound is dropped, because the solve-time demand is not captured.
  - Energy, peak and cost cover every captured Load, matching the card; shed hours count the electrical Loads, as the engine does.
  - Bus-keyed legacy captures are refused.
  - Monte Carlo and the EH per-year LOLE are withheld: retaining samples would be an engine change with its own plan.
- **S8 notes.** An `investment-case/1` answer never says its LCOE is on the incremental value; that note is gated on the LCOE profile as well as on a counterfactual (audit close-out).
- **Display.** The Adequacy view's units read "over the modelled horizon"; the engines return horizon totals.

## Open questions

None open.
- **S4b** (closed by #483). The independent convolution pins the classical two-state case only. Fleets with profiled units, period-varying or folded capacity, or capacities off the 1 MW grid are refused, named.

## Follow-ups

Each is outside this package and gets its own issue or PR.
- **Results-tab window.** A checked answer's link to Results opens the tab without passing the evidence window (UX follow-up c on S3b).
- **Scorecard rows without a numeric verdict.** The categorical and non-scalar rows (`grid_former`, `lole`, `oscillation`, `energization_peak:*`) each need their own profile; `scorecard-row/1` leaves them unchecked.
- **Chart evidence messages (#486, GK nits).**
  - `verify()` says "expired" when the evidence file is missing; it should say "evidence no longer stored".
  - An orphan provenance JSON is left behind when `_save_agent_export` is refused.
- **"Not drawn" names (#487, UX nit).** The line has no readable series name, because the receipt carries no entity or carrier. Fixing it changes the receipt contract.
- **Guided reliability link.** `AvailabilityEditor.openReliability` opens the hub card in Guided mode, not Results (filed separately by the UX reviewer).
