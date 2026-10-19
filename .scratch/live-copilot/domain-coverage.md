<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Domain coverage and independent review

Status: ready-for-agent
Reviewed source: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Execution: acceptance requirements; no new domain tests were run in this review.

## Review finding

An owner-requested independent engineering agent reviewed the application,
plan and LC-T01–19. The shared admission, provenance and recovery design is
sound, but one journey per broad family is insufficient: materially different
engines and assumptions need separate cases. The following 24 domain suites
supplement, rather than replace, the [generic cases](test-plan.md). Existing
tests below are source-reviewed oracle seeds, not new copilot E2E evidence or
independent domain release approval. Real-use calibration stays pending.

The source denominator is the current generated inventory and
`smoke/assistant_control_matrix.py`, not historical prose counts in the earlier
live-voice plan. At this source the inventory has 255 tools, 15 result tabs and
19 panels; the matrix has 170 controls and the 14 families mapped below.
Creation-form fields and catalogue closed sets do not enumerate every editor,
subview, filter or operation. Those controls require an additional audit.
`full_coverage=not_established` remains unchanged.

## Fidelity is part of capability identity

“Simulation” is ambiguous. Resolve the requested engine and requested claim
before an ordered workflow starts it. Preserve these distinctions in discovery,
previews, approvals, facts, navigation, report and eventual speech:

| Capability | Supported meaning / source | Boundary that must be tested |
| --- | --- | --- |
| Capacity expansion / dispatch | Full, rolling and myopic modes in `services/solver_service.py`; native investment periods and solver settings | Modes are not interchangeable. Rolling dispatch does not prove investment optimality, real-time control or dynamic stability. Record solve status, gap and horizon |
| Static load flow / security | Native LOPF/SCLOPF and AC PF stages; `services/loadflow_summary.py` | Active-flow proxy, AC apparent-power loading, voltage and contingency scope are different claims. Missing Q, failed convergence or an unexamined contingency cannot become compliance |
| Adequacy | COPT, storage-aware LP proxy, standard sequential MC and zonal Energy Hub MC under `services/adequacy/` | COPT lacks storage chronology; LP uses perfect foresight; standard MC is electrical/single-area with its declared weather/independent-outage assumptions. Existing zonal MC adds grid areas, grid storage and opt-in common-mode events; do not apply standard-engine limitations to every variant. All retain stochastic uncertainty and explicit scope |
| Time-domain frequency | `gridspine/static/frequency.py`: actual fixed-step RK4 ODE, default 1 ms step / 20 s horizon; native island study tools | Single-bus aggregate frequency screening; no voltage dynamics, inter-unit oscillations or current-limit dynamics. Do not claim general EMT/RMS, PLL interaction or transient voltage stability certification |
| GFM recommendations | `gridspine/static/gfm_tuning.py`, `services/campus_island_tuning.py` | Bounded heuristic screening with final native verification; saturation and search budgets matter. Applying a setting is not firmware tuning certification |
| External electrical handoff | `gridspine/drivers/year_study.py`, `gridspine/readback/results.py` | Prepared PowerFactory data and static read-back do not prove an integrated external dynamic simulation ran |

Unavailable advanced studies must refuse that particular claim while offering
an explicitly labelled supported screening. Never silently substitute hourly
optimization for a seconds-scale request, or suppress existing frequency
simulation by declaring all time-domain functionality unavailable.

## Required domain suites

Every suite needs a source-pinned public authenticated API/SSE journey through
the actual native handler/worker, plus real browser display and independently
parsed export when those surfaces exist. Each relevant numerical result needs
an oracle independent of the production adapter. API/UI/chat/export agreement
is a consistency check; all four can otherwise share the same wrong formula.

Native paths below are relative to `pypsa-gui/backend/` unless prefixed with
`gridspine/` or `tests/gridspine/`. Candidate regressions under `tests/` refer
to backend tests. Recheck actual names and behavior before implementing a slice.

| ID / domain | Native seam and affirmative cases | Independent oracle and required failure/boundary cases |
| --- | --- | --- |
| LC-D01 Projects and scenarios | Create/activate/save/rename/copy/template/scenario and exact reopen through ordinary tools | Independent project IDs/files and unchanged baseline. Same-name ambiguity, foreign owner/org, dirty state, deleted source, interrupted create/rebind; no duplicate create on reconnect |
| LC-D02 Topology and parameters | `get_component` / class-specific CRUD, bulk edits, bus rename, line/transformer and carrier connections | Component/reference counts and small-network KCL. Occupied name, invalid connections/voltage, computed fields, transient rows, stale preview and one bad batch member; verify actual atomicity or explicit partial outcomes, not an assumed rollback |
| LC-D03 Time series and chronology | Profile import, snapshots, weights, investment periods and dynamic overrides | Hand-integrate `ΣP·Δt`; independently test objective/generator/store weights and period-year factors. Missing/duplicate/nonmonotonic times, alignment, timezone/DST/leap year, MW versus pu, horizon overlap and dynamic-over-static precedence. Synthetic profile provenance stays visible |
| LC-D04 Capacity expansion | `get_results`, `get_vintage_results`, `explain_investment`: fixed/extendable/min/max, zero build and bound-driven construction | Small unique LP: demand 10 MW, cheap build capped at 6 MW, higher-cost backup fills 4 MW, with explicit costs making 6/4 optimal; calculate objective and cap perturbation by hand. Distinguish existing/optimized/added capacity; MW for Generator/Link/StorageUnit, MWh for Store, MVA for branches. Missing duals cannot produce invented economic reasons |
| LC-D05 Multi-period planning | Full/myopic/limited foresight, build year/lifetime/retirement, vintages, period budgets and costs | Hand-discount a two-period build/retirement fixture; test visibility and horizon versus annual costs using `test_myopic_horizon_cost.py` seeds. Different horizons/foresight never compare as equivalent. Unavailable future capacity/duals stay unavailable |
| LC-D06 Solver and native jobs | Config/validation, full/rolling/myopic optimization, SCLOPF and AC stage, foreground/queue/status/cancel | Hand-solvable LP/MILP and power balance. Infeasible/unbounded, time limit, nonzero gap, unavailable solver, partial results, abort, lock and nonconvergence. Worker exit or HTTP 200 alone does not establish an optimal/valid solution |
| LC-D07 Load flow and security | `get_loadflow_summary`, exact current/retained branch/time/source, AC stage | LC-T07 weighted branch fixture plus `P=60 MW,Q=80 Mvar → 100 MVA`; both terminals/loss/KCL, optimized rating, varying `s_max_pu` and convergence masks. Independently solved static case; active proxy versus apparent loading, missing Q, N-1 coverage and voltage claims. Seeds: `test_loadflow_summary.py` |
| LC-D08 Dispatch, curtailment and lost load | Native dispatch/asset/result series and exact window/period | Independent energy balance and available-minus-dispatched generation; sum/mean/max, MW/MWh, negative dispatch, missing data, zero denominators and curtailment versus lost-load distinction. Summary masks must match admitted native rows |
| LC-D09 Storage and cost edits | Store and StorageUnit schemas, charging/discharging/state, exact cost edit/read-back and invalidation | For bounded StorageUnit without inflow/spill: `E_t=(1-loss)^Δt E_prev+η_c P_cΔt−P_dΔt/η_d`; predecessor chain, efficiencies, standing loss and simultaneous charge/discharge. Separately test native inflow/spill and Store sign/boundary semantics where supported; never infer split powers from net power. Cost oracle and checked-profile limits below; cyclic/multi-period/inflow scopes cannot silently inherit current checked support |
| LC-D10 Prices, emissions and economics results | Enumerate all actual result tabs/asset series and native cost/emission facts | Hand-calculated weighted marginal cost/emission totals; negative prices/costs, price versus shadow dual, tonnes versus EUR/tonne, emission factor bases, annual versus horizon cost and missing duals. Zero division gives an explicit unavailable ratio |
| LC-D11 Health, FMEA and COPT | Health assumptions/records, FMEA worksheet/sweep, COPT native results | Enumerate a two-unit available-capacity distribution; mass sums to one. FOR/EFORd basis, MTTR/MTTF, failure-mode/common-mode assumptions, transport limitations and critical-load scope. Health assumptions and FMEA screens do not certify chronological adequacy or electrical protection |
| LC-D12 Sequential and zonal Monte Carlo | Standard MC and zonal Energy Hub MC, separately named common-mode variant; native start/status/results, persistent outages, storage and period reset | Deterministic outage/storage trajectory, hand-enumerated states and seeded thermal-only analytic COPT comparison within statistical tolerance. CI/CoV/draw cap, seed/CRN identity, temporal correlation, accounting weights versus outage/SoC chronology and zero-observed-risk floor. Zonal import oracle: `min(available Link capacity, positive grid surplus × delivery ratio)`; grid demand priority, grid-storage depletion, disconnected areas, affected-Link removal on common-mode event, absent/invalid MTTR and stable random streams. Seeds: `test_adequacy_mc.py`, `test_adequacy_benchmarks.py`, `test_energy_hub_zonal_mc.py`, `test_energy_hub_zonal_areas.py`, `test_energy_hub_common_mode.py`. Frozen legacy zonal kernels importing production helpers are compatibility evidence, not independent physical truth |
| LC-D13 Adequacy planning and refinements | LP proxy, frontier, ELCC, reserve margin, coupling/campaign; explicit `margin_loop`, `eh_redundancy`, `eh_levers`, `eh_dtc`, `eh_dtc_planning`, multi-energy reporting and staged energization subcases | Hand-compute EUE/ENS/LOLE with horizon/year/critical-load basis. Test step-function margin response (cheapest verified margin is not global minimum), discrete redundancy/options/prices/finalist certification (not joint MILP optimality), DtC stress versus planning and bus versus per-Load VoLL priority. Critical Load can promote a whole bus; shared-bus reporting cannot invent per-carrier ENS. H₂/heat dedicated-bus versus per-Load attribution tested separately from electrical MC. Energization absolute-MW oracle `min(schedule, capacity)` survives capacity edits. Include unsupported scope, bounded refinement stop, study swap, stale source and aborted/incomplete campaigns |
| LC-D14 Static campus electrical | Create/configure, exact dispatch binding, stage/rank/capacity/connection, external handoff/read-back | Independently rank a tiny hourly fixture and compare a known static PF handoff. Source exclusivity, actual hour count, window/overlap, selected-hour nonconvergence, N-1/N-2 pruning, SCR/fault assumptions, Q/PF/tap/voltage limits and as-is versus measures; preserve bundle digest. Seeds: `tests/gridspine/test_campus_study.py` and native service tests |
| LC-D15 Control strategy schemas | Island config through native tools: `gfl_pq`, `gfl_qu`, `gfl_pf`, `gfl_fw`, `gfm_droop`, `gfm_vsm`; droop/isochronous governors | Per-model required fields and unsupported combinations. Droop percent on active-power base, inertia seconds on apparent-power base, filters/delays/ramps/deadband, pu current limit; wrong base/unit/model refuses. Static setpoints and aggregate screening never imply detailed converter-controller simulation |
| LC-D16 Time-domain events and numerical fidelity | `campus_run_island_study`: all applicable E1–E6 events, exact operating hour/SoC, trace/metrics/limits | Independent inertia `df/dt=−ΔP f0/(2HS)`, settled `df=−ΔP/ΣK`, single-governor matrix exponential and UPS walk-in energy `L·walk_in/(2·3600)` MWh. Cover cold start/sync/ramp, saturation/no-headroom/collapse, UPS dropout/autonomy, protection trips, unsettled horizon and initial/windowed RoCoF. Halve `dt` and extend horizon against explicit tolerances; reject invalid/too-costly requests. Seeds: `tests/gridspine/test_frequency.py`, `test_frequency_events.py`. EMT/PLL/voltage-dynamic requests require explicit unsupported disposition |
| LC-D17 Recommend, apply and rerun controls | `campus_recommend_gfm_settings` → preview → `campus_apply_gfm_settings` → required hub solve/re-study | Re-run native final verification for consistency; independently check analytic fixtures and time-step/horizon convergence. Cover bounded search incomplete/budget exhaustion, saturation/nonmonotonic qualification, stale/already-applied/wrong-model/busy refusal. Exact applied values/read-back and fresh inputs precede dependent work. Recommendation alone never proves the effect or study passed |
| LC-D18 Investment, tariffs and Library | Versioned Library inputs, site/participants/contracts, billing/Decision study and verdict | Short independent bill and discounted cash-flow fixture; TOU/demand peak/ratchet/billing interval/timezone, import/export signs, transfers versus system costs, currency/price year, annual/horizon/NPV, missing lifetime/rate/tax and incompatible versions. Guided/Expert use the same native engine; default provenance remains visible |
| LC-D19 Sensitivity and experiments | Plan/prepare/admit/advance/compare/export, exact isolated case artifacts | Independently solve two small cases, inspect modified inputs and unchanged baseline. Numeric/bool/enum/nested/file variations only with actual native support; common seeds, signed deltas, zero baseline, incompatible basis/source, shared caps, partial failure, unknown submission/reconciliation and no replay |
| LC-D20 Import/export/delivery | Every eligible supported format, inspection/mapping/validation, charts/bundles/delivery and authenticated download | Independently parse delivered bytes and round-trip values/units/times. Malformed/oversized data, format loss, ownership, bad references, path/formula injection, expired delivery and interrupted work. URL existence or HTTP 200 is not content proof |
| LC-D21 Reports and round trip | Actual template/section mapping, generation/status/export and supported edited-DOCX import/diff | Independently inspect document/table/figure facts and source versions. Stale/deleted fields, unsupported edits, partial/abort/restart and duplicate outputs. Narrative regeneration cannot change admitted numbers |
| LC-D22 Audit, undo and checkpoints | Preview/apply/audit, native undo and snapshot restore under ordinary gates | Independent before/after fingerprints, references and freshness. Concurrent edit/rebind, stale preview, lock/ACL, missing checkpoint and retained evidence; restoring inputs neither restores old approval nor makes old results current |
| LC-D23 Harness and provider parity | Discovery beyond initial offer, all eligible tools, workflow/goal/task, profile switch/recovery through both wires | Same native input/effect under both wires; forbidden capabilities never granted. Injection in uploads/tool prose, truncation/spill, incompatible history, lost approval, timeout and unknown effect. Deterministic wire fixtures prove dispatch, not hosted-model intent accuracy |
| LC-D24 UI and monitoring | Every eligible panel/subview/filter/editor/selection, native progress during reads/navigation and exact source ACK | Wrong project/run/generation/time/filter, stale revision, manual supersession, disconnect, keyboard/touch/320px/short/reduced-motion. Retain actual 6 pass / 2 fail baseline until checkbox pointer interception and whole-page overflow are reproduced and fixed; no forced clicks |

## Map the actual 14 families to suites

| Family from current generator | Required domain suites (plus generic LC-T contracts) |
| --- | --- |
| Projects/scenarios/templates | D01, D19, D22 |
| Network/topology/asset parameters | D02, D09, D15 |
| Time series/horizon/multi-period | D03, D05, D09 |
| Solver/simulation | D04–07, D14, D16–17 |
| All results | D04–18; enumerate all 15 actual tabs and applicable asset-detail views |
| Reliability/health/adequacy | D11–13 |
| GridSpine/campus electrical | D14–17 |
| Economics/investment/Library | D04–05, D09–10, D18 |
| Experiments/sensitivity | D19 plus the domain being varied |
| Import/export/delivery | D20 plus each delivered domain |
| Reports/templates/edit round trip | D21 plus each reported domain |
| Audit/undo/checkpoints | D22 plus each supported mutable domain |
| Harness/workflows/providers | D23 and LC-T01–19 |
| UI/preferences/monitoring | D24 plus the selected domain |

## Strengthen the owner's compound examples

**Storage costs.** Discover the actual schema and distinguish stored versus
display units and Store versus StorageUnit. A 2 MW / 4 h fixture at 100 EUR/kW
power cost and 50 EUR/kWh energy cost has 200,000 EUR power plus 400,000 EUR
energy overnight cost. Independently annualize the separate components with
their explicit lifetimes/rates/FOM bases; a duration edit rederives the energy
contribution. Dynamic marginal prices override scalars where native semantics
say so. Reject missing basis/units and preserve unrelated fields. Seeds:
`services/asset_schema/fields.py`, `test_asset_economics_capital_costs.py`.
The narrower checked direct-cost oracle of 51 EUR in
`test_storage_cost_assurance.py` does not certify overnight/FOM derivation or
total battery project economics; retain the actual checking limitations.

**Line edits then simulation.** Resolve total versus per-length impedance,
type-based parameters, rating and cost bases from native schemas; never infer
Ω/km from an unqualified value. Verify every approved native postcondition and
invalidate old results. Resolve whether the next operation is optimization,
static AC PF or a supported dynamic study. Failed second edit, intervening
manual edit, changed lock/policy, denial and unknown completion block advancement.
Successful dispatch does not establish that resistance was assessed by AC PF.

## Traceability and release gate

Before an adapter PR is ready, attach a machine-readable execution manifest
with: source/schema/tool/control IDs; engine/mode/fidelity; eligible operation;
project/scenario/case/run/input/result revisions; entity/period/window; weighting
column, units/base/sign; provenance; validity/completeness; applicable LC-T and
LC-D cases; independent oracle; public/native/browser/export evidence; observed
pass/fail/skip/unavailable disposition and reason. No placeholder journey ID or
inventory count closes a capability. A capability with multiple engine variants
must map every supported variant, not only one representative family journey.

Maintain denominators for every eligible tool, parameter schema, operation,
result tab, panel and audited control. New source/schema items start unmapped
and block claims for that slice until assigned tests or an explicit supported
limitation. Pending profile/process-unit PRs remain pending until actually
merged and re-reviewed. Audit unmapped controls rather than silently excluding
them from the denominator.

For each domain, exercise positive/current and eligible retained evidence,
missing/stale/corrupt evidence, invalid units/inputs, ACL and personal policy,
ordinary versus supported checked mode, Guided/Expert applicability and
provider parity. Apply duplicate/restart/denial/unknown-effect cases to every
effectful adapter and numerical-claim corruption cases to every fact adapter.
Record inapplicable combinations with a reason. Use risk-based combinations
for the remaining cross-product, but never omit a distinct mathematical engine,
supported control model or authorization/effect boundary to reduce test count.

Release each proven capability separately. This expanded finite specification
is more comprehensive than LC-T16 alone; it does not assert all imaginable
requests, successful new executions, zero hallucinations, independent domain
approval, live vendor readiness or production enablement.
