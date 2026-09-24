# Phase 6(b) — Multi-slack / shared-bus attribution spike

**Date:** 2026-09-24  
**Plan:** `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md` Phase 6  
**Depends on:** P6(a) merged (#49) — dedicated-bus honesty shipped; shared-bus still fail-closed.  
**Rule:** Do not invent per-Load ENS from bus-level capture. Any shared-bus attribution requires a slack redesign.

## Why P6(a) is not enough

Industrial + residential (or AC + H₂) Loads on the **same** bus share one involuntary VOLL Generator (`__voll_<bus>`). Capture is snapshot × **bus**. `multi_energy` correctly returns `not_established` for that geometry. Product still wants honest unmet-energy by Load identity / carrier when models are shared-bus.

P4b DtC already pinned `attribution=bus_aggregate_not_per_load` for the same reason.

## Current geometry (code)

| Layer | Behaviour |
|---|---|
| Slack creation | `solver/assumptions.py` — **one** Generator per load-bearing bus: `f"{VOLL_SLACK_PREFIX}{bus}"`, carrier `load_shedding`, `p_nom ≈ 10× max load` |
| Capture | `lost_load_t` / `lost_load_bus_period_mwh` — columns = **buses** |
| Electrical scope | `electrical_columns` / ENS-cap / FMEA ΔEUE filter AC buses only |
| Identity helpers | `services/adequacy/slack.py` — `VOLL_SLACK_PREFIX`, `involuntary_slack_mask`, source guard forbids inline `__voll_` literals |
| P6(a) | `multi_energy.py` — dedicated-bus roll-up only |

## Options (reconfirmed)

| ID | Approach | Honesty | Cost |
|---|---|---|---|
| A | Relabel bus `by_carrier` as ENS | **NO** | — |
| B | Dedicated-carrier buses (P6a) | **YES** when model shape allows | Shipped |
| **C** | **Per-Load slack** (`__voll_<bus>__<load>` or similar) | **YES** — shed is the Load's slack | High: creation, capture, ENS-cap, FMEA, restore, source guard, FE |
| D | Per-(bus, carrier) slack | Partial — still cannot split industrial vs residential on same carrier | Medium |

## Recommendation for P6(b)

**Ship option C** (per-Load involuntary slack), not D.

- Shared-bus industrial+residential is the stated unmet need; D does not unlock it.
- Naming: extend `slack.py` as the sole owner of prefixes (`VOLL_SLACK_PREFIX` + load-id encoding); keep the source guard.
- Capture: migrate primary shed frame to snapshot × **Load** (or × slack-id with Load map); keep a bus roll-up for electrical ENS-cap / FMEA default so electrical-only path stays bit-stable.
- ENS-cap / shed-hours: continue **electrical Load** scope (canonical carrier), not “all Loads”.
- DtC / P4b: can optionally adopt Load-scoped critical vs noncritical once C exists; until then keep bus-aggregate honesty pin.
- Migration: if a Load has blank name collision risk, fail-closed preflight rather than silent merge.

**Do not ship** a soft “by_carrier on shared bus” escape hatch.

## Touch list (implementation order when unblocked)

1. **TDD red:** shared-bus fixture (AC industrial + AC residential on one bus) — today `multi_energy` → `not_established`; target after C: per-Load / per-identity ENS in capture + section.
2. `slack.py` — Load-scoped name helpers + involuntary mask still by carrier tier.
3. `assumptions.py` — create one slack per Load (not per bus); size `p_nom` from that Load’s p_set.
4. Lost-load capture + `electrical_columns` Load analogue.
5. ENS-cap / metrics / FMEA / DtC consumers — prefer Load columns; bus roll-up derived.
6. `multi_energy.py` — shared-bus OK when Load carriers dedicated **or** per-Load capture present; honesty pin `per_load_slack`.
7. FE — show per-Load / per-carrier ENS without inventing values when capture missing.
8. QA gate — electrical-only regression (P6a electrical skip + existing ENS-cap suites).

## Out of scope for P6(b)

- Mixed VoLL prices on the same Load (still one cost per slack).
- DSR tier redesign (already separate in `slack.py`).
- Climate P8(b), chat tools, Class-C authoring UI.

## Gate

Spike only — **no implementation commit** until product confirms C (vs keep fail-closed shared-bus forever). Acceptance when implementing: shared-bus industrial+residential fixture shows distinct shed; electrical default unchanged; source guard green.


## Implementation status (2026-09-24)

Product confirmed option **C**. Shipped on this branch:

* `voll_slack_name(load_id)` + per-Load creation in `assumptions.py`
* Capture: `lost_load_t` / `lost_load_load_period_mwh` by Load; `lost_load_bus_period_mwh` roll-up
* `multi_energy` prefers `per_load_slack` when Load-period capture present
* `electrical_columns` classifies Load or bus columns
* Tests: `test_energy_hub_multi_energy_p6b.py`
