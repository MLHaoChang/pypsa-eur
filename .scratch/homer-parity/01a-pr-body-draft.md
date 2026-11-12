Closes homer-parity 01a (`.scratch/homer-parity/issues/01-offgrid-renewable-fraction-and-operating-reserve.md`, owner decision HOMER-GAPS-1). 01b (operating reserve) is not in this PR.

## Changes proposed in this Pull Request

### Summary

HOMER's renewable fraction, electrical side: `f_ren = 1 − E_nonren / E_served`. It is an optional LP row and a KPI shown on every solve.

```diff
 build_lp_chain
   … _wrap_with_reserve_margin
+  _wrap_with_min_renewable_fraction      # services/solver/renewable_fraction.py
   _wrap_with_island …

 per investment period P (row min_renewable_fraction[_P]; none on a period without load):
+  Σ w·nonren + (1−f)·Σ w·slack ≤ (1−f)·Σ w·load        # w = snapshot_weightings.generators
```

- **E_served:** the scaled `p_set` of Loads on electrical buses, minus every slack's supply (VOLL, DSR, dc_curtailment). Shed energy is not served. With a slack present the LP may meet the target by shedding at the slack's price (R5).
- **E_nonren:**
  - non-renewable Generators on electrical buses, counted gross (an auxiliary `z ≥ p, z ≥ 0` where `p_min_pu < 0`);
  - Links out of a *supply bus* into an electrical bus, counted as η(t)·p0. A supply bus has a non-renewable Generator, no Load and no Line, for example a fuel bus or a grid bus. Its own Generator is then not counted, so nothing is counted twice.
  - Storage, electrolyser/fuel-cell loops and elec→elec converters are not sources.
- **Renewable test:** reuses `carrier_kpis._is_renewable_carrier`.
- **The fraction is not clipped.** Like HOMER's, it is negative when a genset out-produces what was served. 03a clips at its own call site.

```text
SolverConfigSchema.min_renewable_fraction  float | None, ge=0, le=1, no NaN/inf   (None/0 = off)
GET /api/results/renewable_fraction        renewable_fraction_result(n, lost-load capture, target)
  -> {fraction|None+reason, renewable_mwh, nonrenewable_mwh, served_mwh, per_period[], target}
get_results(kind="renewable_fraction")     chat reads the same payload
_diagnose_infeasibility                    "Minimum renewable fraction: the target of 90% (row …) needs X MWh …; at most Y MWh"
goal_free_config                           drops the key while off (OFF_WHEN_FALSY): fingerprints and IC digest unchanged
```

`renewable_fraction(n)` is the one implementation, as R2 requires. homer-parity 03a (session_01G7qCukJadD5XpQvRgzCT2o) has agreed to import it.

**UI.** Solver settings › Network tab gets a new **Off-grid constraints** block, after Reliability; 01b's inputs join it. Section 8, Scenarios & optimisation. The block is reachable in Expert and, in place, in Guided through `openFlowTargetInPlace({section:'scenarios'})`.

```diff
 <SolverSettings> tab=network
   <ReliabilityAssumptions/>
+  <OffGridConstraints draft patch/>    # pages/solver/OffGridConstraints.tsx
   <StressConditionsSection/>
```

### Evidence

Screenshots are in `.scratch/homer-parity/shots-01a/`:
- idle, value set and out-of-range, in light and dark: `{light,dark}-{idle,set,out-of-range}.png`;
- the block in its page: `light-page-context.png`;
- Guided, in place: `guided-light-value-100.png`;
- an infeasible solve's log naming the row: `dark-infeasible-log.png`.

Infeasible solve on the `eh_microgrid` template at a 100 % target with VOLL 0:

```text
[RENEWABLE] Minimum renewable fraction 100.0% (row min_renewable_fraction): non-renewable supply ≤ 0.0 MWh of 55,347.1 MWh electrical load.
[INFEASIBLE]   • Minimum renewable fraction: the target of 100% (row min_renewable_fraction) needs 55,347.1 MWh of renewable electricity served; the renewable generators can produce at most 46,539.8 MWh. …
```

**Plan conditions → tests.** All tests are in `backend/tests/test_homer_renewable_fraction.py` unless named otherwise.

| | Condition | Test |
|---|---|---|
| Acc. | 0–1 input, validated | `test_the_input_rejects_values_outside_0_to_1[-0.1,1.5,nan,inf,x]`, `test_the_input_accepts_a_fraction_or_none` |
| Acc. | KPI = hand-computed on the 3-asset fixture (PV, diesel, battery: 0.625) | `test_the_kpi_equals_the_hand_computed_fraction` |
| Acc. | row binds | `test_the_row_forces_more_renewables_and_holds` (free optimum 0.33 → ≥ 0.8) |
| Acc. | infeasible names the row | `test_an_unreachable_target_is_infeasible_and_names_the_row` |
| R1 | off is byte-identical | `test_off_keeps_master_fingerprint_input[None,0]` (pins master's sha), `test_a_target_changes_the_fingerprint_input`, `test_off_solve_is_identical_to_a_solve_without_the_field`, `test_off_solve_settings_carry_no_new_key`, `test_off_leaves_the_lp_untouched`; `test_reliability_goal.py::test_goal_free_config_drops_only_the_inert_fields` restated |
| R2 | one implementation | `renewable_fraction(n)`; 03a agreed to import it |
| R3 | grid import, either shape; storage is not a source | `test_grid_import_generator_is_non_renewable`, `test_grid_import_link_from_an_electrical_grid_bus_is_counted_once`, `test_a_link_fed_through_storage_is_not_a_source`, `test_the_kpi_counts_a_link_genset_from_a_fuel_bus_and_not_the_battery`, `test_a_genset_behind_a_line_counts_at_the_generator` |
| R4 | scaled load, time-varying η | `test_the_row_uses_the_scaled_load` (×1.5 in 2035 → 360 MWh), `test_a_time_varying_efficiency_reaches_the_row` |
| R5 | slack sheds instead | `test_with_a_voll_slack_the_target_is_met_by_shedding` (and test d runs with no slack) |
| R6 | served 0 → None; NaN/inf refused | `test_nothing_served_is_none_with_a_reason`, `test_a_capture_from_another_solve_is_refused`, schema tests above |
| R7 | every settings path validates | `test_the_solver_config_put_refuses_out_of_range` (422, value unchanged), `test_the_chat_settings_tool_refuses_out_of_range`, `test_the_chat_edit_card_preview_refuses_out_of_range`. The chat tool takes a generic partial, so no inventory change is needed. `assistant_inventory.py --check` and `assistant_control_matrix.py --check` are ok. |
| R8 | new route | golden coverage, route ACL, write lock, chat disposition (`get_results` kind), `test_chat_tools_endpoint_map.py` (enum 32, inventory fixture line), `test_results_range.py` (aggregate); `assistant_route_parity.py --check` ok |
| R9 | diagnosis names the row and the reach; no row on an empty period | `test_the_infeasibility_hint_names_need_and_reach`, `test_no_infeasibility_hint_when_off`, `test_a_period_without_load_gets_no_row` |
| R10 | one row per period, on its own weightings | `test_one_row_per_investment_period`, `test_each_period_row_uses_only_its_own_weightings` (weights 1 and 2 → rhs 120 and 240), `test_horizon_totals_weight_each_period_by_its_years` |
| R11 | vitest | `pages/solver/OffGridConstraints.test.tsx`: 80 → 0.8, empty/0 → null, −5/150/text refused with no patch; tsc clean |
| R12 | mutants | see below |
| U1–U4 | strings and block | vitest "never shows the engine key, and says 0 means no minimum"; out-of-range refusal opens with the label |
| U6 | edit-card label parity | `test_the_chat_edit_card_label_matches_the_panel` (reads the TSX constant) |

**Mutants (R12).** `scripts/coord/mutants.py services/solver/renewable_fraction.py tests/test_homer_renewable_fraction.py`:

MUTANTS_PLACEHOLDER

### Merge Danger

**Door:** two-way. The feature is off by default and adds a field and a route; nothing is migrated.

**Blast radius:** small. With the field unset, `build_lp_chain` returns the same callable, `goal_free_config` emits the same dict (pinned sha) and the solve settings carry no new key. The chat `get_results` enum grows by one kind (ChatPanel tool-list vitests pass).

### Skipped and known gaps
- Heat and hydrogen loads are not in the fraction (electrical only, documented).
- A bus fed by both a renewable and a non-renewable Generator counts whole as non-renewable (conservative, documented).
- The Issues panel's "No feasible solution" card stays generic. The row is named in the solve log only. **UX:** say if you want the hint surfaced on the card; that is a follow-up.
- No results UI reads `/results/renewable_fraction` yet. The KPI is served to the API and the chat.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated by Claude Code.)

Session: session_01G7qCuivVJjtuwpfTxxgQSV

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G7qCuivVJjtuwpfTxxgQSV
