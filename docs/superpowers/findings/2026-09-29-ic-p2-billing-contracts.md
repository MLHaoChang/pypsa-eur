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
| 2.1a-ii | Tiers inside TOU windows (URDB semantics, proportional split) | FAIL → **PASS WITH CONDITIONS**, closed (no reviewer round 3; the gate assessor verified the closure by probe) |
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

The first table is the evidence the gate assessor judged. The assessor's conditions, and the re-run after closing them, follow in "Gate assessor verdict".


| Check | Result |
|---|---|
| `tests/qa_billing_contracts.py` | **38/38.** A: R1 imported through the URDB route, attached by `import_tariff_ref` through the config route; REopt parity to the cent for 2023 and 2024, both branches. B: R2 and R3′ imported; R3 (cases 2 and 3), R4a and R4b to the cent; the engine on a 15-min year with 8 items takes **0.27 s** (bound 10 s). C: the US site with a PPA, a CfD on a Library reference price, and DR on an active DSR bus; every settlement line matches its hand formula to the cent; the gap is fully attributed; the objective residual equals the DSR slack cost exactly. D: a `changes_dispatch` PPA with a Library tariff and reference price; gap 0 before and after save → load and after a bundle round trip; the line equals the row; both pins are carried. |
| All QA drivers (`tests/run_qa_drivers.py`) | **24/24 passed** (includes `qa_billing_contracts` and `qa_commercial_lp`) |
| Full backend suite (`-m "not slow"`, Python 3.12 venv), on the tree at 40b0c4f, run while the review fixes landed | **6,785 passed, 31 skipped, 3 failed** (44 min). Two failures were the route registries (WP2.5 review F2), fixed in f618d84. The third (`test_chat_e2e::test_run_simulation_dispatcher_targets_route_handler_not_service_fn`) reads source at run time; the source watcher attributes it to concurrent edits, and it passes on the head. |
| Every test file touched after the full run started (chat tools, manifest, results billing/seam/facade/range/golden, gap, site billing, contracts, series, library, URDB, PPA, settlement, seam, tripwires, audit) | **1,122 passed, 18 skipped, 0 failed** on the head |
| Frontend `vitest run` | **178 files, 1,966 tests passed**. An earlier run concurrent with the backend suite had 3 transient failures that did not reproduce. |
| Frontend `tsc --noEmit` | clean |

## Findings and carried items

- **ADR-0002 live-API probe: NOT RUN. The Library chat tools (WP2.4c) are unverified against a live API.** This environment has no provider credentials (no Anthropic key, no local Ollama); both `test_live_probe_*` tests skip. Before the chat surface is called done, someone with credentials runs and records (probe name, date, model, outcome) in this note and the plan:
  1. the manifest probe: `PYPSA_GUI_TEST_LIVE_ANTHROPIC=1 pytest tests/test_llm_provider_seam.py -k live_probe_anthropic_wire`. The openai-wire probe counts **only through a saved profile with `tools=True`**: `_ensure_live_openai_profile` creates its profile with `tools=False`, so no tool schemas are sent. Either probe's prompt is "No tools", so it proves only that the vendor accepts the manifest with the new schemas;
  2. therefore also one live turn that exercises the tools: upload `tests/fixtures/investment_case/oracles/r1_leap_year.urdb.json` to a project, then ask the assistant to list the Library tariffs, import the upload as a URDB tariff named `probe_r1` with `valid_from` 2023-01-01, show it, and attach it (a commercial config with `poc_link` set first). Expected tool calls: `list_library_items`, `import_urdb_tariff`, `get_library_item`, `attach_tariff`; expected result: `commercial.import_tariff_ref` pinned to `probe_r1` v1. Record the transcript.
- **The DSR slack cost is not a cost row** (pre-existing, from the adequacy DSR tier). When the DSR slack dispatches, it is in the LP objective but not in `cost_breakdown`, so the objective decomposition reports it as `residual_gap_eur` — the same design as the VOLL slack. The `objective_decomposition` docstring named only VOLL slacks; it now names the DSR slack too (the payload carries no separate term). Gate check C proves the residual is exactly that cost. **Carried into P4:** decide whether it is a cash flow (the site pays for shedding) or an opportunity cost (a DSR price is a value of shed load) before it becomes a row.
- **Engine-side error in a windowed tier split** (WP2.3 accepted residue). `tier_allocation` is billed − LP by the plan's definition. LP-record corruption is caught (volume, rate, width), but an error in the engine's own proportional split would be absorbed. An independent recomputation would close this.
- **Chat bug outside P2 scope — fixed by #63.** `update_component` with a `name` in `attrs` raised a TypeError (duplicate `name` keyword) on every class (`_get_schema(...)(name=name, **attrs)`). #63 (9aa87f8) landed on this branch while the gate was open: the rename now goes through the PUT handler's guarded rename, with dispatch tests. It is a chat change, so the owed ADR-0002 probe covers it too.
- **WP2.5 recorded, no action:**
  - a few readers use the live frames rather than `result_df` (equal after an LOPF);
  - the stored settlement record (`contracts_record`) is P4's to persist;
  - line `period` keys stay None / int beside the payload's "_" / string keys.
- **R2 hand translation:** its demand settlement changed from `h` to `15min` under the importer's absent-`demandwindow` rule (PROVENANCE updated). The engine oracle is unchanged on its hourly axis.
- **Postgres migrations** for new DB columns: none in P2. The Library items share the P1 table.

## Gate assessor verdict

**Round 1: PASS WITH CONDITIONS.** The assessor re-ran the driver (38/38), all QA drivers (24/24), 680 targeted tests (every file changed since the full-suite tree) and `tsc`, and probed scenarios C and D independently. Its conditions, and what was done (details in the plan's "Phase 2 e2e QA gate"):

1. **H1–H3 hand-rated bills were missing** (binding; spec §13 and the plan's oracle table). Now committed: `bills/h1_de_rlm.json` (DE, 296,457.36), `h2_nl_business.json` (NL, 266,097.52), `h3_us_ci.json` (US C&I, 128,227.64). They are generated by a stdlib-only script beside them, regenerated by a test, rated to the cent by the engine test and by the driver's new scenario E. H1 and H3 matched at the first run. H2 missed by a cent because its fixture put an expected value on an exact half-cent tie; the fixture was corrected, and the engine was not changed.
2. **ADR-0002 live probe: NOT RUN** (binding, owed; see above).
3. **Driver hardening:** non-vacuous gap and non-zero PV / DSR in C; the hand PPA formula and the per-kind gap after reload and bundle import in D; R1 facility values in PROVENANCE.
4. **Upload text sanitization** finished (`startdate`, REopt label, `urdb_invalid` message, refusal reasons; `_fit` bounds the first entry), with a test.
5. **Note and plan corrections** (this section, the WP2.1a-ii record, the probe procedure, the DSR wording).
6. **The chat bug:** fixed by #63, which landed on this branch while the gate was open.
7. **Carried into P4:** the DSR cash-flow decision and the windowed-tier split residue.
