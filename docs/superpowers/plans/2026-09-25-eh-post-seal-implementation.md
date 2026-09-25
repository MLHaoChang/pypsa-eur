# Energy Hub reference design — post-seal implementation plan

**Status:** draft for review (2026-09-25)
**Source of the TODO list:** [`findings/2026-09-25-eh-handover-assessment-claude.md`](../findings/2026-09-25-eh-handover-assessment-claude.md) §3–§4
**Parent plan / spec:** [`2026-09-14-eh-reference-design-gaps.md`](2026-09-14-eh-reference-design-gaps.md), [`specs/2026-09-14-eh-reference-design.md`](../specs/2026-09-14-eh-reference-design.md)
**Base:** `master` + `claude/epic-allen-k2t1c4` (isolation / budget / honest-failure fixes). Every phase below assumes those fixes. In particular, `run_eh_study` runs on a private network + cfg copy and enforces `budget_solves`.

**Working rules (unchanged from the parent plan):**
- TDD: red first, then green.
- Independent QA gate (`GO` / `GO WITH BINDING CONDITIONS` / `NO-GO`) per phase.
- One phase per PR.
- Build on existing engines; no parallel adequacy stack.
- Honesty over completeness: when something can't be established, report `not_established` with a reason; never invent a value.
- Every phase adds at least one **unstubbed HTTP** EH test. The #52 seal's blind spot was that `test_energy_hub_study_http.py` stubs the driver.

---

## Dependency order

```
P10 hygiene (DSR preflight, DtC stale fallbacks, campaign FMEA estimate, CI)
 ├─ P11 mc_certify stage ──────────────┐
 │    └─ P12 frontier + fmea_top stages ┤ (budget interplay → Decision Q3)
 ├─ P13 pack parameters (HTTP/UI/chat) ─┤ (needs P11 for target_lole_h / certify flags)
 │    └─ P17 energy import cap (+ lever)│
 ├─ P14 EH network tagging + readiness ─┤
 │    └─ P15 Class-C authoring UI       │
 ├─ P16 DtC per-Load attribution (spec amendment first)
 └─ P18 pipeline UI + whole-report export (after P11/P12 so the stage table is worth showing)
```

P10, P14 and P16's spec amendment can start in parallel. P11 is the highest-value item: it is what makes MVP-B honest.

---

## P10 — Hygiene (small, independent)

### P10a DSR preflight wiring (decision 15)
- In `eh_study.run_eh_study`, replace `solver_config_patch(pack)` with `solver_config_patch_with_preflight(pack, network=network, dsr_buses=dsr_buses)` (`archetypes.py:119`).
- New kwarg: `dsr_buses: list[str] | None = None`.
- Warnings go to the `apply_pack` stage note and to a `levers`-independent report note. Nothing is applied when the list is empty; decision 15's "never silently global" stays.
- `_assumptions_hash` also hashes `dsr_price_eur_per_mwh` and `dsr_share_of_load`; today it hashes only `dsr_buses`.
- HTTP / chat exposure of `dsr_buses` lands in P13. Until then only the driver kwarg exists.
- **Tests:** weak pack + `dsr_buses` gives a DSR tier with `dsr_total_mwh` in the capture. A bus hosting a StorageUnit gives the double-count warning on the stage note. No buses gives the warning "DSR stays OFF". The strong pack ignores DSR.

### P10b DtC stale fallbacks after P6(b)
`dtc._bus_unserved_mwh` (`dtc.py:108-173`) has two fallbacks that silently stopped working after P6(b):
- `lost_load_t` columns are now **Load ids**, not buses (:131-141);
- the `__voll_<bus>` name lookup is stale (:159-160).

Fix: roll `lost_load_t` up by `n.loads.bus`, or drop that fallback in favour of `lost_load_load_period_mwh` + `loads.bus`. Remove the stale name match and keep the `involuntary_slack_mask` bus match.
- **Tests:** force the primary key (`lost_load_bus_period_mwh`) to be absent and assert the fallback still yields the same critical / non-critical MWh as the primary path.

### P10c Campaign estimate for `fmea_sweep`
- `campaign.py:272-279` estimates the Class-B sweep at K+1. `run_contingency_sweep` actually does 1 frozen base + K contingencies + 1 closing restore = **K+2** (`sweep.py:268-366`).
- Fix the estimate, and pin it against a counted run (instrumented `run_simulation`), as the frontier estimate is.
- Verify first. If a test already pins K+1, check whether the base solve is shared with something else before changing it.

### P10d CI / environment
- `pixi.toml` `[dependencies] python = ">=3.10"` → `">=3.12"`. The code already needs 3.12 (PEP 701 f-string in `gridspine/drivers/year_study.py:200`), and the lock already resolves 3.12.12/3.12.13. The floor stops a fresh solve from picking 3.11.
- **Frontend tests are not in CI at all**: no workflow runs `npm`/`vitest`. Add a `gui-frontend-tests` job to `.github/workflows/test.yaml`:
  - node 22, `npm ci`, `npx vitest --run`, `npx tsc --noEmit -p .`
  - path filter `pypsa-gui/frontend/**`
- Promote `tests/test_energy_hub_study_isolation.py` (live HTTP) into the handover's seal command list.

**Acceptance P10:** each item has a red → green test; `gui-tests` green; the new FE CI job green on a PR.

---

## P11 — `mc_certify` stage (spec decisions 1–2, §3 MVP-B)

**Engine (no new one):**
- `mc.snapshot_inputs(n, cfg=cfg)` (`mc.py:170`), taken under `lock`.
- Then `mc.mc_adequacy(inputs, draws=…, seed=…, stop_event=stop_event)` (`mc.py:731`).
- No LP. It samples the **solved** plan through `copt.solved_capacity`, so an extendable row counts at `p_nom_opt`. It must run after `ens_solve`, on the same private network.
- Reference call sequence and guards: `mc_loop_runner.start_mc` (`mc_loop_runner.py:49-219`):
  - empty `inputs.units` → refuse;
  - check `transition_probs(u.q, u.mttr_hours)` per unit.

**Driver changes (`eh_study.py`):**
- Add `"mc_certify"` to `IMPLEMENTED`.
- Default pipeline keeps `mc_certify` when `pack.mc_certify_required` **or** `pack.availability.target_lole_h is not None`; otherwise it is `skipped`.
- The stage is blocked (skipped + reason) when `ens_solve` failed. The **solve budget is not charged** (`solves_charged=0`, consistent with `campaign.py:22,255-258`). A separate `max_draws` ceiling applies (default `mc.MAX_DRAWS=2000`, which is the engine cap).
- Only the baseline `mc_adequacy` call passes `stop_event`. ELCC/loop replays must not (`test_adequacy_abort.py::test_F1j`). EH calls the baseline only.
- Populate `ReferenceDesignReport.mc_lole_h`.
- **Time basis:** `lole_hours` is per horizon, and `time_basis == "hours_per_year"` only for ~1-year horizons (`metrics.py:200`).
  - `target_lole_h` is h/yr.
  - Either annualise (`lole_hours / horizon_years`) with an explicit `annualised_from_horizon_years` field and note, or refuse to certify on non-annual horizons. → **Decision Q2.**

**Report shape:**
- Add a report section `certification`. This is additive: `REPORT_SECTIONS` grows by one, and the FE `completenessRows` already appends unknown names. It is kept separate so it doesn't fight SCR for `gates` on `weak_flexible` — today, the missing certification on weak is visible nowhere.
- Payload:
  ```
  {metric: "mc_lole", target_lole_h, lole_h, lole_ci, eue_mwh, eue_ci,
   n_samples, converged, seed, time_basis, horizon_years, verdict}
  ```
- `verdict ∈ {"pass", "fail", "inconclusive"}` → **Decision Q1** (mean vs CI rule).
- `ReferenceDesignReport` gets `certified: bool | None`.
  - Decision 2: `certified=False` whenever LOLE fails, even if the ENS target is met.
  - `None` when no LOLE target is set or certification is `not_established`.
- `mc_certify_required` and not run (not requested / blocked / aborted / no units) → `certification=not_established` with the reason. Remove the old "not implemented" text in `gates` for `off_grid`.

**Tests:**
- live `off_grid` + `weak_flexible` MVP-B runs **without** the `mc_certify_required=False` override. Update `test_energy_hub_mvp_b.py` so these become the real DoD.
- pass / fail fixtures: a fleet with high FOR fails at a low `target_lole_h`, while ENS is met (decision 2).
- abort mid-MC.
- `solves_consumed` unchanged by MC.
- seed pinned → deterministic `lole_h`.
- non-annual horizon handled per Q2.
- SCR orthogonality test still holds (`test_weak_flexible_gates_ok_does_not_imply_mc_certify`, re-pointed at `certification`).

**FE:**
- LOLE headline chip with CI, plus a certification verdict chip.
- `certification` shows up in the completeness chips.

**Chat:**
- `get_adequacy_results('eh_reference_design')` passes it through unchanged.
- Update the `run_eh_study` description (mc_certify no longer "not executed").

---

## P12 — `frontier` and `fmea_top` stages

### P12a frontier
**Engine:** `frontier.run_frontier_sweep(network, lock, cfg, targets, *, stop_event=…)` (`frontier.py:166`).
- One LP per target, plus a closing `_restore_base` that cannot be disabled (it's in a `finally`).
- Needs `cfg.voll > 0`, else `FrontierConfigError`.
- At most 12 points (`MAX_FRONTIER_POINTS`).

**Isolation:** run it on **its own `network.copy()`** (detach the model first). Its re-solves overwrite `p_nom_opt`, and `mc_certify` / `fmea_top` must see the `ens_solve` plan. The closing restore then happens on the throwaway copy.
- Cost: frontier costs `len(targets)+1` solves. Accept that, or add an opt-out kwarg to `run_frontier_sweep` for callers that pass a disposable copy. **Decision Q4.**

**Targets:**
- default `DEFAULT_TARGETS_PERMYRIAD` ∪ {pack cap};
- truncated to `remaining_budget - 1` (restore);
- loosest first, per `_validate`;
- if fewer than 2 points fit → `skipped`, reason "budget".

**Report:**
- `sections.frontier.payload = {points, knee_index (frontier.knee_index), base_restored, aborted}`.
- Status `ok` when ≥2 points solved; otherwise `not_established`.
- Keep the "excludes shed cost" + period basis fields (decision 3).

### P12b fmea_top
**Engine:** `sweep.run_class_b_sweep(network, lock, cfg, …)` (`sweep.py:453`).
- K = `class_b_contingencies(n)`, max 20.
- Cost: frozen base + K + closing restore = K+2 solves.
- It freezes capacities, so it needs the `ens_solve` `p_nom_opt`. Run it on a copy of the post-`ens_solve` private network.

**Budget:** pre-count K. If K+2 > remaining → `skipped` with reason (no partial sweep: a partial top-N ranking is misleading).

**Top-N:**
- re-sort rows by `(-criticality_eur_per_year, mode_id)`, the worksheet rule (`test_adequacy_abort.py::test_F1k`);
- drop rows with `failure_mode=None`, counting them in `unsolved`;
- N = 5 by default.

**Report:**
- `sections.fmea_top.payload = {rows[:N], k_links, unsolved, in_metric_scope counts, note}`.
- Keep `FMEA_TOP_LINK_PRIMARY_NOTE` (decision 14).
- The ranking uses the **pack-applied** plan, and the payload says so.

### Budget interplay
Rough solve counts on the default pipeline:
- ens 1
- frontier ≤ 9
- redundancy 4–8
- levers 3–6
- DtC ~N_links
- fmea K+2

**Decision Q3:** keep 30 and document the stage order as the priority (later stages are skipped with the "budget" reason), **or** raise `DEFAULT_EH_BUDGET_SOLVES`. Spec decision 17 pins 30; raising it needs a spec edit.

**Tests:**
- live frontier over ≥2 points is monotone in cost;
- `mc_certify` still sees the ens plan after the frontier (compare `lole_h` with/without frontier);
- fmea top-N ordering matches the worksheet rule;
- a budget too small for fmea gives `skipped` with the "budget" reason;
- abort between frontier points.

**FE:** a small frontier table (target, cost, achieved ENS) + CSV, and a top-N FMEA table + CSV. Reuse `downloadCSV`.

---

## P13 — Pack parameters over HTTP / UI / chat

**Backend (`eh_study_runner.EhStudyRequest`):** optional fields.

```
pack_overrides: {
  ens_cap_permyriad?: float>0,
  target_lole_h?: float>=0,
  import_p_nom_mw?: float>=0,
  mc_certify_required?: bool,
  dtc_stress_default?: bool,
  dtc_planning_default?: bool,
  levers?: {redundancy?, import_cap?, storage_duration?}
}
dtc_config?: DtcConfig
dsr_buses?: list[str]
```

- Merge onto the factory pack with `model_copy(update=…)`, then **re-validate** through `ArchetypePack.model_validate`. Any pydantic error → 422 with the field path.
- `pack_hash` changes accordingly, since it is computed from the merged pack.
- Unknown DtC ids / buses → 422 before the worker starts, so no worker publish happens (the guard-path discipline from #51).

**Chat:**
- `run_eh_study` gets fully-specified nested object schemas, following the asset-health `entries` pattern (`chat_tools_schema.py:832-850`).
- Python defaults for every optional field (`test_tool_schema_signature_consistency.py`).
- Campaign charge is unchanged (`budget_solves`).

**FE:** a collapsible "Pack settings" block in `EhReferenceDesignPanel`:
- string-state numeric inputs;
- Run is disabled on invalid values, with the reason in `title`;
- fields are **omitted from the body when blank** (the `LoopPanel.tsx:340-353` pattern), so the FE never invents a default;
- a stages multi-select and a budget input.

**Tests:**
- HTTP live: weak pack with `ens_cap_permyriad` override is feasible on the MVP-B fixture, where the default pack is infeasible;
- invalid override → 422 with no record;
- `pack_hash` differs;
- chat schema ↔ signature consistency;
- FE: body omits blank fields.

---

## P14 — EH network tagging + readiness

**Backend:**
- Blockers today:
  - `_drop_unknown_extras` (`services/network_crud.py:111-130`) keeps only catalog inputs or **existing** columns, so a first `eh_*` write is silently dropped (`tests/test_extras_passthrough.py:33-69` pins that).
  - `/_bulk` returns 400 on unknown columns (`network_bulk.py:376-382`).
- Add a typed whitelist:
  ```python
  EH_CUSTOM_COLUMNS = {
      "Bus":  {"eh_poc": bool, "eh_critical": bool,
               "eh_sk_mva": float, "eh_ibr_mva": float},
      "Link": {"eh_role": Literal["", "grid_import"]},
  }
  ```
- Whitelisted keys create the column with a typed default (False / NaN / ""). Non-whitelisted keys keep today's behaviour; the extras-passthrough test stays true for them.
- Bool columns must be real `bool` dtype, or netCDF export fails (`pypsa_service.normalise_flag_column`, `:650-662`). Normalise on write and on import.
- Declare the fields on `BusCreate` / `LinkCreate` (`schemas.py`) like `outage_rate_*`, for OpenAPI and docs.
- `GET /results/eh_readiness?archetype=…` — a **read-only preflight** that reuses the driver's own selectors, so there is no second rule set:
  - selected import Links + which §6 rule matched (`select_import_links`);
  - critical buses;
  - PoC SCR coverage (`scr_gate` inputs);
  - DtC derivability;
  - storage presence (lever soft-skip);
  - Class-B K;
  - an estimated solve count per stage vs budget.

**FE:**
- An "Energy Hub" section on `BusPanel` (`PropertiesPanel.tsx:1581`) and `LinkCard` (`:1090`), next to the existing "Adequacy" sections, using `cardKit` inputs.
- The EH panel shows the readiness summary before Run.

**Tests:**
- PUT/`_bulk` create the whitelisted columns;
- a non-whitelisted key is still dropped;
- netCDF + xlsx round-trip keeps dtype;
- the readiness summary matches what the driver then does (live);
- FE card edit → PUT body.

---

## P15 — Class-C authoring UI (handover priority 1)

**Backend exists:**
- `GET/PUT /api/projects/{name}/stress_scenarios` (`routers/adequacy_worksheet.py:64-77`): whole-list replace, 422 on `StressValidationError`.
- Registry rules are in `stress.py:50-210`:
  - ≤10 scenarios;
  - id `[a-z0-9_-]{1,64}`;
  - `kind ∈ {parametric, profiles}`;
  - frequency in (0, 365];
  - parametric multipliers are bounded;
  - `profiles` uses inline series or `profile_pack`.

**Gap found:** `profile_pack` ids resolve against `backend/tests/fixtures/eh_class_c/` (`stress.py:59-94`). Shipped code reads from the test tree, and the frozen app won't contain it.
- Move the packs to a data dir (e.g. `backend/data/eh_class_c/`), keeping the tests pointed at the same files.
- Add `GET /adequacy/profile_packs` to list them.
- Check `check_bundle.py` / `pypsa-gui.spec` include the new data dir.

**FE:**
- `putStressScenarios` client.
- A scenario editor on `FmeaTab`, following the worksheet `putWorksheet` pattern (`FmeaTab.tsx:63-72`):
  - add/edit/delete;
  - parametric fields with the backend bounds mirrored client-side;
  - a `profile_pack` picker from the new list endpoint;
  - inline profile upload deferred;
  - 422 messages surfaced verbatim.

**Tests:**
- FE round-trip (edit → PUT body → refetch);
- a bounds violation shows the backend 422;
- backend profile-pack resolution from the data dir;
- the packaging test covers the data dir.

---

## P16 — DtC per-Load attribution (spec amendment first)

**Why now:** P6(b) created one VOLL slack per Load. `last_lost_load.lost_load_load_period_mwh` is keyed by Load id (`assumptions.py:842-850`). Decision 8 and §10's premise ("one slack per bus") no longer hold.

**Step 1 — spec amendment** (product sign-off, → Decision Q5):
- Amend decision 8 / §8 non-goal / §10: DtC may attribute per Load when the capture has `lost_load_load_period_mwh`.
- Bus-aggregate stays the fallback.
- `DtcConfig.attribution: Literal["bus_aggregate_not_per_load", "per_load"]`, default `"auto"`, which resolves to `per_load` when the capture supports it.

**Step 2 — stress:**
- Critical unserved = Σ over `critical_load_ids` (+ loads on critical buses) from the Load-keyed capture.
- Drop the "different buses" requirement for `per_load`.
- Keep the honesty notes accurate (`per_load_slack`).

**Step 3 — planning:**
- Retained-critical demand by **Load**: zero non-critical Loads' `p_set` even on shared buses.
- System ENS remains the planning metric.
- Report per-critical-Load unserved.

**Tests:**
- update `test_energy_hub_dtc.py`: the refusal test becomes "refuses per_load when the capture lacks Load keys";
- a shared-bus fixture (critical + comfort on one bus) now yields separate critical / non-critical MWh;
- a bus-aggregate regression for old captures.

---

## P17 — Energy import cap (spec §6)

**Constraint:** new `extra_functionality` wrapper `_wrap_with_import_energy_cap` in `solver/adequacy.py`, modelled on `_wrap_with_ens_cap` (`:225-427`):
- `Link-p` over the selected import links;
- per-period buckets;
- `≤ E × Σw_P / 8760` using weights **without** the `investment_period_weightings.years` multiplier. `snapshot_weights` includes it (`period_utils.py:80-108`), and nyears ≠ 1 is common.

**Config:** `SolverConfig.import_energy_cap_mwh_per_year` + `import_energy_links`, set only from the pack overlay. Never a user global — the same stance as DSR.

**Preflight:**
- refuse rolling / myopic (as the ENS cap does);
- warn when `p_min_pu < 0` (bidirectional Link: `p` is not pure import).

**Pack:** `apply_archetype_pack` stops warning "reserved" and instead sets the cfg fields for `weak_flexible` when `import_energy_mwh_per_year` is set.

**Lever:** `import_energy` kind in `levers.py`:
- default MWh ladder;
- `OptimizationLevers.import_energy`;
- a cfg-based branch in `apply_lever_scenario` (no network mutation);
- the "ineffective" check extended.

**Alternative:** formally re-scope spec §6 energy caps out of v1. → **Decision Q6.**

**Tests:**
- the binding cap raises cost / ENS monotonically;
- a multi-period network with years weighting has the per-year cap honoured;
- rolling is refused;
- the lever table has ≥2 differentiated options.

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

## Open decisions (need product owner)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Certification verdict rule | `pass` iff CI upper ≤ target; `fail` iff CI lower > target; else `inconclusive` (report the mean regardless) |
| Q2 | Non-annual horizons vs `target_lole_h` (h/yr) | Annualise with explicit `annualised_from_horizon_years` + note; refuse (`not_established`) when horizon < 1 week |
| Q3 | Default budget 30 vs full pipeline | Keep 30 (spec decision 17). Stage order is the priority, and later stages are skipped with a "budget" reason. Revisit after P12 measurements |
| Q4 | Frontier closing restore on a disposable copy | Add `restore_base: bool = True` kwarg; EH passes False on its private copy (saves 1 solve) |
| Q5 | DtC per-Load (amend decision 8 / §10) | Amend. P6(b) removed the premise |
| Q6 | Energy import cap | Implement (P17) after P13; otherwise re-scope in spec |

## Per-phase DoD (all phases)
- Red → green evidence.
- The phase's live HTTP test is unstubbed.
- `pixi run gui-tests` green, and the FE CI job (P10d) green.
- Plan checkbox + gate verdict recorded here.
- The handover/seal command list is updated when new test files are added.
