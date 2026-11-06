# Battery-based backup in place of diesel

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 01
Gated: yes (adequacy, finance)
Model tier: opus
Size: M

## What to build

- A backup-mode setting on the DC load: `genset | bess | hybrid`.
- BESS sizing for ride-through plus N hours of autonomy for the critical share, honouring redundancy (N, N+1, 2N).
- The DtC stress re-dispatch proves critical load is served on loss of grid.
- Investment case: avoided genset capex/fuel/run-hours against BESS capex, augmentation and fade (`BessSpec` already has them).

## Done when

A DC project can switch to `bess` and see adequacy plus the cost delta against `genset`.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: DC load model and template parameters, `services/adequacy/` (DtC stress re-dispatch), `BessSpec` sizing and finance. Blockers: 01 is done (#421). Reviewers: GK A (adequacy) and GK B (finance). Unblocks 05, 06, 07.

### 2026-11-06: plan verdicts (session_01G7qCunRZs4jhG7eAZytnEp)

GK A (13), adequacy: GO. Q1 (a) an optional outage window on `DtcConfig` (None means today's path, byte-identical). Q2 (a) a stored setting plus an assessment on a copy; the saved network is never mutated. Conditions:
1. The windowed zeroing keeps each link's own p_*_pu outside the window. Undo restores exactly: a created links_t column is dropped. Tested with and without a pre-existing series.
2. Window placement:
   - Length is in hours via snapshot weightings, never by index.
   - It starts at the max rolling critical-energy span; start and energy are reported.
   - A window longer than the horizon is refused (422); one that runs past the end wraps or shifts, never clipped.
   - Tested on a profile whose peak hour is outside the max-energy window.
3. The strings carry usable energy only (nameplate × DoD × retention), with η from the same RTE split as the sizing. Proof: exactly sized serves, 0.95× fails.
4. Backup holds its charge outside an outage: discharge is off in normal solves and allowed only in the stress window. SoC is pinned full at the window start, or the perfect-foresight note is kept. Tested: normal-solve discharge = 0.
5. Sizing:
   - S = max (P_c − g)⁺ with g from `site_peak.firm_genset_mw`.
   - Ride-through seconds are in energy only; the hourly LP cannot prove them.
   - The fade arm names its cycle count.
   - Tested: 2N differs from N+1 + 1.
6. Sidecar:
   - The bess sidecar passes `gridspine.schema.island` validation.
   - site_peak's peak check and outage reserve read the new fleet, tested in bess and hybrid mode.
   - Genset mode keeps byte-identity for every template.
7. Routes:
   - The route-policy tests pass at the trial merge.
   - The PUT validates like the create body.
   - Revision or solve-time reads are shown.
   - assess uses `_solver_config_from_dict`, `build_lp_chain` and `PyPSAService.solving_context`, and names any pre-solve step it does not reproduce.
   - network.nc and eh_template.json are byte-equal around assess.
8. An unreadable critical-unmet is "not shown", never "served".
9. If the window is persisted where fingerprints hash it, it is appended only when set, with a pin test; otherwise the PR says it is assess-only.

GK B (25), finance: GO WITH CHANGES, option (a). A standalone `backup_cost_delta` uses `finance.metrics.npv`; the IC engine is untouched; it folds in under DC-05.
- F1 The genset fleet uses the same N/N+1/2N function as the adequacy sizing. A test shows both read the same MW.
- F2 One horizon-end convention for both legs, named. Hand test with a repurchase in the final year.
- F3 The rate basis matches the IC engine's real/nominal handling. The 7 % fallback is assumed and real. Tested with inflation set.
- F4 Units on every row. A pack fuel row is used if present. Assumed values are labelled. Hand test: fuel = h × MW × heat rate × price.
- F5 Fail closed (typed refusal) on a missing pack key, blank fade, analysis_years < 1 or a non-finite input. One mutant goes RED.
- F6 Hybrid legs count only their own share; the parts sum to the whole.
- F7 `none` puts the up-sized MWh in capex and buys nothing later. restore and fixed_years are hand-tested on the same fade curve.
- F8 BackupSpec fields that are not LP inputs stay out of `_network_input_fingerprint`, with a test; agreed with GK A.
