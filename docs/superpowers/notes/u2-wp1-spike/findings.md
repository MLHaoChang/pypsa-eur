# U2 WP1 facade spike — findings (2026-10-05)

**Plan.** `docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md`, WP1.

**What ran.** The owner chose "prep now" (2026-10-05). The spike ran on a throwaway combined copy that was never pushed:
- the IC branch at `9b3f65a`;
- PR #78 at `dec1e5f`, merged in without conflicts;
- the GS study modules, checked out from `f3f2ba5`.

The only shim was a copy of GS's `_wrap_with_demand_charge` inside the test file. **Moved (2026-10-06, WP1 proper):** the test now lives at `pypsa-gui/backend/tests/test_u2_wp1_spike.py`, adapted to master's real APIs (defaults pack, flat export series helper, public `export_revenue`, the branch's own demand wrapper; D11 changes the Q5 facts). The combined-copy version is in this file's git history (`2935fe4`).

**Result.** `20 passed in 67.21s`.

The U1 follow-up is not yet on the IC branch: the facade module, the defaults-pack loader and the flat-export-series helper are missing. The spike therefore compiles the tariffs by hand and mints the export series with `series_store.put_series`.

## Facts

### 1. Q5 / R3 — `single_owner` owns the meter Links

The owner assets of `value_flow_templates.build("single_owner")` on the site pack are:
- `grid_import` and `grid_export`;
- the battery;
- PV, when the option has it.

**Consequence.** `finance_case._assets` reads the Links' NaN `overnight_cost` as missing. Capex then reads `overnight_cost_missing`, the NPV is `None`, and `asset_lifetime_unknown` appears on both Links.

**Ways out (both measured).**
- **(a)** Type `overnight_cost=0` and a finite `lifetime` on both Links. This clears both problems.
- **(b)** Drop the Links from `asset_owners`. This gives the same counterfactual, but the template reads `template_edited`.

**Engine ask.** One of:
- `single_owner` does not own meter Links;
- `_assets` treats an uncosted, non-extendable meter Link as 0, with no lifetime flag.

### 2. C4 — `rate_meter` on the unsolved baseline pack

`rate_meter` works and equals GS's `BillCalculator` to 1e-9: DE 866,424.00 and TOU 638,592.00.

**Correction.** `settlement="h"` belongs on every compiled item, not only the demand item. Otherwise the energy and network items carry a false `bill_resolution_differs_from_settlement`.

### 3. Q9 — preflight on the compiled DE and TOU configs

The preflight is clean:
- no error;
- no `demand_resolution`;
- no `tariff_out_of_validity`.

Both controls fire when they should. GS's `eh_role=grid_supply` exemption for `gen_zero_costs` must be kept (WP0).

### 4. Rows 21–34 — refusals and flags

**Refusal order:**
1. `value_flows_not_configured`
2. `cod_missing` (it names the Links too)
3. the case builds
4. `run_case` refuses `analysis_years_missing`

**After that.** Missing rows become reasons, not refusals. With all rows present:
- operating is ok;
- the WACC gate is consistent;
- the only remaining capex reasons are `overnight_cost_missing` for the battery and the two Links.

**Row 30.** PV degradation is required: without it, `degradation_missing:pv`. Three degradation codes also appear at 0 degradation:
- `export_degrades_by_generation_share:*`
- `degradation_bill_first_order`
- `degradation_bill_volume_items_only`

These need HELP sentences, or a filter.

### 5. C1 — the S0 parts reproduce today's battery

`derive.apply_parts`, given the ledger's two parts, reproduces the following to 1e-9 for h = 1, 2 and 4:
- the two-annuity `capital_cost`;
- the FOM;
- the upfront cost.

The solved LP is identical: objective 829,729.809424 and `p_nom_opt` 0.771958 MW.

**Blocked.** `finance_case._assets` does not read `upfront_parts` yet (S0b).

**Open: who books the inverter replacements once S0b lands.** If S0b derives them from the power part's lifetime, GS must not also write `replacement_capex`.

### 6. C3 / C6 / Q15 — org and fork binding

**Org: resolved.** A study fork lives in its base project's org, which in local mode is the local org. `put_series` is idempotent there, so `library_org_unknown` cannot arise.

**Binding: the real problem.**
- `_bind_commercial(commercial, user)` binds only the foreground network. The spike showed it writing the foreground and leaving the fork untouched.
- `binding.bind_commercial`, called on the fork's in-memory network with the fork's directory and a resolver in the fork's org, does bind the fork. The binding survives the netCDF write and read.

**Engine ask.** Add to the facade:
- `binding.bind_commercial`, or a `bind_on_network(n, commercial, *, org_id, project_dir)` helper;
- `series_store` put/resolve.

The flat-export helper should take the org explicitly.

**New decision.** Minted export series appear in the org's Library list and outlive the study. They need a naming and cleanup rule.

### 7. §3.2 / Q12 / Q14 — the export line

`value_flows._export_revenue` equals −`commercial_cost_terms.block.energy_export` to 1e-9: 14,443.98 EUR on `bess_pv_2h`. Read `block`, because `items` omit zero amounts.

**Demand.** It is reported per period. The per-item split exists only in `ic_demand_peaks`. For GS's single demand item, the two agree.

**Decomposition.** The objective decomposition closes to about 1e-8 EUR.

**Q14.** The actual template has an export line; the counterfactual has none.

**Engine ask.** A public export accessor (`_export_revenue` is private).

### 8. R9 — run time

R9 is closed.

| Step | Time |
|---|---|
| `build_finance_case` on 8,760 h | 0.56–0.69 s |
| `run_case` | under 1 ms |
| the LP solve | 13.5–16.5 s |

### 9. §4.7 — demand formulation

On a 3-month toy, GS's wrapper and IC's `add_demand_terms` give:
- the same objective: 258,314.029548, relative difference 2e-16;
- equal monthly peaks;
- the same battery size: 0.792740 MW.

The year-billed (capacity) basis is still unverified.

### 10. §4.6 — the NPV identity on the IC engine (pre-evidence for WP7)

**Stand-in setup.**
- rows 21–34;
- C2: a `fixed` terminal value equal to GS's salvage;
- C4;
- the Links at 0;
- the battery upfront typed after the solve, as a stand-in for S0b.

**Result.** The engine's NPV equals the LP saving × AF(7 %, 25) to about 1e-11. It also reproduces GS's S5 golden figures:

| Option | NPV (EUR) | IRR |
|---|---|---|
| `bess_2h` | 371,681.6028 | 15.5637 % (flag `irr_multiple_sign_changes`) |
| `bess_pv_2h` | 1,566,950.7616 | 12.8690 % |

So C1, C2 and C4 are expected to give zero deltas once S0b lands.

## Engine asks for the IC session (to go through the owner)

1. **Q5.** Meter Links in `single_owner`.
2. **S0b.**
   - `_assets` reads Σ `upfront_parts` × capacity.
   - Settle who books the inverter replacements.
3. **Q15.** Expose these in the facade:
   - `binding.bind_commercial` or `bind_on_network`;
   - `series_store` put/resolve.

   The U1 (e) helper should take the org explicitly.
4. **Q4.** A public export-line accessor.
5. **R8.** The facade should also expose:
   - `commercial_cost_terms`;
   - `rate_meter`;
   - `value_flow_templates.build` and `template_status`;
   - `build_finance_case`, `run_case` and `FinanceRefused`;
   - optionally `demand_amount`.
6. **Decided by the owner (2026-10-05).** Minted per-study export series are named after the study (`decision-study:<base_uuid>:<study_id>:export`, labelled with the study's name) and deleted with it, except one another project still pins. IC needs to add `series_store.delete_series` and a pin check; the rule is in the sub-plan.
