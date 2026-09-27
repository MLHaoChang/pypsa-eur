# EH certification — the import is sampled: Link outages (v1) and grid-side surplus (zonal v2)

**Date:** 2026-09-27  
**Branch:** `claude/eh-zonal-mc-import-outages` (from `master` `9bdc0e3`, which contains PR #53)  
**Plan:** [`2026-09-27-eh-zonal-mc-import-outages.md`](../plans/2026-09-27-eh-zonal-mc-import-outages.md)  
**Spec:** [`2026-09-14-eh-reference-design.md`](../specs/2026-09-14-eh-reference-design.md) decisions 1–2, 14, 16, 18; §6 import overlays  
**Closes:** "Still open — the import is firm up to its cap" in [`2026-09-26-eh-wire-skipped-stages.md`](2026-09-26-eh-wire-skipped-stages.md)

## The gap, as verified on `master`

After #53 the hub is certified on its own side of the import Link. The import itself, though, was subtracted from the hub residual as a **deterministic firm block** at the Link's planning cap (`import_firmness: planning_limit_only`). That is optimistic in two ways:

1. The Link fails. The fixture's PoC Link carries `outage_rate_value = 0.03`, `mttr_hours = 48`, and the Class-B sweep in the same study already reads them through `resolve_outage_params(n, "links")`. The certification ignored them.
2. The grid behind the Link may have no surplus in the hours the hub needs it. Grid-side outages and grid-side load never reached the hub's MC.

## What changed

| Area | Change |
|---|---|
| `services/adequacy/eh_stages.py` | `hub_fleet_scope` resolves each identified import Link into an `ImportLinkModel`: **`sampled_unit`** (occurrence data + finite MTTR → a two-state `CoptUnit` whose UP capacity is the cap at the hub per snapshot, `capacity_series` only when the cap varies), **`firm_block`** (no data, or no finite MTTR, with the reason), or **`islanded`** (cap 0 every hour). A rate outside `[0, 1)` raises `OutageRateError`, the same as for a generator. `freeze_fixed_plan(…, import_model="auto" \| "sampled_unit" \| "firm_block")` appends the sampled Link units **after** the hub's generators and nets out only the firm-block part. The COPT screens that same unit list, so the membership invariant holds. `"auto"` adds the zonal grid area when it can. `run_mc_certify_stage` runs the two-area engine when it exists. `run_fmea_top_stage` ranks a sampled Link **once** (`_rank_import_links_once`). |
| `services/adequacy/mc_zonal.py` (new) | `ZonalInputs`, `simulate_zonal_blocks`, `zonal_mc_adequacy`: a two-area block simulation in which the hub receives `min(Σ link_up × cap + firm Links, max(grid_avail − grid_residual, 0) × delivery)` each hour. |
| `services/adequacy/mc.py` | One keyword on `mc_adequacy`: `blocks_fn=None` (default → `_simulate_blocks`). Nothing else changed: `sample_capacity`, `_simulate_blocks`, `simulate` and every existing call site (MC endpoint, ELCC, coupling / margin loops) are untouched. |
| Payload (`fleet_scope`, on `certification` and `fmea_top`) | `import_model` ∈ `sampled_unit` \| `firm_block` \| `islanded` \| `mixed` \| `zonal`; `import_firmness` ∈ `outage_sampled` \| `partially_outage_sampled` \| `grid_sampled` \| `outage_and_grid_sampled` \| `planning_limit_only`; `import_cap_mw_max`; `import_link_models[]` (per Link: model, cap, q, MTTR, basis, source, reason); `import_units`; `grid_area` (zonal only: units, capacity, demand peak, `storage_dispatched: false`). `import_firm_mw_max` now covers the firm-block part only and is `null` when no Link is a firm block (ADR-0001, never 0 for "not applicable"). `whole_network` scope carries `import_model: null` / `import_firmness: null` rather than a firmness it never applied. `certification` repeats `import_model` / `import_firmness` at top level, and `engine: mc_zonal`, `fidelity: sequential_mc_two_area` when the two-area path ran. `fmea_top` gains `import_link_ranking` (`{link: class_b \| class_a}`) + `import_link_ranking_note`. |
| Frontend | `EhReferenceDesignPanel.tsx`: an "import" line in the certification block (`data-import-model`), the fleet-scope note, and the FMEA import-ranking line. `importModelLabel` reads a pre-change report (hub-side scope, no `import_model`) as the firm block it was. Types in `api/simulation.ts` are additive. |
| Chat / docs | `run_eh_study` description and the `CHATBOT.md` tool row say the certification samples the Link and the grid behind it when data allows, and name `certification.import_model`. |
| QA | `tests/qa_eh_reference_design.py` gains the import assertions on the weak_flexible e2e, plus section 3 (the firm-vs-sampled fixture pair and the islanded off_grid control). The fixture pair lives in `tests/eh_stage_fixtures.py::firm_vs_sampled_import_pair`. |

### Design points worth keeping

- **Zonal is a separate module and shares the batching.** `mc_adequacy`'s batching, convergence, CI and `by_period` code is reused through `blocks_fn`, so the two-area certification cannot drift from the single-area one on any statistic. The single-area kernel is not edited at all.
- **The CRN contract survives, and a test pins it.** The zonal path samples the hub fleet (Links excluded, their paths generated and discarded) and the Links (every other unit replaced by a zero-rate placeholder, which consumes no stream) with the same seed and positions as v1. It forms the float32 sum in the same order. With a grid that never binds (q = 0, 1000 MW behind 50 MW), the per-draw LOLE / EUE arrays and the whole payload are **bit-identical to v1** (`test_grid_with_surplus_above_the_cap_every_hour_reproduces_v1_exactly`). The grid fleet samples from its own tagged substream (`spawn_key + (2³²−1,)`), so adding the grid moves no hub draw. `sample_capacity` spawns from the SeedSequence it is given, so each call gets a fresh copy of it.
- **The grid serves itself first.** The hub gets only `grid_available − grid_residual`, where the residual is net of the grid's must-take. Grid-side storage is not dispatched, which is conservative for the hub, and the payload says so. A grid side made of several disconnected components, a refused grid snapshot, or a grid with no sampled unit all fall back to v1 and give the reason in the note ("grid side has no occurrence data … v1 applies").
- **The Link is ranked once in FMEA.** The sampled Link is part of the COPT fleet: it shapes every class-A row's criticality and the COPT LOLE, just as it shapes the MC. Its own class-A row is **withheld** when the Class-B sweep ranked it (the LP re-solve on the full network is the Link-primary view, decision 14). When the sweep did not run (budget, VOLL, abort), the class-A row is **kept and relabelled** (`component_class: "Link"`, `link:<name>:forced_outage`), so the Link is never unranked and never ranked twice.
- **Nothing is charged.** The MC and the COPT still charge zero solves. Stage order (decision 18), the carrier-only / non-separating fallbacks and the single report builder (decision 16) are unchanged.

### Test premise updated deliberately

`test_energy_hub_certify_scope.py::test_weak_flexible_counts_import_as_firm_up_to_its_planning_cap_only` pinned `import_firm_mw_max == 50` on the weak_flexible fixture. That number was the firm block, which is the gap itself. It now asserts `import_cap_mw_max == 50`, `import_firm_mw_max is None` and `import_model == "zonal"`. The off_grid assertion gains `import_model == "islanded"`. The rest of that test is unchanged.

## Local verification

Environment: the SessionStart venv (`~/.venv-pypsa-gui`, Python 3.12, pandas 2.3.3, pixi-lock pins).

| Suite | Before (`master` `9bdc0e3`) | After |
|---|---|---|
| `test_energy_hub_*.py` + `test_adequacy_sweep.py` | **221** | **243** (+22: `test_energy_hub_import_outages.py` 12, `test_energy_hub_zonal_mc.py` 10; 0 failed) |
| EH + sweep + `test_adequacy_mc*.py` + `test_*elcc*.py` | — | 308 passed (single-area MC / ELCC suites unchanged by the `blocks_fn` hook) |
| full backend `-m "not slow"` | 5770 passed, 31 skipped | **5792 passed, 31 skipped, 0 failed** (+22) |
| `run_qa_drivers.py` | 22 drivers | **22 drivers passed** |
| `qa_eh_reference_design.py` | 39/39 steps | **64/64** steps |
| frontend `npx vitest run` | 1949 passed | **1952** passed (177 files; `EhReferenceDesignPanel.test.tsx` 32 → 35); `tsc --noEmit` clean |
| `ruff check` (changed files) | — | clean. `mc.py` carries 19 pre-existing docstring-style hits, the same count as on master; none are new |

## E2E evidence

### weak_flexible default pipeline (`certifiable_weak_network`, over HTTP)

The PoC Link is capped at the pack's 50 MW, q = 0.03, MTTR 48 h. Behind it sits a 200 MW `grid_supply` unit (q = 0.02). Local units: `base` 60 MW and `peaker` 40 MW. Target LOLE 3 h; pack MC 200 draws, seed 0, CoV target 0.05.

| Import model | MC LOLE (h) | 95 % CI | EUE (MWh) |
|---|---|---|---|
| **before** — firm block at 50 MW (`master`) | **415.005** | [359.2, 470.8] | 7 479 |
| v1 — Link sampled (`import_model="sampled_unit"`) | 516.84 | [455.2, 578.5] | 9 724 |
| **after** — zonal, Link + grid sampled (default) | **585.28** | [519.3, 651.3] | 11 744 |

The HTTP report gives `import_model: zonal`, `import_firmness: outage_and_grid_sampled`, `import_link_models: [{import_poc, sampled_unit, cap 50, q 0.03, MTTR 48, asset}]`, `grid_area.units: [grid_supply]` and `import_firm_mw_max: null`. The verdict stays **failed**, and the LOLE it fails by is now 41 % higher than the firm block claimed. `fmea_top` ranks the Link once, by its Class-B row: `import_link_ranking: {import_poc: class_b}`. The top-N is `B import_poc`, `A base`, `A peaker`.

### Firm-vs-sampled pair and the islanded control (QA section 3)

`firm_vs_sampled_import_pair()` builds two identical hubs behind a reliable PoC Link (q = 0.02, MTTR 24 h). One Link carries that occurrence data and the other carries none. The grid supply has no occurrence data, so neither hub goes zonal and the pair isolates the Link.

| Pack | Link with data | Link without data |
|---|---|---|
| weak_flexible | `sampled_unit` / `outage_sampled`, **499.32 h** | `firm_block` / `planning_limit_only`, **415.005 h** |
| off_grid (`peaker_mw=65`, self-sufficient) | `islanded`, **1400.48 h** | `islanded`, **1400.48 h** (identical) |

The same seed gives the same generator draws in both hubs, because the Link is appended after the generators. A reliable Link with q > 0 therefore raises LOLE, and islanding makes the two hubs agree to the last digit.

## Still open / deliberately not done

- **Grid-side storage is not dispatched** in the zonal area, which is conservative. Dispatching it needs a joint hub/grid storage policy, and that is a design question, not a mechanical one.
- **One grid area.** A hub with PoC Links into several disconnected grid components stays on v1 and says so.
- **The COPT screening is single-area.** Class A sees the sampled Link (v1 membership) but not the grid-side surplus, because a surplus-limited import is not a unit a convolution can hold. The MC is the certifying number. The fleet-scope note and the `import_model` field tell the two apart.
- **Independent outages.** Link and grid outages are drawn independently of hub outages, the same as every other unit (`MC_WARNING_V1`). A common-mode event that takes down the grid and the PoC together is not modelled.
