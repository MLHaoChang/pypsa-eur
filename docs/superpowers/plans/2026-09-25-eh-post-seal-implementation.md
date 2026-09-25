# Energy Hub reference design — post-seal implementation plan

**Status:** revised after two independent QA gates. Both returned `GO WITH BINDING CONDITIONS`. All conditions are folded in below: B1–B14 from the first gate, R1–R5 from the re-gate. Q1–Q7 were **decided by the product owner on 2026-09-25**; each went with the recommended option (see the Decisions table). All phases are unblocked, subject to the dependency order and spec amendments.
**Source of the TODO list:** [`findings/2026-09-25-eh-handover-assessment-claude.md`](../findings/2026-09-25-eh-handover-assessment-claude.md) §3–§4
**Parent plan / spec:** [`2026-09-14-eh-reference-design-gaps.md`](2026-09-14-eh-reference-design-gaps.md), [`specs/2026-09-14-eh-reference-design.md`](../specs/2026-09-14-eh-reference-design.md)
**Base:** `master` + `claude/epic-allen-k2t1c4` (isolation / budget / honest-failure fixes). Every phase assumes those fixes. In particular, `run_eh_study` runs on a private network + cfg copy and enforces `budget_solves`.

**Working rules (unchanged from the parent plan):**
- TDD: red first, then green.
- Independent QA gate (`GO` / `GO WITH BINDING CONDITIONS` / `NO-GO`) per phase.
- One phase per PR.
- Build on existing engines; no parallel adequacy stack.
- Honesty over completeness: when something can't be established, report `not_established` with a reason; never invent a value.
- Every phase adds at least one **unstubbed HTTP** EH test. The #52 seal's blind spot was that `test_energy_hub_study_http.py` stubs the driver.
- **Spec amendments land first**, in the same PR as the phase that needs them: §4 report contract (P11), §9 (P12), decision 8 / §8 / §10 (P16), §6 (P17). The spec is binding, and `models/energy_hub.py` says "do not renegotiate" without one.

**Explicit non-goals of this plan** (still deferred): climate P8(b), spare-lead severity modifier, planned-outage MC, joint MILP, in-tree EMT, and multi-area / network-aware MC (P11 stays copper-plate; see Q7).

---

## Dependency order

```
P10 hygiene (DSR preflight, DtC stale fallbacks + fixed-plan, campaign FMEA estimate, CI, locked copy)
 ├─ P11 mc_certify stage   (Q1/Q2/Q7 decided)
 │    └─ P12 frontier + fmea_top stages (pack-scoped frontier; budget-safe)
 ├─ P13 pack parameters (HTTP/UI/chat)   ← after P11 (target_lole_h / certify flags)
 │    └─ P17 energy import cap (+ lever) ← spec §6 amendment, lowest priority
 ├─ P14 EH network tagging + readiness
 │    └─ P15 Class-C authoring UI
 ├─ P16 DtC per-Load attribution          ← spec amendment + VOLL-priority design (Q5)
 └─ P18 pipeline UI + whole-report export (after P11/P12)
```

P10, P14 and P16's spec amendment can start in parallel. P11 is the highest-value item: it is what makes MVP-B honest.

---

## P10 — Hygiene (small, independent)

### P10a DSR preflight wiring (decision 15)
- In `eh_study.run_eh_study`, replace `solver_config_patch(pack)` with `solver_config_patch_with_preflight(pack, network=network, dsr_buses=dsr_buses)` (`archetypes.py:119`).
- New kwarg: `dsr_buses: list[str] | None = None`.
- **Where warnings go:** the `apply_pack` stage note, plus a new optional `ReferenceDesignReport.notes: list[str]`.
  - Today there is no generic notes field. Adding one is a contract change, so add it to `EXPORT_KEYS` and the golden fixtures (see P11 contract rule).
- Nothing is applied when the list is empty. Decision 15's "never silently global" stays.
- `_assumptions_hash` also hashes `dsr_price_eur_per_mwh` and `dsr_share_of_load`; today it hashes only `dsr_buses`.
- HTTP / chat exposure of `dsr_buses` lands in P13.
- **Tests:**
  - weak pack + `dsr_buses` → DSR tier present (`dsr_total_mwh` in capture);
  - a bus hosting a StorageUnit → double-count warning on the note;
  - no buses → "DSR stays OFF" warning;
  - strong pack → DSR ignored.

### P10b DtC correctness
1. **Stale fallbacks after P6(b).**
   - `dtc._bus_unserved_mwh` (`dtc.py:108-173`): `lost_load_t` columns are now **Load ids** (`:131-141`), and the `__voll_<bus>` name lookup is stale (`:159-160`).
   - Fix: roll `lost_load_t` up by `n.loads.bus` (or use `lost_load_load_period_mwh` + `loads.bus`). Remove the stale name match; keep the `involuntary_slack_mask` bus match.
   - **Test:** with `lost_load_bus_period_mwh` absent, the fallback yields the same MWh as the primary path.
2. **"Fixed plan" is not fixed [B14].**
   - `run_dtc_stress` re-solves a plain `network.copy()` with extendables still free (`dtc.py:227-245`; no `freeze_capacities`), yet labels itself `stress_fixed_plan` (decision 8: "stress-on-fixed-plan").
   - Fix: freeze with `sweep.freeze_capacities` (as the Class-B sweep does) and undo after.
   - Also strip `ens_cap_permyriad`, `ens_zone_cap_multiple` and `reserve_margin` for the frozen solve, as the sweep does (`sweep.py:298-299`). Frozen capacity plus a surviving margin is infeasible. [R5]
   - **Test:** an extendable generator does not grow under islanding in DtC stress; critical unserved > 0 where the frozen plan is short.
   - Update `test_energy_hub_dtc.py` expectations if values change.

### P10c Campaign estimate for `fmea_sweep` [B10]
- Actual cost is `(K+2 if K>0 else 0) + (C+2 if C>0 else 0)`. Class B and Class C each run their own `run_contingency_sweep` with a base solve and a closing restore (`fmea_sweep_runner.py:71-88`, `stress.py:525`, `sweep.py:268-366`).
- The estimate today is `K + C + 1` (`campaign.py:271-279`).
- Fix it, and update the pinning test `test_adequacy_campaign.py:236-248` against a counted run (instrumented `run_simulation`), as the frontier estimate is.

### P10d CI / environment
- `pixi.toml` `[dependencies] python = ">=3.10"` → `">=3.12"`.
  - The code already needs 3.12 (PEP 701 f-string, `gridspine/drivers/year_study.py:200`).
  - The lock resolves 3.12.13 on linux-64 and 3.13.0 on osx/win. 3.12.12 is only the `doc` env.
- **Frontend tests are not in CI at all.** Add a `gui-frontend-tests` job to `.github/workflows/test.yaml`:
  - node 22, `npm ci`, `npx vitest --run`, `npx tsc --noEmit -p .`
  - path filter `pypsa-gui/frontend/**`
- Promote `tests/test_energy_hub_study_isolation.py` (live HTTP) into the seal command list.

### P10e Locked network copy
- `run_eh_study` copies the **shared** network in the worker thread without the lock (`eh_study.py:185-186`). Take the copy under `lock`, and detach the model inside it.
- **Test:** a concurrent edit (held lock) blocks the copy rather than racing.

**Acceptance P10:** each item red → green; `gui-tests` green; FE CI job green on a PR.

**P10 status (2026-09-25, `claude/epic-allen-k2t1c4`):** implemented. Tests are in `tests/test_energy_hub_p10_hygiene.py` plus `test_adequacy_campaign.py`; all were shown red before each fix.

- [x] **P10a DSR preflight.**
  - `run_eh_study(dsr_buses=…)` goes through `solver_config_patch_with_preflight`.
  - Warnings are on the `apply_pack` note and in the new `ReferenceDesignReport.notes` (added to `EXPORT_KEYS` and the goldens; the FE renders them).
  - `_assumptions_hash` covers DSR price and share.
- [x] **P10b DtC.**
  - Fallbacks read the Load-keyed capture, rolled up by `loads.bus`. An authoritative bus roll-up is final, including 0.
  - Stress freezes capacities and strips the ENS cap, zone multiple and reserve margin.
  - **Also found and fixed:** P6(b) per-Load VOLL slacks had no per-snapshot bound, so a slack could "shed" more than its own Load and export the surplus over Links. The fix sets `p_max_pu = p_set / p_nom` in `services/solver/assumptions.py`.
  - The critical/non-critical split between coupled buses remains degenerate at equal VOLL. That is the Q5 premium (P16); P10 tests pin only the physical bounds.
- [x] **P10c:** the `fmea_sweep` estimate is `(K+2 if K) + (C+2 if C)`, pinned against counted runs.
- [x] **P10d:**
  - `pixi.toml` python `>=3.12`; `pixi lock --check` reports the lock is up to date.
  - `gui-frontend-tests` CI job (vitest + `tsc --noEmit`).
  - The seal glob `test_energy_hub_*.py` already includes the new files.
- [x] **P10e:** the shared network is copied under its RLock.
- [x] **QA gate** (independent) — `GO WITH BINDING CONDITIONS`. All four conditions were fixed with red → green tests:
  1. **DSR tier bounded per snapshot.** `p_max_pu = bus load(t) / peak`. It had the same "exceeds its own load, exports over Links" defect as the VOLL slack.
  2. **DSR preflight reads the private copy.** It now runs after the locked copy.
  3. **P10a acceptance asserts the capture.** The tests check `dsr_total_mwh > 0` and the `dsr_t` columns.
  4. **DtC on an unsolved network freezes at nameplate.** PyPSA holds `*_nom_opt = 0` before any solve, so the plan is set from `*_nom` on the copy. The docstrings are corrected.
- **Also from the gate:**
  - VOLL slacks are added in **one batched `n.add`**. That is 1.2 s for 1,000 Loads × 8,760 h, against ~18 s projected for a per-Load loop with time series.
  - `p_set` gaps now raise a phase warning instead of being zeroed silently.
  - The vacuous restore test was replaced by a spy on the islanded solve's bounds and cfg (zone multiple + margin stripped).
  - A real-capture parity test was added for the Load-keyed fallbacks.
  - FE note keys now use the index.
- **Behaviour change (release note):**
  - VOLL slacks (and the DSR tier) can no longer supply more than their own Load's (bus's) demand.
  - A shortfall caused by a non-Load sink (a Link with `p_min_pu > 0`, a Store minimum) is no longer "rescued" by phantom shedding. Such solves now report infeasible, which is the honest answer.
- **Known limitation (not P10):**
  - On multi-period networks with vintage bounds, `apply_vintage_bounds` (`assumptions.py` step 7) re-expands extendables after `freeze_capacities`. DtC stress and the existing Class-B sweep can therefore still build capacity there.
  - Tracked for P12 (fmea_top), which reuses the same freeze.

---

## P11 — `mc_certify` stage (spec decisions 1–2, §3 MVP-B)

**Engine (no new one):**
- `mc.snapshot_inputs(n, cfg=cfg)` (`mc.py:170`), under `lock`.
- Then the **baseline** `mc.mc_adequacy(inputs, draws=…, seed=…, cov_target=…, stop_event=stop_event)` (`mc.py:731`).
- No LP. It samples the solved plan via `copt.solved_capacity` (`copt.py:403-433`): extendables count at `p_nom_opt`.
- Guards to reuse from `mc_loop_runner.start_mc` (`:49-219`):
  - empty `inputs.units` → `not_established`;
  - `transition_probs` validity per unit.

### Fleet boundary [B1] — decide Q7 before coding
- The MC/COPT engine is **copper-plate and network-free**: "StorageUnits, Stores, Links and imports never enter" (`copt.py:8-9`, `_membership_walk` `:449-509`).
- A generator on the far side of an import Link is therefore counted as local firm capacity, whatever the pack did to the Link. Both MVP-B fixtures have a 200 MW `remote` gas generator on the `grid` bus (`test_energy_hub_mvp_b.py:44,82`). Gas has a carrier-default outage rate (`occurrence.py:165`), so the MC counts it.
- Without a fix, `off_grid` would "certify" on grid capacity the pack has cut off.
- **Required:** take the MC snapshot from an **MC-only copy with the hub boundary applied**. Remove every component on the **far side** of the selected import Links. Implement this as a helper `hub_boundary_copy(n, pack)` in `archetypes.py` that reuses `select_import_links`.
- **Finding the hub side [R1]:**
  1. Remove the selected Links and compute the connected components of the remaining bus graph (Lines, Transformers, the other Links).
  2. **Rule 2 (`eh_poc`):** the PoC buses mark the far side, and the hub is every component without a PoC bus.
  3. **Rule 1 (`eh_role=grid_import`):** the hub is the component holding the `eh_critical` buses; otherwise, the component with the largest weighted load.
     - If two components tie, or the hub's side can't be told apart from the far side → `not_established` "hub side ambiguous — tag `eh_poc` on the grid-side bus".
  4. **Rule 3 (carrier fallback):** **refuse** with `not_established` "tag `eh_role`/`eh_poc` to certify". The carrier rule also matches internal hub Links (e.g. `crit_flex` in the weak fixture), and cutting those would split the hub itself.
- Every far-side component is removed on the MC copy; the hub keeps its Stores/Storage, which MC ignores anyway.
- **How `weak_flexible` import enters the MC (Q7):**
  - (a) excluded — conservative, the default recommendation; or
  - (b) represented as one two-state unit of `import_p_nom_mw`, only when the Link has outage data (decision 6: import is a planning limit, not firm, unless outages are modelled).
- **Tests:**
  - `off_grid` `lole_h` == LOLE of the same network with the grid side deleted, and the `remote` generator is not in `inputs.units`;
  - a carrier-rule-only network → `not_established` "tag eh_role/eh_poc";
  - ambiguous components → `not_established`.

### Stage gating and order [B3, B5]
- Default pipeline keeps `mc_certify` when any of these holds:
  - `pack.mc_certify_required`;
  - `pack.availability.certification_metric == "mc_lole"` (`energy_hub.py:76`);
  - `target_lole_h is not None`.
- **Zero-solve stages are not budget-blocked.** `_blocked` (`eh_study.py:241-255`) must block `mc_certify` only on `failed_reason` / abort, never on `_remaining() <= 0`. Otherwise `test_budget_exhausted_before_stage_skips_it` (budget 1) silently drops a required certification.
  - **Test:** budget 1 → `mc_certify` still runs. Use a **certifying** fixture (≥168 h, max MTTR ≤ horizon). The existing 12 h `off_grid` budget test would return `not_established` under the MTTR rule (gas MTTR 50 h).
- **Execute stages in decision-18 order.** Today the frontier / fmea handling sits after `dtc_stress` (`eh_study.py:591-610`). Refactor the driver into a stage table iterated in `EH_PIPELINE_STAGES` order:
  - `frontier` → `mc_certify` → `fmea_top` → `redundancy` → `levers` → `dtc_stress` → `dtc_planning`.
  - **Test:** pipeline record order == execution order (instrumented).
- `solves_charged=0`, consistent with `campaign.py:22-25,255-258`. A separate draw ceiling applies (default `mc.MAX_DRAWS=2000`).
- **Abort:** only the baseline call gets `stop_event`. Extend F1j [B6]:
  - add `services/adequacy/eh_study.py` to the scan list and `run_eh_study` to `ALLOWED` (`test_adequacy_abort.py:670-672`);
  - extend its `seen_allowed` check (`:673-680`, which today reads only `mc_loop_runner.py`) to also require the `run_eh_study` call site. [R5]

### Time basis [B4, Q2]
- `mc_adequacy.lole_hours` is **per horizon (weighted)**. `time_basis == "hours_per_year"` only when the horizon is ~1 yr (`metrics.py:200`). The coupling loop already takes a **horizon-basis** target (`coupling_loop_runner.py:100-104`; the FE `wireTarget` multiplies by `horizon_years`, `LoopPanel.tsx:150-157`).
- **Rule:**
  - `AvailabilityTarget.target_lole_h` is **h/yr** — pin it in the docstring.
  - Certification compares `lole_hours` against `target_lole_h × horizon_years(n)`. This is the loop's convention.
  - Report both `lole_h_per_horizon` and `lole_h_per_year = lole_hours / horizon_years`.
  - Refuse (`not_established`) when `horizon_years ≤ 0`, or when the modelled hours are shorter than the largest unit MTTR. A calendar rule is the wrong test.
- **DoD fixtures:** the MVP-B fixtures (4 h and 12 h, `test_energy_hub_mvp_b.py:28,63`) cannot certify under that rule. Add certifying variants: ≥ 168 h with weightings, and MTTR ≤ the horizon. Keep the short fixtures for "refused with reason".

### Verdict [B3, Q1]
- Reuse the loop vocabulary (`coupling.py:508-529`: `met` = mean ≤ target; `confident` = CI upper ≤ target):
  - `pass` — CI upper ≤ target (confident);
  - `fail` — CI lower > target;
  - otherwise `inconclusive`.
- **Resolution floor:** a target below `resolution_floor_h` → `inconclusive`. Reuse `coupling_loop_runner.py:252-266`.
- `certified = True` only for `pass`.
  - `fail` and `inconclusive` → `False`.
  - Decision 2: `False` even when the ENS target is met.
  - No LOLE target, or certification `not_established` / aborted → `None`, with no verdict.
- If DSR is on (P10a), add a note to the payload: the MC does not model DSR, so its LOLE is pessimistic relative to the LP plan.

### Report contract [B2]
- New section `certification`. Its payload:
  ```
  {metric: "mc_lole", target_lole_h, target_basis: "h_per_year",
   horizon_years, lole_h_per_horizon, lole_h_per_year, lole_ci, eue_mwh, eue_ci,
   by_period, n_samples, converged, draws, seed, cov_target,
   resolution_floor_h, warning (MC_WARNING_V1), fleet_boundary, verdict,
   met_on_mean, confident}
  ```
- Contract changes, all in the same PR:
  - `REPORT_SECTIONS` + golden `mvp_a_report_skeleton.json` (`test_energy_hub_contract.py:122-128`);
  - `ReferenceDesignReport.certified: bool | None` + `EXPORT_KEYS` (`eh_report.py:26-42`) + golden `mvp_a_export_keys.json` (`test_energy_hub_report_p5.py:99-103`);
  - spec §4 amendment line.
- `mc_certify_required` and not run → `certification=not_established` with a reason. Remove the old "not implemented" text in `gates` for `off_grid`.
- The SCR orthogonality test (`test_weak_flexible_gates_ok_does_not_imply_mc_certify`) is re-pointed at `certification`.
- `mc_lole_h` (existing field) = `lole_h_per_year`.

### Redundancy cadence [B7]
- P3b pins `mc_certify_cadence="finalists_only"` (`redundancy.py:34,158`), but P11 certifies only the ENS plan.
- In P11: keep `mc_certify_cadence="finalists_only"`, which is pinned by `test_energy_hub_redundancy_select.py:18,116`. Add a **separate** payload field `finalists_mc_certified: false` with a note that finalists are ENS-only. [R3]
- Finalist MC certification is a follow-up (P11b, optional). It uses the same helper per finalist network, 0 LP solves.

### Tests
- Live `off_grid` + `weak_flexible` MVP-B on the certifying fixtures, **without** the `mc_certify_required=False` override. These become the real DoD; the short fixtures assert "refused with reason".
- Decision 2: a high-FOR fleet fails a low `target_lole_h` while ENS is met.
- Fleet boundary: see B1 above.
- Budget 1 still certifies.
- Abort mid-MC → `not_established`, no verdict.
- `solves_consumed` unchanged by MC.
- Pinned seed → deterministic output.
- Resolution-floor → `inconclusive`.
- The F1j scan includes `eh_study.py`.
- Live HTTP: the record carries `certified`.

### FE and chat
- **FE:** LOLE/yr headline with CI, a verdict chip, and `certification` in the completeness chips.
- **Chat:** update the `run_eh_study` description.

**P11 status (2026-09-25, `claude/epic-allen-k2t1c4`):** implemented. `tests/test_energy_hub_mc_certify.py` has 22 tests including a live HTTP run; 21 of them fail on pre-P11 code.

- [x] **Spec §4 amendment.** Adds the `certification` section, `certified`, per-year `mc_lole_h` and `notes`. `target_lole_h` is documented as h/yr.
- [x] **Contract goldens.** `REPORT_SECTIONS`, `EXPORT_KEYS`, and both golden fixtures are updated.
- [x] **`archetypes.hub_boundary_copy`.**
  - Hub side found per R1, via `select_import_links_with_rule`.
  - Carrier-only selection is refused; an ambiguous hub side is refused.
  - Q7 import units are built from Links with their own outage data; closed or undocumented Links are excluded, each with a reason.
- [x] **Driver refactored into a stage table.** `_STAGE_HANDLERS` runs in `EH_PIPELINE_STAGES` order, pinned by an instrumented test.
  - `mc_certify` is a zero-solve stage and is never budget-skipped (B5).
  - Verdict follows Q1, including the resolution floor. Time basis follows Q2, with the MTTR refusal.
  - An abort gives no verdict.
  - With DSR on, the payload carries the pessimism note.
  - Gates for `off_grid` are now `skipped`; missing certification lives on `certification`.
- [x] **F1j.** Now scans `eh_study.py` and exempts `_stage_mc_certify` by function. Exemptions must still call `mc_adequacy`. Mutation-verified.
- [x] **Redundancy disclosure.** The table carries `finalists_mc_certified: false`. The cadence pin is unchanged.
- [x] **Test update.** `test_energy_hub_study.py::test_run_marks_not_established_when_required_mc_missing` now asserts `certification`, not `gates`. This is the intended contract move.
- [x] **FE.** LOLE h/yr + CI (per year), a verdict chip, and `certification` in the chip order.
- [x] **Chat.** The `run_eh_study` description is updated.
- [ ] **QA gate** — pending.

---

## P12 — `frontier` and `fmea_top` stages

### P12a frontier
**Engine:** `frontier.run_frontier_sweep(network, lock, cfg, targets, *, stop_event=…)` (`frontier.py:166`).
- One LP per target, plus a closing `_restore_base` that cannot be disabled (it's in a `finally`). Cost is `len(targets)+1`, so ≤ 10 with the defaults.
- Needs `cfg.voll > 0`. At most 12 points.

**Pack scope [B8]:**
- New pack flag `frontier_default: bool`. It is True only for `strong_grid`, where spec §3 makes the frontier the deliverable. Weak and off-grid run it only when `stages` includes it explicitly.
- **Contract impact [R4]:**
  - update `tests/fixtures/eh_archetypes/strong_grid_pack.json` (plus the weak/off-grid fixtures with `false`), since `test_archetype_pack_fixtures_round_trip` (`test_energy_hub_contract.py:106-118`) compares against the factory;
  - `pack_hash` changes for **every** pack (`archetypes.py:30-33`). Note this in the PR: stored reports from before are not hash-comparable.
- When run, the number of points is `min(remaining, max(2, floor(0.4 × budget_solves)), 12)`. With Q4, EH skips the closing restore, so no solve is reserved for it. Without Q4, use `remaining - 1`.
- Targets: the pack cap is **always** kept. Fill from `DEFAULT_TARGETS_PERMYRIAD`, nearest to the pack cap first, then order loosest first per `_validate`.
- Fewer than 2 points fit → `skipped` with the "budget" reason.

**Isolation:** run on **its own `network.copy()`**. Its re-solves overwrite `p_nom_opt`, and `mc_certify` / `fmea_top` must see the `ens_solve` plan. The closing restore then lands on the throwaway copy.
- Q4: add `restore_base: bool = True`; EH passes False on its disposable copy.
- **fmea exclusions:** `run_class_b_sweep` takes no exclusion parameter. **Pre-filter on the copy** instead: drop Links the pack closed, so no engine change is needed.
- Test that no HTTP route passes False (grep-style, like F1j).

**Report:**
- `sections.frontier.payload = {points, knee_index (frontier.knee_index), aborted, restore_skipped_on_private_copy}`.
- `ok` when ≥2 points solved.
- Keep ex-shed + period basis (decision 3).

### P12b fmea_top
**Engine:** `sweep.run_class_b_sweep(network, lock, cfg, …)` (`sweep.py:453`).
- K = `class_b_contingencies(n)`, max 20 (`SweepBudgetError`).
- Cost: frozen base + K + restore = K+2.
- It freezes capacities (needs the `ens_solve` `p_nom_opt`). Run it on a copy of the post-`ens_solve` private network.

**Edge cases [B9]:**
- K = 0 costs 0 solves (early return, `sweep.py:465-476`). This is typical on the MVP-B fixtures, because AC Links have no carrier default. → `not_established` "no Class-B-eligible Links (no occurrence data)".
- K > 20 (`SweepBudgetError`) → `not_established` with a reason.
- K+2 > remaining → `skipped` with the "budget" reason. No partial sweep: a partial top-N is misleading.
- Exclude import Links the pack already closed (`off_grid` `p_*_pu→0`) by pre-filtering them off the fmea copy.

**Top-N:**
- re-sort by `(-criticality_eur_per_year, mode_id)` (worksheet rule, `test_adequacy_abort.py::test_F1k`);
- drop rows with `failure_mode=None`, counting them in `unsolved`;
- N = 5.
- Payload: `{rows, k_links, unsolved, in_metric_scope_counts, basis: "pack_applied_ens_plan"}` + `FMEA_TOP_LINK_PRIMARY_NOTE` (decision 14).

**Spec §9 amendment:** "every frontier point gets its own FMEA ranking" is **deferred**. EH ranks only the `ens_solve` plan. Record this in the spec.

### Budget test [B8]
- Default `weak_flexible` and `off_grid` at budget 30, on a fixture with K ≥ 5 outage-rated Links: levers and DtC still reach `ok`.
- Default `strong_grid` at 30: frontier `ok` with the pack cap among the points.
- **Tests that change** (default-pipeline expectations): `test_energy_hub_study.py:77,126` and `test_energy_hub_report_p5.py:133`.

### Other tests
- Frontier monotone in cost.
- `mc_certify` output identical with and without frontier (copy isolation).
- FMEA ordering = worksheet rule.
- K=0, K>20, and budget-short cases.
- Abort between frontier points.
- The FMEA fixture needs `outage_rate_value` set on the Links.

**FE:** frontier table (target, cost, achieved ENS) + CSV; FMEA top-N table + CSV. Reuse `downloadCSV`.

---

## P13 — Pack parameters over HTTP / UI / chat

**Backend (`eh_study_runner.EhStudyRequest`):** optional fields.

```
pack_overrides: {
  ens_cap_permyriad?: float>0, target_lole_h?: float>=0,
  certification_metric?: "mc_lole"|"none",
  import_p_nom_mw?: float>=0, mc_certify_required?: bool,
  frontier_default?: bool, dtc_stress_default?: bool, dtc_planning_default?: bool,
  levers?: {redundancy?, import_cap?, storage_duration?}
}
dtc_config?: DtcConfig
dsr_buses?: list[str]
mc?: {draws?: int, seed?: int, cov_target?: float}
```

- Merge onto the factory pack via `model_copy(update=…)`, then **re-validate** through `ArchetypePack.model_validate`. Errors → 422 with the field path.
- `pack_hash` reflects the overrides.
- Unknown DtC ids / buses → 422 **before** the worker starts, so no publish happens (the #51 guard discipline).

**Chat:**
- Fully-specified nested object schemas, following the asset-health `entries` pattern (`chat_tools_schema.py:832-850`).
- Python defaults for every optional field (`test_tool_schema_signature_consistency.py`).
- Campaign charge unchanged (`budget_solves`; MC is 0).

**FE:** a collapsible "Pack settings" block:
- string-state numeric inputs;
- Run is disabled on invalid input, with the reason in `title`;
- **omit blank fields** (the `LoopPanel.tsx:340-353` pattern);
- a stages multi-select and a budget input;
- `target_lole_h` labelled **h/yr** (P11 rule).

**Tests:**
- HTTP live: the weak pack with an `ens_cap_permyriad` override is feasible on the MVP-B fixture, where the default is infeasible;
- invalid override → 422 with no record;
- `pack_hash` differs;
- schema ↔ signature consistency;
- FE omits blank fields.

---

## P14 — EH network tagging + readiness

**Backend:**
- **Blockers today:**
  - `_drop_unknown_extras` (`services/network_crud.py:111-130`) keeps only catalog inputs or **existing** columns, so a first `eh_*` write is silently dropped (pinned by `tests/test_extras_passthrough.py:33-69`);
  - `/_bulk` returns 400 on unknown columns (`network_bulk.py:376-382`).
- **One shared role vocabulary [B11]:**
  - Today the roles are split across `archetypes.py` (`grid_import`), `redundancy.py:47-54,400` (`_IMPORT_ROLES`, `_CONVERSION_ROLES`, `eh_n1_conversion`) and `levers.py:57` (`eh_import`, `import`).
  - Move them into one `EH_LINK_ROLES` constant, e.g. in `models/energy_hub.py`. All three modules import it.
  - The whitelist validates against it. Do not use a two-value Literal, which would break existing roles.
- **Keep separate subsets [R5]:** `EH_IMPORT_ROLES`, `EH_CONVERSION_ROLES`, and `""`.
  - **§6 selection rule 1 stays `grid_import` only.** If `select_import_links` matched `eh_import`/`import`, the set of Links each pack applies to would widen silently.
  - **Test:** a Link tagged `eh_import` is not selected by rule 1.
- Typed whitelist:
  ```python
  EH_CUSTOM_COLUMNS = {
      "Bus":  {"eh_poc": bool, "eh_critical": bool,
               "eh_sk_mva": float, "eh_ibr_mva": float},
      "Link": {"eh_role": EH_LINK_ROLES},
  }
  ```
- Whitelisted keys create the column with a typed default. Non-whitelisted keys keep today's behaviour, so the extras-passthrough test stays true for them.
- **Bool normalisation [B11]:** `occurrence.normalise_flag_column` (`occurrence.py:78`) handles only the generator `p_max_pu_includes_outages` flag, called from `export_network_to_netcdf` (`pypsa_service.py:645-663`).
  - Add a generic helper for whitelisted bool columns.
  - Wire it into netCDF export and import, and into Excel import.
- Declare the fields on `BusCreate` / `LinkCreate` (`schemas.py`) for OpenAPI.
- `GET /results/eh_readiness?archetype=…` — a **read-only** preflight built from the driver's own selectors and budget estimator, shared code, not restated:
  - selected import Links + which §6 rule matched;
  - critical buses;
  - PoC SCR coverage;
  - DtC derivability;
  - storage presence;
  - Class-B K;
  - the MC fleet boundary (P11);
  - per-stage solve estimate vs budget.

**FE:**
- An "Energy Hub" section on `BusPanel` (`PropertiesPanel.tsx:1581`) and `LinkCard` (`:1090`), next to "Adequacy", using `cardKit` inputs.
- The EH panel shows readiness before Run.

**Tests:**
- PUT/`_bulk` create the whitelisted columns;
- non-whitelisted keys are still dropped;
- netCDF + xlsx round-trip keeps dtype;
- all three role consumers use the shared constant;
- readiness matches what the driver then does (live);
- FE card edit → PUT body.

---

## P15 — Class-C authoring UI (handover priority 1)

**Backend exists:**
- `GET/PUT /api/projects/{name}/stress_scenarios` (`routers/adequacy_worksheet.py:64-77`): whole-list replace, 422 on `StressValidationError`.
- Rules are in `stress.py:50-210`:
  - ≤10 scenarios;
  - id `[a-z0-9_-]{1,64}`;
  - `kind ∈ {parametric, profiles}`;
  - frequency in (0, 365];
  - parametric multipliers bounded;
  - `profiles` uses inline series or `profile_pack`.

**Gap:** `profile_pack` ids resolve against `backend/tests/fixtures/eh_class_c/` (`stress.py:59-62`). Shipped code reads from the test tree.
- Move the packs to `backend/data/eh_class_c/`; tests point at the same files.
- Add `GET /adequacy/profile_packs`.
- Make sure `check_bundle.py` / `pypsa-gui.spec` include the data dir. The packaging test covers it.

**FE:**
- `putStressScenarios` client.
- A scenario editor on `FmeaTab`, following the `putWorksheet` pattern (`FmeaTab.tsx:63-72`):
  - add/edit/delete;
  - parametric bounds mirrored client-side;
  - `profile_pack` picker;
  - 422 surfaced verbatim;
  - inline profile upload deferred.

**Tests:**
- FE round-trip;
- a bounds violation shows the backend 422;
- pack resolution from the data dir;
- packaging includes the dir.

---

## P16 — DtC per-Load attribution (spec amendment + design first)

**Why:**
- P6(b) created one VOLL slack per Load.
- `last_lost_load.lost_load_load_period_mwh` is keyed by Load id (`services/solver/assumptions.py:832-872`).
- Decision 8 / §10's premise ("one slack per bus") no longer holds.

**Blocking design issue [B12]:**
- Every per-Load slack bids the same `cfg.voll` (`assumptions.py:771-786`).
- On a shared bus, the LP's split of shed between critical and non-critical Loads is **degenerate and arbitrary**. Per-Load numbers would be solver artefacts.
- Pick one (Q5):
  - (a) **VOLL priority:** critical Loads' slacks get `voll × (1 + ε_crit)`. This is a documented priority, so the LP sheds non-critical first. It affects cost only via the ε term; report ε.
    - **Scope it to the DtC stage's private cfg only** [R5], so it never changes `ens_solve`, the frontier, or the user's own solve.
  - (b) **Bounds:** report critical unserved as the interval `[0, bus_total]` on shared buses, and exact values only for critical-only buses.

**Contract:**
- `DtcConfig.attribution: Literal["bus_aggregate_not_per_load", "per_load"]`.
- **Default stays `bus_aggregate_not_per_load`; `per_load` is opt-in.** There is no `"auto"`.
- `per_load` refuses when the capture lacks Load keys.

**Steps:**
1. Amend spec decision 8 / §8 non-goal / §10.
2. Stress: critical unserved = Σ over the critical Loads from the Load-keyed capture, under (a) or (b). Drop the different-bus requirement only for `per_load`.
3. Planning: retained-critical demand by **Load**. System ENS stays the planning metric.

**Tests:**
- The refusal test becomes "refuses per_load without Load keys".
- Shared-bus fixture: critical vs non-critical separated under (a), or bounded under (b).
- Bus-aggregate regression unchanged.
- Under (a): on a shared bus short by X MWh, the **non-critical Loads are shed first**. Critical unserved = max(0, X − non-critical demand). [R5: "same result twice" can't detect a degenerate split]
- `ens_solve` / frontier costs are unchanged when DtC `per_load` is on (ε scoping).

---

## P17 — Energy import cap (spec §6 amendment first; lowest priority)

**Spec:** §6 currently says apply "must NOT add a GlobalConstraint". Amend it to allow this constraint, which is enforced via `extra_functionality` and is not a PyPSA GC row.

**Constraint `[B13]`:** `_wrap_with_import_energy_cap` in `services/solver/adequacy.py`, modelled on `_wrap_with_ens_cap` (`:225`).
- **Direction [R2]:** find the hub side with the P11 `hub_boundary_copy` logic.
  - v1 meters only Links oriented **grid → hub**: `bus0` on the grid side. Import energy at the hub bus = `p0 × efficiency`.
  - A one-way Link oriented hub → grid can never import: PyPSA defines `p1 = −efficiency·p0`, so `−p1 = efficiency·p0`, which is **export**. Such a Link is ignored with a stage note, or refused if it is the only selected Link.
- **Bidirectional Links** (`p_min_pu < 0`): refuse in v1 with a preflight error. Capping only the import direction needs positive-part auxiliary variables, which is deferred.
- **Weights:** the `generators` weight column **without** the `investment_period_weightings.years` multiplier (`period_utils.py:80-108` includes it).
  - Per period P: `Σ_t w_t · import_t ≤ E × Σ_t w_t / 8760`.
- Refuse rolling / myopic (as the ENS cap does).

**Config:** `SolverConfig.import_energy_cap_mwh_per_year` + `import_energy_links`, set only from the pack overlay. Never a user global.

**Pack:** `apply_archetype_pack` stops warning "reserved" for `weak_flexible` and sets the cfg fields instead.

**Lever:** `import_energy` kind in `levers.py`:
- default MWh ladder;
- `OptimizationLevers.import_energy`;
- a cfg-based branch in `apply_lever_scenario`;
- the "ineffective" check extended.

**Tests:**
- A binding cap raises cost monotonically.
- A multi-period network with years weighting honours the per-year cap.
- A grid → hub Link is metered as `p0 × efficiency`; a hub → grid one-way Link is ignored with a note.
- A bidirectional Link is refused.
- Rolling is refused.
- The lever gives ≥2 differentiated options.

---

## P18 — Pipeline UI + whole-report export

**Panel:**
- A collapsible "Pipeline" table: stage, status (`run` / `skipped` / `aborted` / `failed`), solves charged, note.
- `solves_consumed / budget_solves` already ships.

**Export:**
- `GET /results/eh_reference_design` already returns the stable `export_reference_design` shape. Add a **Download JSON** button that saves that body.
- Optionally, a flat CSV of headline + completeness + notes.

**Tests:** FE table renders all statuses; download is called with the export body.

---

## Decisions (product owner, 2026-09-25)

Each decision took the recommended option.

| # | Question | Decision |
|---|---|---|
| Q1 | Certification verdict rule | **CI upper ≤ target.** `pass` iff CI upper ≤ target; `fail` iff CI lower > target; else `inconclusive`. `certified=True` only on `pass`. Resolution-floor guard; loop vocabulary (`met_on_mean`, `confident`) kept in the payload |
| Q2 | Time basis for h/yr targets | **Scale + MTTR floor.** Compare against `target_lole_h × horizon_years`; report per-year and per-horizon values. `not_established` when `horizon_years ≤ 0` or modelled hours < max MTTR. Add ≥168 h certifying fixtures |
| Q3 | Default budget | **Keep 30** (decision 17). Frontier is on by default only for `strong_grid`, capped at ~40% of the budget |
| Q4 | Frontier restore on a disposable copy | **Opt-out flag.** `restore_base=True` default; EH passes False on its private copy; a test guards that no HTTP route passes False |
| Q5 | DtC per-Load | **Yes, with a VOLL premium.** Amend decision 8 / §10. Critical Loads' slack gets a disclosed ε premium scoped to the DtC stage cfg only. `per_load` is opt-in; bus-aggregate stays the default |
| Q6 | Energy import cap | **Build it last.** P17 after P13, behind a §6 amendment. Grid→hub Links only; bidirectional Links refused in v1 |
| Q7 | Import in MC certification | **Exclude unless outage data.** Hub-side assets only. An import Link enters as one two-state unit of `import_p_nom_mw` only when it has outage-rate data (decision 6) |

## QA gate record
- **2026-09-25 — plan gate:** `GO WITH BINDING CONDITIONS`. B1–B14 incorporated above:
  - B1 fleet boundary (P11)
  - B2 contract/goldens (P11)
  - B3 verdict semantics (P11)
  - B4 time basis / fixtures (P11)
  - B5 zero-solve gating + decision-18 execution order (P11)
  - B6 F1j scan (P11)
  - B7 redundancy cadence disclosure (P11)
  - B8 pack-scoped frontier + budget test (P12)
  - B9 fmea edge cases + §9 deferral (P12)
  - B10 campaign formula (P10c)
  - B11 shared role vocabulary + generic bool normaliser (P14)
  - B12 VOLL degeneracy (P16)
  - B13 import direction/metering/weights + §6 amendment (P17)
  - B14 DtC fixed-plan freeze (P10b)

- **2026-09-25 — plan re-gate:** `GO WITH BINDING CONDITIONS`. RESOLVED: B2–B6, B8–B12, B14. PARTIAL: B1, B7, B13. New conditions, all folded in above:
  - R1 hub-side rule (P11)
  - R2 import metering (P17)
  - R3 separate cadence field (P11)
  - R4 pack fixture / `pack_hash` + budget-share formula (P12)
  - R5 notes: margin strip, `seen_allowed`, rule-1 guard, ε scoping, shed-order test

## Per-phase DoD (all phases)
- Red → green evidence.
- The phase's live HTTP test is unstubbed.
- `pixi run gui-tests` green, and the FE CI job (P10d) green.
- Spec amendment merged where required.
- Plan checkbox + gate verdict recorded here.
- The seal command list is updated when new test files are added.
