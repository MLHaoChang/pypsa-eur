# Energy Hub — sample the import: Link outages (v1) and grid-side surplus (zonal v2) in `mc_certify`

> **For agentic workers:** Implement work-package by work-package, TDD per package. Extend the existing engines (`mc.py`, `copt.py`, `eh_stages.py`) rather than building a parallel certifier. The report is assembled ONLY by `assemble_reference_design_report` (spec decision 16).
>
> **Companion spec:** `docs/superpowers/specs/2026-09-14-eh-reference-design.md` (decisions 1–2, 14, 16, 18; §6 import overlays — `import_firmness = planning_limit_only` "unless Link outages are modelled in the same study").
> **Parent plan:** `docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md` (WP1 `mc_certify`, WP3 `fmea_top`; follow-up fix "the MC / COPT fleet is the hub's").
> **Findings:** `docs/superpowers/findings/2026-09-27-eh-zonal-mc-import-outages.md` (written at the end, with before/after counts and e2e evidence).

**Goal.** Close the "still open" item of the 2026-09-26 findings: after `hub_fleet_scope` splits the network at the import Link(s), `freeze_fixed_plan` subtracts the import from the hub residual as a **deterministic firm block** at the Link's planning cap. That is optimistic twice over: the Link itself fails (it usually carries `outage_rate_value` / `mttr_hours`, which the Class-B sweep already reads through `resolve_outage_params(n, "links")`), and the grid behind it may have no surplus in the hours the hub needs it. A `weak_flexible` certification must reflect both.

**Already shipped (do not rebuild).**
- `eh_stages.hub_fleet_scope` / `_hub_side_copy` / `freeze_fixed_plan` (hub-side fleet, import firm block, `fleet_scope` disclosure, carrier-only / non-separating fallbacks)
- `mc.sample_capacity` with per-unit `capacity_series` (UP = the series that hour) and the positional CRN stream contract
- `copt.screening_analysis` (class A) and `sweep.run_class_b_sweep` (class B, one row per occurrence-bearing Link)

**Honest scope.**
- **v1 — the import Link as a sampled unit.** When an identified import Link has resolvable occurrence data (asset value or carrier default) and a non-zero planning cap, it joins the hub-side fleet as a two-state `CoptUnit` whose UP capacity is the cap per snapshot (`capacity_series`, `None` when constant), with q / MTTR / basis from `resolve_outage_params(n, "links")`. It is appended AFTER the hub's generators, so every generator keeps its positional substream. The same unit list feeds the COPT screening (membership invariant). Links without occurrence data stay a firm block; a Link whose cap is zero every hour is `islanded`. An unusable rate (outside `[0, 1)`) refuses the snapshot like a generator's does.
- **v2 — grid-side surplus (zonal).** When the hub is split off at a single grid component and that grid side has a non-empty sampled fleet, the grid side becomes a second area: its fleet and demand are snapshotted from a pruned grid-side copy, and each hour the hub receives `min(Σ link_up × cap, max(grid_available − grid_residual, 0))`. Grid-side storage is not dispatched (conservative, disclosed). No grid-side occurrence data, several grid components, or a grid snapshot that refuses → v1 with the reason in the note.
- **Where the two-area path lives.** A separate module `services/adequacy/mc_zonal.py` owns the two-area block simulation; `mc.mc_adequacy` gains ONE keyword (`blocks_fn`, default `None` → `_simulate_blocks`) so the batching / convergence / aggregation code is shared rather than copied. `sample_capacity`, `_simulate_blocks`, `simulate` and every existing call site (MC endpoint, ELCC, coupling / margin loops) are untouched; the zonal path samples the hub + Link fleet with the SAME seed and positions as v1, so a grid with surplus ≥ the Link cap every hour reproduces the v1 draws exactly (pinned by a test).
- **FMEA: the Link is ranked once.** The sampled Link is part of the COPT fleet (it shapes every class-A row and the COPT LOLE), but it is ranked in `fmea_top` by its **Class-B** row (LP re-solve on the full network). Its class-A row is withheld when the Class-B row exists; when the Class-B sweep did not run (budget, VOLL, abort) the class-A row is kept, relabelled `component_class: "Link"`, so the Link is still ranked exactly once. The payload says which view ranked it.
- **Disclosure.** `fleet_scope` gains `import_model` (`sampled_unit` | `firm_block` | `islanded` | `zonal` | `mixed`), `import_firmness` (`outage_sampled` | `outage_and_grid_sampled` | `planning_limit_only`), `import_cap_mw_max`, per-Link `import_link_models`, and `grid_area` (zonal only); `import_firm_mw_max` is the firm-block part only (`null` when no Link is a firm block — never 0 for "not applicable", ADR-0001). `certification` repeats `import_model` / `import_firmness` at top level.
- **Budget.** MC and COPT still charge zero solves; stage order (decision 18) and the scope fallbacks are unchanged.

---

## WP1 — import Link as a sampled unit (v1)

**Files.** `services/adequacy/eh_stages.py`, `tests/test_energy_hub_import_outages.py` (new), `tests/test_energy_hub_certify_scope.py` (one assertion updated deliberately).

**Steps**
- [x] `hub_fleet_scope` resolves each identified Link's occurrence data and cap series → `ImportLinkModel` rows (`sampled_unit` / `firm_block` / `islanded`); `import_firm_mw` keeps only the firm-block part; `import_units` carries the `CoptUnit`s.
- [x] `freeze_fixed_plan` appends `import_units` to the hub-side `MCInputs.units` and subtracts only the firm part from the residual; the COPT screening reads the same units.
- [x] `run_fmea_top_stage` withholds the class-A row of a sampled import Link when Class B ranked it, else relabels it as a Link row; payload `import_link_ranking`.

**Acceptance**
- [x] weak_flexible fixture: `import_model` sampled (v1 or v2), `import_firmness != planning_limit_only`; MC LOLE (v1) > firm-block LOLE on the same seed.
- [x] Link without occurrence data → `firm_block`, `planning_limit_only`, LOLE identical to the pre-change result.
- [x] off_grid islanded hub → `islanded`, LOLE unchanged vs firm block.
- [x] fmea_top ranks the Link exactly once (B when the sweep ran, relabelled A otherwise).

**TDD evidence:** `ImportError: firm_vs_sampled_import_pair` / `KeyError: 'import_model'` (red) → `ImportLinkModel` + `freeze_fixed_plan(import_model=…)` → `test_energy_hub_import_outages.py` 12 green. On the same seed, sampled 516.8 h > firm 415.0 h on the weak fixture; islanded LOLE is identical either way. One premise was corrected: on the weak fixture a Link without data goes `zonal` (grid-sampled) under `"auto"`, so the firm-block test pins `import_model="sampled_unit"`. `test_energy_hub_certify_scope.py` weak_flexible assertion updated deliberately (`import_firm_mw_max` 50 → `import_cap_mw_max` 50, firm `null`).

## WP2 — grid-side availability (zonal v2)

**Files.** `services/adequacy/mc_zonal.py` (new), `services/adequacy/mc.py` (`blocks_fn` hook only), `services/adequacy/eh_stages.py`, `tests/test_energy_hub_zonal_mc.py` (new).

**Steps**
- [x] `ZonalInputs(hub: MCInputs, grid: MCInputs, import_idx, firm_import_mw)`; `simulate_zonal_blocks` → the `_simulate_blocks` dict shape; `zonal_mc_adequacy` = `mc_adequacy(hub, blocks_fn=…)`.
- [x] `freeze_fixed_plan` builds the grid-side snapshot when eligible; falls back to v1 with the reason otherwise.
- [x] `run_mc_certify_stage` runs the zonal engine when `frozen.zonal_inputs` is set.

**Acceptance**
- [x] Grid with surplus ≥ cap every hour → zonal per-draw LOLE/EUE bit-identical to v1.
- [x] Grid whose own load eats its supply → zonal LOLE > v1.
- [x] Grid with no occurrence data → v1 with a note naming the fallback.
- [x] `mc_adequacy` without `blocks_fn` bit-identical to before (existing MC / ELCC suites green).

**TDD evidence:** `ModuleNotFoundError: services.adequacy.mc_zonal` (red) → `mc_zonal.py` + the `blocks_fn` hook → `test_energy_hub_zonal_mc.py` 10 green. The pins: bit-identity to v1 with an unbound grid; a grid whose own load eats its supply raises LOLE; the fallback note when the grid side has no data; and certification through the grid area when the hub side has no sampled unit. EH + sweep + MC + ELCC suites: 308 passed.

## WP3 — report + panel + chat copy

**Files.** `frontend/src/pages/results/EhReferenceDesignPanel.tsx`, `EhReferenceDesignPanel.test.tsx`, `frontend/src/api/simulation.ts` (types), `services/chat_tools_schema.py`, `pypsa-gui/CHATBOT.md`.

- [x] Certification block shows the import model (sampled Link / zonal grid surplus / firm planning limit / islanded) with the fleet-scope note; FMEA block says how the Link was ranked.
- [x] Vitest: each import model renders its label; a pre-change report (no `import_model`) renders the planning-limit wording.

**TDD evidence:** `EhReferenceDesignPanel.test.tsx` 32 → 35 green; full vitest 1952 passed; `tsc --noEmit` clean.

## WP4 — QA

**Files.** `tests/eh_stage_fixtures.py`, `tests/qa_eh_reference_design.py`.

- [x] weak_flexible e2e asserts the import is sampled (`import_model ∈ {sampled_unit, zonal}`) and records the LOLE.
- [x] New fixture pair: a reliable Link with q > 0 raises LOLE vs the same hub with the Link's occurrence data cleared (firm block); an islanded off_grid hub gives the same LOLE either way.

**Evidence:** `qa_eh_reference_design.py` 64/64 over HTTP. weak_flexible `import_model=zonal`, MC LOLE 415.005 h (master) → 585.28 h. Pair: sampled 499.32 h > firm 415.005 h; off_grid 1400.48 h = 1400.48 h.

## Definition of done

- Backend `pytest -m "not slow"` green, QA drivers green, frontend vitest green, ruff clean on changed files.
- Findings note with before/after counts and the weak_flexible MC LOLE before/after, pushed to `claude/eh-zonal-mc-import-outages`.
