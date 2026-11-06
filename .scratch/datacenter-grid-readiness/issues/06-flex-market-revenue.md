# Flexibility-market revenue from reserved BESS/UPS headroom

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 03
Gated: yes (finance, solver)
Model tier: opus
Size: M

## What to build

- Reserve products (FCR / aFRR / mFRR style, generic names) as headroom the BESS or UPS commits, with the critical-backup energy kept untouchable.
- Revenue goes into the investment case. The reserved headroom shows in dispatch.
- Prices are user time series. No market-specific price data is shipped.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: reserve-product constraints in the solve path, investment-case revenue lines, dispatch view. Blockers: 03. Reviewers: GK A (solver/adequacy engine) and GK B (finance).

### 2026-11-06: plan verdicts (DC-06, session_01G7qCujioNERLSXtUgzvUcq)

UX (11): GO with changes. Q1 (a) solid muted floor fill with a lock, labelled "Backup floor (kept for outages)", value in legend and tooltip. Q2 (a) one hatched band per product, palette colours, up/down share a colour with different hatch angles, overflow folds into "Other reserve". View and summary row in Guided and Expert. Summary: own Source cell ("user price series"); revenue header names currency and money year; "not solved" for a missing frame, never 0; "out of date" per ResultState; DEMO tag on demo series. DC backup line "Reserve products: N · revenue … · headroom … h per string · Storage view >" through the mode-aware opener with withReturn. Shots in both modes: up+down products, missing frame, storage without reserves, DC backup line with link and Back.

GK B (28), finance: GO WITH CONDITIONS. Q4 (a) `ancillary` settlement line, Q5 (a) capacity only, Q6 (a) headroom capex in backup_cost_delta.
- L1 Settlement weights = the objective's (w_obj × period weight), same source as the LP; mutant on generator weights; two-period hand test.
- L2 Price-series currency equals the finance currency through the GS-01 gate before the LP is built; mismatch refused; stored-EUR-default case tested.
- L3 Price per MW per h; multiplied by snapshot hours (3-hourly test: weight 3 earns 3 h × price × r); coverage full or 422, hash-keyed; series hash in the IC digest.
- L4 reserve_products hashed via goal_free_config (test); headroom and reserve columns feed `_network_input_fingerprint` and the IC digest only when non-default; pin test.
- L5 None + `reserve_not_solved` when the solved frame is absent or stale (fingerprint differs), both tested; a solved product with no MW reads 0 unflagged.
- L6 List every export-class rule in cashflow/IC; test none touches an `ancillary` line (else stop and report); IC row labelled reserve capacity revenue.
- L7 Activation not valued; directional products flagged `activation_not_valued`; response names activation energy, cycling wear and non-delivery penalties as not modelled.
- L8 Headroom capex in backup_cost_delta with the backup energy's DoD/retention/augmentation (DC-03 credit rule); revenue never without capex and vice versa; capex priced exactly once.
- L9 IC revenue from settlement lines only, never the objective; NPV = Σ discounted settlement amounts; no double netting.
- L10 Each new guard (currency, coverage, stale frame, missing sustain_h) tested with a killed mutant; new refusal kinds in a tool-error-kinds.d fragment.

GK A (15), engine: GO WITH CONDITIONS. Q1 (a) CommercialConfig, bound like reference prices. Q2 (a) every string's backup energy, spares included. Q3 (a) template UPS eligible under R7, else strings only.
- R1 Power rows read availability: p_dis − p_store + Σr ≤ p_nom·p_max_pu(t); p_store − p_dis + Σr ≤ p_nom·(−p_min_pu(t)); fixed and extendable p_nom; test p_max_pu < 1 at one snapshot.
- R2 Floor row on soc(t−1) and soc(t); at t0 soc(t−1) is state_of_charge_initial, or the period's last snapshot when cyclic per period. Non-cyclic initial SoC below floor + committed need refused before the solve, typed. Both paths tested.
- R3 One floor source: derived from the stored DC-03 spec at bind time, or the stored column checked against it and refused on disagreement. Not user-editable; NaN/unreadable floor = ineligible, never 0 (#1079).
- R4 No products: LP, network.nc, `_network_input_fingerprint`, IC digest and settlement lines equal master. With products: reserve_products, the reserve_price hash and backup_floor_mwh enter the fingerprint only when present; pin test. reserve_headroom_h = 0 keeps size_bess and template sidecars byte-equal.
- R5 Every build_lp_chain runner carries the rows; listed in the PR body; one loop runner tested; DC-03 assess byte-equal with products set; bypassing paths named.
- R6 Finance reads r only from a solve whose fingerprint matches current inputs; stale → flag, amount None (#1170). One weighting helper (objective × period weighting) for LP and settlement.
- R7 UPS floor P_crit × ride_through_s/3600/η_dis from the same stored spec and fleet as the DC-03 proof (#1172); a missing input = ineligible. Hand test at/just above the floor.
- R8 Each typed refusal (non-eligible, floor > usable, missing/stale price, R2 SoC) tested where caught, kind in a tool-error-kinds.d fragment; sustain_h > 0 and max_commit_mw ≥ 0 checked on every CommercialConfig write path incl. bulk and preview.
- R9 Extra mutants RED: p_max_pu dropped from (1); soc(t−1) side of (3) dropped; stale floor column after a spec edit; period weight dropped; settlement on a stale frame.

### 2026-11-06: amended rulings (DC-06)

- R6 / L1 (GK A and GK B, replacing the old R6 weighting sentence and L1): one helper `reserve_weights(n)` → (hours = snapshot_weightings.objective, period_w = investment_period_weightings.objective or 1), read by the LP and settlement. LP term Σ hours·period_w·price·r (PyPSA's marginal-cost weighting). Each period's settlement line Σ hours·price·r over that period's snapshots, never × period_w; the IC alone applies years and discounting. Tests: the two-period hand test checks the LP term (distinct period weights) and the per-period lines (without them). Mutants killed: period_w dropped from the LP; period_w added to settlement; weights from generators. L3 stands.
- R1 (GK A): option (a). DC-03's p_max_pu series of 0 is a dispatch hold, not availability. Strings are identified from the stored DC-03 spec and `dc_backup.string_names` (N + spares), never a `backup_bess_*` glob; their up row reads the static p_max_pu, every other eligible storage p_max_pu(t). A string whose series holds anything but 0 is refused (`reserve_string_availability_edited`). Tests: string with scheduled discharge 0 and r_up > 0 at p_nom·static; UPS at p_max_pu(t) < 1; a non-string named backup_bess_x takes the series. Mutants: string reads p_max_pu(t); non-string reads static. soc(t−1) through PyPSA's balance accepted; tested with standing_loss > 0 and a weighted snapshot. The PR body states an activated up-reserve draws energy only above the floor, through (3).
- Sequencing (coordinator): build in new files now; the commercial.py / binding.py / hashing.py hunks after #1180 merges, reusing its currency gate.
- DC-05: DC-06 lands its dc_backup.py hunks; DC-05 merges over them. Keep `_part`, `real_rate`, `_check_years`, `project_spec`, `_meta`, `_basis`, `finance_rate` signatures.

### 2026-11-06: DC-06 (2) build notes

- Headroom: `BackupSpec.reserve_headroom_h` (0 dumps as before). `size_bess` adds the headroom's usable energy at the backup energy's DoD and retention. `backup_cost_delta` prices it once, inside the energy lines.
- Settlement: `results.billing.reserve_lines`, one line per product and period, Σ hours × price × MW. A missing or stale solve gives None + `reserve_not_solved`. The stale case comes from `reserve_products.inputs_digest`, which covers the products and floors. Directional products carry `activation_not_valued`. The ledger source is `reserve` / `ancillary`, from the counterparty to the site, with a reconciliation bridge term. The IC row is "Reserve capacity revenue". `objective_decomposition` names `reserve_revenue_eur`.
- Reserves are read-only in chat for now: GET /api/results/reserves (`routers/reserves.py`, `services/results/reserves.py`) feeds the storage view and the DC backup line. The chat reads the same lines through the billing payload's `reserve` section.
- Runners of `build_lp_chain`: `run_simulation`, which covers plain, rolling, myopic and SCLOPF and carries the rows, and the sizing loop (`sizing_loop_runner._prepare`), which carries them too but refuses a project with commercial bindings by name. The DtC outage re-dispatch strips them (`without_reserves`). The DC-03 assess proof runs through the DtC, so it is byte-equal with products set.

### 2026-11-06: L6 resolved (GK B (28), carried by GK B (29))

The audit found that only `cashflow.STREAM_CLASS["ancillary"] = "export"` touches an `ancillary` line. The REFERENCE_CLASS CfD leg, the `energy_export`-only owner_gens split, the participants export_split and the lp_bindings/preflight/tariff_engine export rules never see settlement lines. GK B chose option (a): reserve capacity revenue escalates at the export market rate. Conditions:
- L6a: the IC row "Reserve capacity revenue" says it "escalates at the export market rate" and names the rate (`revenue_streams` row: `note`, `escalation_class`, `escalation_rate`).
- L6b: tests cover the exact two-year hand value, `escalation_missing:export` when the rate is missing, the owner_gens split leaving the line whole, and the row mapping. Mutants on the row mapping and the class are killed.
