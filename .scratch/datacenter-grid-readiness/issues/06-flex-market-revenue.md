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
