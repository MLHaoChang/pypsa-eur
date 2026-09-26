# Energy Hub — wire the skipped pipeline stages (`frontier`, `mc_certify`, `fmea_top`) + LCOH

> **For agentic workers:** Implement work-package by work-package, TDD per package. Prefer wiring the existing engines (`frontier.py`, `mc.py`, `copt.py` / `sweep.py`, `results/lcoh.py`) into `services/adequacy/eh_study.py` over any new parallel engine. The report is assembled ONLY by `assemble_reference_design_report` (spec decision 16).
>
> **Companion spec:** `docs/superpowers/specs/2026-09-14-eh-reference-design.md` (decisions 1–2, 3, 14, 16, 17, 18; §4 completeness enum).
> **Parent plan:** `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md` (P1.5 shipped the sync driver with these three stages `skipped`; P5 shipped the assembler).
> **Findings:** `docs/superpowers/findings/2026-09-26-eh-wire-skipped-stages.md` (written at the end, with before/after test counts and e2e evidence).

**Goal.** Close the gap verified on `master` `ec23302`: `run_eh_study` records `frontier`, `mc_certify` and `fmea_top` as `skipped` with the note "not implemented in P1.5 sync driver", so a `weak_flexible` / `off_grid` report (packs with `mc_certify_required=True`) never carries `mc_lole_h`, the frontier and FMEA sections are never populated, and `TeaBlock.lcoh_eur_per_kg` is never computed although `services/results/lcoh.py` exists. A design without a certified LOLE is not bankable.

**Already shipped (do not rebuild).**
- ε-constraint frontier engine `services/adequacy/frontier.py` (`run_frontier_sweep`, `knee_index`, exception-safe base restore)
- Sequential MC engine `services/adequacy/mc.py` (`snapshot_inputs`, `mc_adequacy` — solves nothing, never mutates the network)
- COPT screening `services/adequacy/copt.py` (`fleet_and_residual`, `screening_analysis` → class-A rows) and the Class-B Link sweep `services/adequacy/sweep.py` (`run_class_b_sweep`, frozen capacities, restore)
- LCOH per electrolyser Link `services/results/lcoh.py::compute_lcoh`
- The EH driver, assembler, HTTP runner, panel and chat tools (P1.5 → P9)

**Honest scope (v1 of this closure).**
- `mc_certify` runs on the **fixed plan** the report describes: the `MCInputs` snapshot is frozen under the lock at the end of `ens_solve`, so the frontier's re-solves (which come before `mc_certify` in decision-18 order) cannot leak into certification and a failed frontier restore cannot silently change the plan being certified.
- Certification verdict per decision 2: `certified` when `mc_lole_h ≤ target_lole_h`; `failed` otherwise **even when ENS is met**; `no_target` when the pack states no LOLE target (LOLE reported only). `not_established` with the exact reason when the MC cannot run (empty sampled fleet, budget/abort, exception).
- `frontier` is a small ladder **around the target** (×4, ×2, ×1, ×½, ×¼ of `ens_cap_permyriad`), budget-aware, and keeps `excludes_shed_cost: true` + `period_basis` on every cost field (decision 3).
- `fmea_top` = top-N (10) by criticality from the COPT class-A rows (zero solves) **plus** the Class-B Link sweep when Links carry occurrence data and the remaining budget affords it; SCLOPF Line/Transformer rows stay omitted and the section carries `FMEA_TOP_LINK_PRIMARY_NOTE` (decision 14).
- LCOH is a post-process wrap over `compute_lcoh` (decision 9) — `null` + `lcoh_status`/`lcoh_note` flag when the network has no electrolyser Links or the Links never consumed; never `0`.
- Budget: every LP solve the new stages spend is charged to `pipeline.solves_consumed`; a stage that cannot fit in `budget_solves − solves_consumed` is recorded `skipped` with a note naming the shortfall. MC and COPT are charged zero (they solve nothing — same rule as `campaign.estimate_solves`).

**Tech stack.** Unchanged.

---

## Phase QA gate + TDD protocol (mandatory)

Same loop as the parent plan: **red** (failing test encodes the acceptance) → **green** (minimum code) → **verify** (re-run the package's test files) → record the evidence under the package. Live-solve tests carry `@pytest.mark.live_solve`; QA driver is exercised through `tests/run_qa_drivers.py`.

---

## WP1 — `mc_certify` (plan on ENS, certify on MC LOLE)

**Files.** `services/adequacy/eh_study.py`, `models/energy_hub.py` (additive: `ArchetypePack.mc_draws` / `mc_seed` / `mc_cov_target`; `REPORT_SECTIONS += "certification"`), `services/adequacy/eh_report.py` (new `certification_section` helper; `mc_lole_h` already an assembler arg), `tests/test_energy_hub_certify.py`.

**Steps**
- [x] Freeze `MCInputs` under `lock` at the end of a successful `ens_solve` (`snapshot_inputs(network, cfg=cfg)`); an empty fleet or an `OutageRateError` is recorded as the reason, not raised.
- [x] `mc_certify` stage: `mc_adequacy(inputs, draws=pack.mc_draws, seed=pack.mc_seed, cov_target=pack.mc_cov_target, stop_event=stop_event)` (this is the study's own baseline, not an ELCC replay — the only kind of call site that may carry the flag). Zero solves charged.
- [x] `certification` section payload: `metric`, `target_lole_h`, `mc_lole_h`, `lole_ci`, `eue_mwh`, `eue_ci`, `n_samples`, `draws_requested`, `converged`, `resolution_floor_h`, `time_basis`, `horizon_years`, `ens_met`, `verdict`, `warning` (`MC_WARNING_V1`).
- [x] Headline `mc_lole_h` filled through `assemble_reference_design_report(mc_lole_h=…)`.
- [x] Not-run paths: not requested & not required → `skipped`; required but not requested / aborted / empty fleet / no ens_solve → `not_established` with the exact reason. The legacy `gates` fallback for "required by pack but not requested" is kept (pinned by `test_run_marks_not_established_when_required_mc_missing`); the "not implemented in P1.5" branch is removed.

**Acceptance**
- [x] `weak_flexible` default pipeline on a fixture with occurrence data → `completeness["certification"] == "ok"`, `mc_lole_h` finite, verdict ∈ {`certified`, `failed`}.
- [x] LOLE above target with ENS met → verdict `failed` (decision 2).
- [x] Fleet without occurrence data → `not_established`, note names "no electrical generator carries resolvable occurrence data".
- [x] `pipeline.solves_consumed` unchanged by the MC stage.

**TDD evidence:** `ImportError: eh_stages` / `TypeError: compute_tea() got an unexpected keyword argument 'network'` (red) → `eh_stages.py` + driver wiring → `test_energy_hub_certify.py` 12 green (incl. the off_grid decision-2 `failed` verdict on a met ENS target). One P1.5 fixture premise corrected: `gas` HAS a carrier-default outage rate, so the "no occurrence data" fixture uses a carrier without one.

---

## WP2 — `frontier` (ε-constraint around the target)

**Files.** `services/adequacy/eh_study.py`, `tests/test_energy_hub_frontier_stage.py`.

**Steps**
- [x] Ladder: `EH_FRONTIER_LADDER = (4, 2, 1, 0.5, 0.25)` × `ens_cap_permyriad`; `MIN_EH_FRONTIER_POINTS = 3`. Points = `min(len(ladder), remaining − 1)` (the `−1` is the closing restore); below the minimum → `skipped` with a note naming the shortfall.
- [x] `run_frontier_sweep(network, lock, cfg, targets, stop_event=stop_event, log_queue=log_queue, final_state_update=state_update)` — the closing restore goes through the real state sink so the foreground results describe the ENS plan again.
- [x] Section payload: `points` (each with `target_permyriad`, `status`, `point{cap_mwh, achieved_ens_mwh, achieved_shed_hours, total_system_cost_eur, engine, fidelity}`, `binding`, `period_basis`, `excludes_shed_cost: true`), `knee_index`, `voll_eur_per_mwh`, `warning` (non-convexity), `base_restored`, `base_restore_status`, `aborted`, `period_basis`, `excludes_shed_cost: true`.
- [x] Charge `len(points) + 1` solves. `ok` when ≥ `MIN_EH_FRONTIER_POINTS` points solved; `not_established` otherwise with the count.

**Acceptance**
- [x] Default pipeline with budget 30 → `completeness["frontier"] == "ok"`, ≥ 3 points, every point carries `excludes_shed_cost: true` and a `period_basis`.
- [x] Budget 2 → `frontier` `skipped` with "budget" in the note; `solves_consumed ≤ budget_solves`.
- [x] Explicit stages without `frontier` → `skipped`, "not requested".

**TDD evidence:** `test_default_stages_never_leave_unimplemented_pending` pinned `frontier == "skipped"` on the default pipeline (P1.5) — updated deliberately to `run` / `ok|not_established`; `test_energy_hub_frontier_stage.py` 11 green (ladder trimming, budget skip at 3 solves, 5 points with `excludes_shed_cost`/`period_basis`, monotone cost, `base_restored`).

---

## WP3 — `fmea_top` (ranked residual failure modes on the fixed plan)

**Files.** `services/adequacy/eh_study.py`, `tests/test_energy_hub_fmea_top.py`.

**Steps**
- [x] Class A: `fleet_and_residual(network, cfg=cfg)` + `screening_analysis(units, residual, weights=w, voll=voll)` under `lock`, on the frozen plan at the end of `ens_solve` (same instant as the MC snapshot). Zero solves.
- [x] Class B: `run_class_b_sweep(network, lock, cfg, stop_event=…, final_state_update=state_update)` only when `class_b_contingencies(network)` is non-empty **and** `len + 1 ≤ remaining`; otherwise the payload says why (`class_b: "skipped: …"`). Charged `len + 1`.
- [x] Merge, sort `(-criticality, mode_id)` (the worksheet's rule), take `FMEA_TOP_N = 10`. Payload: `top`, `n_total_modes`, `classes_included`, `class_b`, `voll_eur_per_mwh`, `base_restored`, `note = FMEA_TOP_LINK_PRIMARY_NOTE`.
- [x] `ok` when ≥ 1 mode; `not_established` when the COPT fleet is empty (reason from the engine).

**Acceptance**
- [x] Fixture with occurrence data → `fmea_top` `ok`, non-empty `top`, sorted by criticality, note contains "Link-primary" and "SCLOPF" (keeps `test_fmea_top_skip_note_is_link_primary_residual_risk` true for the skipped path).
- [x] Fixture with a Link that carries occurrence data and enough budget → a class-B row appears; with budget exhausted → class-A only + `class_b` skip reason.

**TDD evidence:** `test_energy_hub_fmea_top.py` 6 green — A+B ranking on the fixed plan (3 solves charged: base + Link + restore), A-only fallback at budget 2 (0 charged), A-only when no Link carries occurrence data, `not_established` on an empty fleet, Link-primary wording on the skipped path.

---

## WP4 — LCOH in `compute_tea`

**Files.** `services/adequacy/eh_report.py`, `models/energy_hub.py` (additive `TeaBlock.lcoh_status`, `TeaBlock.lcoh_note`), `services/adequacy/eh_study.py` (pass `network`, `cfg`), `tests/test_energy_hub_report_p5.py` (extend).

**Steps**
- [x] `compute_tea(*, cost_eur, served_energy_mwh, network=None, cfg=None)`: when `network` has electrolyser-like Links (the `compute_lcoh` token set), call `compute_lcoh(network, cfg, result_df=<live-network reader>)` and take `total.lcoh_eur_per_kg_h2`.
- [x] `lcoh_eur_per_kg = None` + `lcoh_status="skipped"` ("no electrolyser Links") when none; `not_established` when Links exist but produced no H₂ or the engine raised; `ok` when finite. Never `0`.
- [x] `notes` keeps the LCOE sentence and appends the LCOH sentence.

**Acceptance**
- [x] Existing P5 TEA tests unchanged and green.
- [x] Sector-coupled fixture with an electrolyser Link that consumes → finite `lcoh_eur_per_kg`, `lcoh_status == "ok"`.
- [x] Electrical-only fixture → `lcoh_eur_per_kg is None`, `lcoh_status == "skipped"`.

**TDD evidence:** `test_energy_hub_lcoh.py` 6 green (no-network path byte-identical to P5; `skipped` flag; `not_established` on unsolved electrolyser Links; finite LCOH ≈ €0.50/kg on the fixture with a consuming electrolyser). P5 TEA tests untouched.

---

## WP5 — Frontend panel + chat copy

**Files.** `frontend/src/pages/results/EhReferenceDesignPanel.tsx`, `EhReferenceDesignPanel.test.tsx`, `frontend/src/api/simulation.ts` (types), `backend/services/chat_tools_schema.py` (`run_eh_study` description), `pypsa-gui/CHATBOT.md` (tool table rows).

**Steps**
- [x] `COMPLETENESS_ORDER` gains `certification` after `target`.
- [x] Headline chips: `MC LOLE` (h, with CI) + verdict tone; `LCOH €/kg` or its flag when `null`.
- [x] Frontier block: table (target ‱ / status / cost / ENS MWh / shed h), knee marker, non-convexity warning, `excl. shed` label, CSV.
- [x] FMEA top block: table (rank / class / component / name / criticality €/yr / ΔEUE), the Link-primary note, class-B status, CSV.
- [x] `get_adequacy_results('eh_reference_design')` payload shape unchanged (`EXPORT_KEYS`); tool + CHATBOT copy say the pipeline now includes frontier / MC certify / FMEA top-N.

**Acceptance**
- [x] Vitest: certification chip + verdict, frontier table + CSV, FMEA table + note, LCOH chip and LCOH flag render from a fixture report; skipped sections do not render blocks.

**TDD evidence:** `EhReferenceDesignPanel.test.tsx` 23 → 32 green (verdict tones, knee marker, infeasible point renders `—` not 0, LCOH flag never `0.00`, pre-wiring report renders none of the new blocks).

---

## WP6 — QA driver

**Files.** `tests/qa_eh_reference_design.py` (auto-discovered by `tests/run_qa_drivers.py`).

**Steps**
- [x] `import tests.qa_support` first; install a `weak_flexible` fixture (import Link with occurrence data, critical bus, occurrence-bearing generators, extendable peaker); set solver config (VOLL, ENS cap); `POST /api/results/eh_study {archetype: weak_flexible}`; poll `GET /api/results/eh_study`; read `GET /api/results/eh_reference_design`.
- [x] Assert: `mc_lole_h` finite; `certification` `ok` with a verdict; frontier ≥ 3 points, each `excludes_shed_cost: true` + `period_basis`; `fmea_top.top` non-empty with the Link-primary note; `tea.lcoh_eur_per_kg is None` + `lcoh_status == "skipped"`; `solves_consumed ≤ budget_solves`; JSON-clean (no NaN/inf).
- [x] Second run on a sector-coupled fixture with an electrolyser Link → `tea.lcoh_eur_per_kg` finite, `lcoh_status == "ok"`.

**Acceptance**
- [x] `python tests/qa_eh_reference_design.py` exits 0; `python tests/run_qa_drivers.py` still green.

**Evidence:** 39/39 steps over HTTP — weak_flexible default pipeline: `solves_consumed=14/30`, `mc_lole_h=82.1 h` vs target 3 h → verdict **failed** with ENS met (decision 2 observed live), frontier 5/5 points around 10‱ with `base_restored`, fmea_top A+B (Link `import_poc` ranked first), LCOH `null`+`skipped`; strong_grid on the electrolyser hub: LCOH €0.50/kg, `lcoh_status=ok`.

---

## Definition of done

- `weak_flexible` / `off_grid` reports carry `mc_lole_h` and a `certification` verdict; `frontier` and `fmea_top` populated; LCOH computed where electrolysers exist and flagged where not.
- Stage order unchanged (decision 18); one report builder (decision 16); every unresolvable field `null` + completeness flag (ADR-0001); no silent budget overrun.
- Backend `pytest -m "not slow"` green, QA drivers green, frontend vitest green; findings note with before/after counts + e2e evidence pushed to `claude/eh-wire-skipped-stages`.
