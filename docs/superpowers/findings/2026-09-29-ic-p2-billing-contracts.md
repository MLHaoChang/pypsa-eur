# Edge Investment Case: Phase 2 end-to-end QA (billing pass and contracts)

**Plan:** `docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md`. **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md`, §5.5 and §15 (rewritten in WP2.3).
**Branch:** `claude/energy-tool-features-research-fdixs0`. **Head at the gate:** the commit carrying this note.

## What Phase 2 delivers

| WP | Deliverable | Review rounds → final verdict |
|---|---|---|
| 2.0 | FOM / capex convention merge; P1 row and drift hygiene | PASS WITH CONDITIONS → **PASS** |
| 2.1a-0 | Demand windows keyed by period name (engine and LP), versioned demand hash | FAIL → **PASS** |
| 2.1a-i | Per-day fixed charges, tariff capacity items (per kW / kVA year, annual measured peak), demand tiers | FAIL → conditions → **PASS** (round 3) |
| 2.1a-ii | Tiers inside TOU windows (URDB semantics, proportional split) | FAIL → condition → **PASS** |
| 2.1a-iii | Designated-month and cyclic ratchets (REopt lookback parity) | conditions → **PASS** |
| 2.1b | Site billing adapter (`bill_site`), compact billing frames, `per_item_sampled` | conditions → **PASS** |
| 2.1c | LP: convex demand tiers, windowed tiers, the new ratchet modes, a predicted tier for non-convex tiers, tariff capacity items | FAIL / conditions → **PASS** per sub-WP |
| 2.2-0 / 2.2a / 2.2b | Settlement inputs (DSR commit, reference series, reserved `ic:` names); contract settlement (PPA variants, CfD, DR, lease, EaaS, retail) | FAIL / conditions → **PASS** (round 3) |
| 2.2c | Contracts on the config; double-count preflight (`dr_without_dsr`, `ppa_export_double_count`, sleeved, `eaas_on_poc`, `meter_bypass`) | FAIL → conditions → **PASS** (round 3) |
| 2.2d | `changes_dispatch` PPA (buyer case) as an LP **objective term** | FAIL (CO2 clobbering) → conditions → **PASS** (round 3) |
| 2.3 | Billing vs LP gap per item kind, with computed causes and the `billing_gap_unexplained` warn gate | conditions → conditions → **PASS** (round 3) |
| 2.4a / 2.4b-0 | Library items (tariffs, contracts, agreements), pins v2, `import_tariff_ref`; binding service refactor | conditions → **PASS** |
| 2.4b-i | URDB importer (route, refusals by name, `accept_partial`, `tariff_incomplete`), R1 oracle added | conditions ×2 → **PASS** (round 3) |
| 2.4b-ii | Series and meter-data import (CSV / xlsx, unit and label, site clock, monthly history) | FAIL → conditions → **PASS** (round 3) |
| 2.4c | Library chat tools (list, get, import URDB from an upload id, attach) | FAIL (manifest) → conditions → **PASS** (round 3); **ADR-0002 live probe owed** |
| 2.5 | `/results/billing` and `/results/cfe_score` | FAIL → conditions → **PASS** (round 3) |

Each round's findings, and what was done about them, are in the plan under the WP.

## Gate evidence (2026-09-29)

| Check | Result |
|---|---|
| `tests/qa_billing_contracts.py` | **38/38.** A: R1 imported through the URDB route, attached by `import_tariff_ref` through the config route; REopt parity to the cent for 2023 and 2024, both branches. B: R2 and R3′ imported; R3 (cases 2 and 3), R4a and R4b to the cent; the engine on a 15-min year with 8 items takes **0.27 s** (bound 10 s). C: the US site with a PPA, a CfD on a Library reference price, and DR on an active DSR bus; every settlement line matches its hand formula to the cent; the gap is fully attributed; the objective residual equals the DSR slack cost exactly. D: a `changes_dispatch` PPA with a Library tariff and reference price; gap 0 before and after save → load and after a bundle round trip; the line equals the row; both pins are carried. |
| All QA drivers (`tests/run_qa_drivers.py`) | **24/24 passed** (includes `qa_billing_contracts` and `qa_commercial_lp`) |
| Full backend suite (`-m "not slow"`) | SUITE_RESULT |
| Frontend `vitest run` | VITEST_RESULT |
| Frontend `tsc --noEmit` | clean |

## Findings and carried items

- **ADR-0002 live-API probe: NOT RUN.** This is owed for WP2.4c. This environment has no provider credentials (no Anthropic key, no local Ollama). Before Phase 2 is called done for the chat surface, someone must run:
  - `PYPSA_GUI_TEST_LIVE_ANTHROPIC=1 pytest tests/test_llm_provider_seam.py -k live_probe_anthropic_wire`, or the openai-wire probe through a saved profile;
  - plus one live turn that calls `list_library_items`, `get_library_item`, `import_urdb_tariff` on an uploaded file, and `attach_tariff`.
- **The DSR slack cost is not a cost row** (pre-existing, from the adequacy DSR tier). When the DSR slack dispatches, it is in the LP objective but not in `cost_breakdown`, so the objective decomposition reports it as `residual_gap_eur`. This is the same design as the VOLL slack and is documented in `objective_decomposition`. Gate check C proves the residual is exactly that cost. For the investment case (P4) it should become a row: the site pays for shedding.
- **Engine-side error in a windowed tier split** (WP2.3 accepted residue). `tier_allocation` is billed − LP by the plan's definition. LP-record corruption is caught (volume, rate, width), but an error in the engine's own proportional split would be absorbed. An independent recomputation would close this.
- **Chat bug outside P2 scope:** `update_component(Bus, attrs={"name": …})` raises a TypeError (duplicate `name` keyword). It is recorded for a separate fix; the `spawn_task` attempts to queue it timed out.
- **WP2.5 recorded, no action:**
  - a few readers use the live frames rather than `result_df` (equal after an LOPF);
  - the stored settlement record (`contracts_record`) is P4's to persist;
  - line `period` keys stay None / int beside the payload's "_" / string keys.
- **R2 hand translation:** its demand settlement changed from `h` to `15min` under the importer's absent-`demandwindow` rule (PROVENANCE updated). The engine oracle is unchanged on its hourly axis.
- **Postgres migrations** for new DB columns: none in P2. The Library items share the P1 table.
