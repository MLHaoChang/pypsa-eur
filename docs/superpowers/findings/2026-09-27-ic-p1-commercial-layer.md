# Edge Investment Case — Phase 1 (commercial layer): end-to-end QA

**Plan:** `docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md` · **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` (§5.1 / §5.2 "As implemented") ·
**Branch:** `claude/energy-tool-features-research-fdixs0` · **Head at gate:** see commit carrying this note.

## What Phase 1 delivers

| WP | Deliverable | Review rounds → final verdict |
|---|---|---|
| 1.0 | Snapshot weightings from the step frequency; 15-min edge fixture | FAIL → re-review conditions → PASS (754877e, 645e377) |
| 1.1a/b/c | Org-scoped, versioned, content-addressed Library series store; `/api/library` router with org ACL; Library pins in the project bundle (`library_issues` on load/import/activate) | conditions closed each round → PASS (893439b … 28993f1) |
| 1.2 | `tariff_engine`: energy / TOU / fixed rating on the site clock, DST-exact, hand-rated fixtures | FAIL → conditions → PASS (a6f7aca … 3fbf14b) |
| 1.3 | PoC energy prices in the LP as a **transient** apply (undone after the solve, committed on success into `links_t["ic_energy_price"]` + `n.meta["ic_poc_links"]`) | FAIL ×2 → redesigned transient → PASS (4d7f56f, 4c883c1, 8ba844e) |
| 1.4a/b | Connection agreements (firm / non-firm / `available_from`), explicit capacity-fee LP term, envelopes, FCA stress entry, `links_p_max_pu` stress slot | FAIL ×3 → PASS (4c883c1 … c567334) |
| 1.5a-0 | linopy new-variable spike | GO (46e6b26) |
| 1.5a | Monthly peak-demand charges: `ic_peak_import` per (item, window, month) on settlement-interval means; engine bills the same | FAIL ×2 → PASS WITH CONDITIONS → **PASS** (round 4) |
| 1.5b | Ratchets on ACTUAL prior peaks (`ic_billed_demand`), meter-history seed for the first investment period, `initial_peak_lower_bound` hook (P6) | reviewed with 1.5a → PASS |
| 1.5c | Convex tiers as stacked volume terms (`ic_tier_q`); non-convex flagged, priced at the first tier | reviewed with 1.5a/1.7 → PASS |
| 1.6 | Energy-hub group contract: one customer under one tariff — every member priced, demand and tiers on the group meter, per-snapshot cap (`ic_group_cap`), energy shares | PASS WITH CONDITIONS ×2 → **PASS** (round 3) |
| 1.7 | Reload-safe commercial rows in `cost_breakdown` and `horizon_system_cost`; objective gap 0 before and after save → load on nine cases | PASS WITH CONDITIONS → **PASS** (round 3) |
| 1.8 | Commercial preflight (`commercial.*` issues in `validate_for_run`), parity with every solve refusal incl. strategy-dependent ones | FAIL → PASS WITH CONDITIONS → **PASS** (round 3) |

Design decisions recorded in the spec and plan: every commercial transform is transient and committed only on
an ok/optimal solve (operational sweep solves never commit); partial months are charged a full monthly
demand charge and disclosed (§5.2); months without snapshots are "not established", never inferred; a net
interval meter reads the interval's net energy; a group is billed as one customer on the group meter.

## Gate evidence (2026-09-27)

| Check | Result |
|---|---|
| `tests/qa_commercial_lp.py` (DE: TOU + firm fee + Library export price + bundle round-trip; US: TOU + ratcheted demand) | **26/26** — LP cost == engine per item, gap 0 before and after project reload, evening peak shaved, user Links untouched, bundle keeps config and Library pins |
| Reconciliation gate `test_commercial_objective_reconciliation.py` (energy, fee, demand, ratchet, tiers, group, representative weeks, two periods, two-period group) | **9/9** — gap < 1e-6 before and after the real project save/load routes; rows, flags, not-established months and group record identical |
| Tests touched after the full run started (17 commercial / library / audit / tripwire files + the two other `models.commercial` importers) | **398 passed** + **98 passed** |
| Full backend suite (`-m "not slow"`, Python 3.12 venv pinned to `pixi.lock`), started at the WP1.5a-round-3 / WP1.6-round-2 / WP1.8-round-1 tree | **6,215 passed, 31 skipped, 0 failed** (31 min) |
| QA drivers (`tests/run_qa_drivers.py`) | **22/22 passed** (incl. `qa_commercial_lp`, `qa_save_load_roundtrip`, adequacy journeys) |
| Frontend `vitest run` | **178 files, 1,947 tests passed** |
| Frontend `tsc --noEmit` | clean |

Reviewer probe matrices (not committed; recorded in the plan's review notes): 48 DST cases (Berlin, New York
fall-back and spring-forward, Lord Howe, Kolkata × 15-min/hourly steps × 15-min/hourly settlement, import and
net metering) agree between LP and engine per (month, window) with gap ≤ 1.2e-9; representative-weeks and
two-period ratchet cases agree per (period, month, window); a two-member group with TOU, net demand, tiers
and export revenue matches the engine on the summed member dispatch per item.

## Carried forward (not Phase 1 scope)

- **P2:** PPA / export-price and DR-contract / `dsr_buses` double-count preflight checks (need P2's
  contracts; plan WP1.8 deviation). Library tariffs (`import_tariff_id`) arrive with WP2.4.
- **P2/P3:** net-measured *energy* items on a multi-member group with an export Link are refused in P1 (the
  group meter nets what per-member adders cannot); a group net-import variable is the P2/P3 option. The
  connection agreement (fee, envelope) binds `poc_link` only; `commercial.group_fee_bypass` warns when another
  member is extendable. Cost allocation across members is P3 (shares are reported now).
- **P6:** windowed dispatch (rolling / multi-period myopic) with demand, tiers or a capacity fee is refused in
  P1; the running-peak carry uses the `initial_peak_lower_bound` hook.
- **Conventions:** with unsampled months the demand row is the sampled sum plus `demand_months_not_established`
  while the engine withholds `per_item`; both disclose, the conventions differ (P2 WP2.3 gap attribution
  should unify them).
- **Pre-existing, outside the commercial layer:** multi-period decomposition with objective weights that
  differ from `years` (discounting) shows a gap for plain solves too; myopic multi-period `gap_pct` covers the
  last period only and `_myopic_period_objectives` is not persisted across a reload.
- **Performance:** the per-key demand constraint loop costs ~4 s for 72 keys + ratchets at year scale.
- **Arbitrage check:** Link efficiencies are not considered (said in the warning).
- **Gate P0 condition 3** stays open: no new `services/solver/` module has landed (the commercial layer lives
  in `services/commercial/`).
- From P0: `billing_frames` size at 15-min resolution (P2 WP2.1).

## Gate verdict

- [ ] Assessor verdict: pending (independent assessor running).
