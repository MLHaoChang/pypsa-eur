# Energy Hub — close the zonal-MC open items: grid storage, several grid areas, two-area COPT screening, common-mode import outages

> **For agentic workers:** Work package by work package, TDD per package (red → green → verify), then a **review pass** of the package's diff whose findings are recorded under the package and fixed before the next package starts. After WP4, an end-to-end QA + integration run. Extend `mc_zonal.py` / `eh_stages.py`; the single-area engine (`mc.py`) stays untouched apart from the `blocks_fn` hook it already has.
>
> **Parent plan:** `docs/superpowers/plans/2026-09-27-eh-zonal-mc-import-outages.md`
> **Parent findings ("Still open"):** `docs/superpowers/findings/2026-09-27-eh-zonal-mc-import-outages.md`
> **Findings:** `docs/superpowers/findings/2026-09-28-eh-zonal-mc-open-items.md` (written at the end: per-WP review findings, before/after counts, e2e evidence)

**Goal.** Close the four items the 2026-09-27 findings left open:

1. Grid-side storage is not dispatched in the zonal area.
2. A hub with PoC Links into several disconnected grid components stays on v1.
3. The COPT screening (fmea_top class A) is single-area: it sees the sampled Link but not the grid surplus.
4. Link and grid outages are independent; a common-mode event that takes the grid and the PoC down together is not modelled.

**Invariants (every WP).**
- Bit-identity where nothing new applies: no grid storage, one area, no common-mode data → `simulate_zonal_blocks` returns exactly what it returns on the 2026-09-27 head; an unbound grid still reproduces v1 exactly.
- CRN: every new random process draws from its own tagged substream; no existing unit's draws move.
- ADR-0001: unresolvable → `null` + note, never 0. Spec decision 16 (one report builder), decision 18 (stage order), MC/COPT charge zero solves.
- Opt-in data only: nothing here invents a rate. A common-mode rate the user did not give is not modelled, and the payload says so.

---

## WP1 — grid-side storage dispatch (pinned non-anticipative policy)

**Policy (pinned, not an optimum — the same stance as `mc._dispatch`).** Per hour, per draw:
1. *Grid first.* Grid storage **discharges** against the grid's own deficit only.
2. The grid surplus (after its own load and must-take) is offered through the Link: `offered = min(link_avail, surplus × delivery)`.
3. *Local first.* The hub's own storage dispatches against what is left.
4. *Remote support.* If the hub is still short, grid storage discharges to the hub, bounded by the Link headroom `link_avail − offered`.
5. Grid storage **charges** only from the grid surplus left after the full offered import is reserved for the hub (conservative for the hub).
Grid stores are derated by their resolved outage rate exactly as hub stores are (`block_store_arrays`), and start each period at the same `initial_soc_frac`.

**Files.** `services/adequacy/mc_zonal.py`, `services/adequacy/eh_stages.py` (`grid_area.storage_dispatched`), `tests/test_energy_hub_zonal_storage.py` (new).

**Acceptance**
- [x] Grid without storage → per-draw arrays identical to the 2026-09-27 zonal engine (a frozen copy of that kernel in the test is the oracle).
- [x] A grid battery that bridges the grid's own shortfall raises the hub's import and lowers hub LOLE vs the same grid with storage disabled.
- [x] Remote support is bounded by the Link headroom (a 1000 MW grid battery behind a 50 MW Link never delivers more than 50 MW).
- [x] Grid storage never charges from power offered to the hub.
- [x] `grid_area.storage_dispatched` is `true` with the store names when the grid has storage.

## WP2 — several grid areas

**Design.** `ZonalInputs.areas: tuple[GridArea]`, one per grid-side connected component that at least one live import Link reaches. Each area: its own `MCInputs` (or `None` when the component has no sampled unit — its Links then see an unbounded surplus, i.e. v1 for those Links), the positions of its Link units, its firm-block series and delivery ratio, and its own tagged substream (`GRID_STREAM_KEY − k`). The hub import is `Σ_k min(offered_k, …)` with the WP1 policy applied per area. Zonal applies when at least one area is sampled.

**Files.** `services/adequacy/mc_zonal.py`, `services/adequacy/eh_stages.py` (`hub_fleet_scope` groups Links by grid component; `grid_areas` payload replaces `grid_area`), frontend types, `tests/test_energy_hub_zonal_areas.py` (new).

**Acceptance**
- [x] One area → identical to WP1 (area 0 keeps `GRID_STREAM_KEY`).
- [x] Two Links into two separate grids, both sampled → `import_model == "zonal"`, `len(grid_areas) == 2`; LOLE ≥ v1 on the same seed.
- [x] One sampled grid + one grid without data → zonal, the unsampled area reported with `sampled: false` and its reason.
- [x] A component reached only by islanded Links gets no area.

## WP3 — two-area COPT screening

**Design.** The COPT cannot hold a surplus-limited import as a two-state unit, but it can hold the Link unit with a per-hour availability **profile**: for Link *k* in area *a*, `profile_h = E[min(cap_a,h, S_a,h)] / cap_a,h × (cap_k,h / cap_a,h share)`, where `S_a,h = max(C_a − r_a,h, 0) × delivery_a,h` and `C_a` is the grid area's COPT capacity distribution (`build_copt`). The Link outage stays exact (two-state); the grid randomness is netted at its expected value — the same class of approximation the COPT already applies to units beyond `K_EXACT`, disclosed. Grid storage is not in the COPT (the COPT never holds storage). The profiled copies are used for the **screening only**; the MC keeps the unprofiled units (otherwise the grid limit would be counted twice).

**Files.** `services/adequacy/eh_stages.py`, `services/adequacy/mc_zonal.py` (`expected_surplus_fraction`), `tests/test_energy_hub_zonal_copt.py` (new).

**Acceptance**
- [x] Unbound grid → profile ≡ 1, `copt_metrics` identical to the v1 screening.
- [x] A grid whose load eats its supply → COPT LOLE (and the Link's class-A ΔEUE) rises over v1.
- [x] `fleet_scope.copt_import_model == "expected_surplus_profile"` with a note; `"two_state"` in v1.
- [x] Unit test of `expected_surplus_fraction` against a hand-computed PMF.

## WP4 — common-mode import outage (opt-in data on the Link)

**Design.** Occurrence data lives on the component, so the common-mode event does too: Link attributes `common_mode_rate`, `common_mode_mttr_hours` (and optional `common_mode_basis`, default `FOR`) on an identified import Link mean "an event that takes this Link AND the grid area behind it down together". Nothing is defaulted: without both values the event is not modelled and the payload says so (a rate without a finite positive MTTR is reported, not guessed; a rate outside `[0, 1)` refuses the snapshot like any rate). When given, the MC draws one two-state chain per such Link from its own substream (`CM_STREAM_KEY − j`); while it is DOWN, the whole area the Link feeds from contributes zero import. Whenever any common-mode chain exists the two-area engine runs (areas without grid data are unbounded, so a v1 hub gains the event too). The COPT folds it exactly into the Link units' rate, `q_eff = 1 − (1 − q_link)(1 − q_cm)` (per hour, two independent two-state events are one), and a firm-block Link with a common-mode rate becomes a unit with `q_cm`. The pack and `pack_hash` are untouched.

**Files.** `services/adequacy/mc_zonal.py`, `services/adequacy/eh_stages.py`, `tests/test_energy_hub_common_mode.py` (new).

**Acceptance**
- [x] No common-mode data → identical to WP3.
- [x] `common_mode_rate > 0` raises LOLE on the same seed; payload `import_common_mode: [{link, rate, mttr_hours, basis, area}]`.
- [x] Rate without MTTR → not modelled, reason in the note; rate outside `[0, 1)` → snapshot refused with the Link named.
- [x] Islanded Link with common-mode data → not applied ("islanded"), LOLE unchanged.
- [x] ~~COPT: `q_eff` on the Link unit; a firm Link with common-mode data becomes a sampled unit at `q_cm`.~~ **Superseded by the WP4 review (R1):** a shared event is not an independent per-unit rate (q_cm² instead of q_cm). The screening now mixes the event states exactly and ranks each event as its own class-A mode. Link units keep their own q; firm blocks stay firm in the "event up" state.

## WP5 — report, panel, chat copy

`EhReferenceDesignPanel.tsx` shows grid areas (count, sampled, storage), the COPT import model and the common-mode event; `chat_tools_schema.py` / `CHATBOT.md` name them. Vitest per new field; pre-change payloads render as before.

## E2E QA + integration (after WP5)

- `tests/qa_eh_reference_design.py`: over HTTP, a weak_flexible hub with a grid battery, a two-grid hub, and a PoC Link carrying common-mode data; each asserts the disclosure and the LOLE ordering against its control.
- Full backend `pytest -m "not slow"`, `run_qa_drivers.py`, frontend `vitest` + `tsc`, `ruff` on changed files.

## Definition of done

All four open items closed or re-scoped with a reason; every WP's review findings recorded and resolved; suites green; findings note with before/after counts and e2e evidence; pushed to `claude/eh-zonal-mc-import-outages`.
