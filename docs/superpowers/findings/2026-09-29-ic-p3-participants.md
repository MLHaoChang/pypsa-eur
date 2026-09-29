# Edge Investment Case: Phase 3 end-to-end QA (participants and value flows)

**Plan:** `docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md`. **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md`, §7 and §15.
**Branch:** `claude/energy-tool-features-research-fdixs0`. **Head at the gate:** the commit carrying this note.

## What Phase 3 delivers

| WP | Deliverable | Review rounds → final verdict |
|---|---|---|
| 3.0 | Value-flow config (participants, externals, assignments, allocation key), its route with If-Match, hashing, FE types | conditions → **PASS** |
| 3.1 | The ledger: sources with legs, lines, coverage, the four conservation checks, reconciliation to `cost_breakdown` through the bridge terms | conditions → conditions → **PASS** (round 3) |
| 3.2 | Templates (`single_owner`, `btm_ppa`, `landlord_tenant`, `dso_developer`, `energy_hub`), drafts with open money fields, edited / stale / outdated status | FAIL → conditions → **PASS** (closed) |
| 3.3a | Energy-hub allocation: metered energy per member, peak items, the four keys, floor source month | conditions → FAIL → **PASS** (round 3) |
| 3.3b | Group net-import LP variable (P2 carry-in): a net cost energy item priced on net import | conditions → **PASS** |
| 3.4 | `GET /api/results/value_flows` (the 31st result kind); chat `get_results` detail / paging and `define_participants`; five error kinds | conditions → FAIL → **PASS** (round 3); **ADR-0002 live probe owed** |
| 3.5 | Frontend foundation: API clients, the Investment tab shell, commercial writes | conditions → conditions → **closed** |
| 3.6 | Participants designer, the per-participant table, the Sankey | FAIL → **PASS** |
| 3.7a | Library browser (items, versions, pins, URDB import with refusals, series and meter uploads, attach) | FAIL → FAIL → **PASS** (round 3) |
| 3.7b | Tariff builder and the bill preview route (`POST /api/results/billing/preview`) | conditions → FAIL → **PASS** (round 3) |
| 3.7c | Contracts and connection-agreement editors | conditions → conditions → **PASS** (round 3) |

Each round's findings, and what was done about them, are in the plan under the WP.

## Gate evidence (2026-09-29)

| Check | Result |
|---|---|
| `tests/qa_value_flows.py` | **222/222** (35 s). See "The driver" below. |
| All QA drivers (`tests/run_qa_drivers.py`) | **25/25 passed** (includes `qa_value_flows`, `qa_billing_contracts`, `qa_commercial_lp`) |
| Full backend suite (`-m "not slow"`, Python 3.12 venv) | _pending_ |
| Frontend `vitest run` (on 2b908e3) | **248 files, 2,753 tests passed** |
| Frontend `tsc --noEmit` | clean |

### The driver

Every template case runs through the routes: POST template → drafts priced and confirmed through the
solver-config route → PUT value flows (If-Match) → solve → GET `/api/results/value_flows`. Each case
asserts status ok, `conservation_ok` True, all four checks in every period, the route's reconciliation
difference under a cent, and an independent reconciliation the driver rebuilds from
`/results/cost_breakdown`, the LP rows, `/results/billing` and the network's export revenue — equal to both
the ledger side and the route's bridge figure.

- **A. V1 `single_owner`:** the grid-supply commodity line = Σ w·p·mc (114,451.41); the fee line = the
  `network_capacity` row (30,636.99).
- **B. V2 `btm_ppa`** (an `as_consumed_btm` PPA and an EaaS; a second site PV so the export split varies):
  the developer's export-price share (20,577.16) and feed-in share (6,553.32), the PPA (29,639.85) and the
  EaaS (7,009.49) equal their hand formulas; the developer pays the PV / BESS cost lines, the site pays
  the bill, the PPA and the EaaS. Also the other two `btm_ppa` cases of the templates test table.
- **C. V3 `landlord_tenant`** with and without a saved lease: lease = payment × hours / 8760 to the cent;
  the landlord pays the leased assets, the tenant the bill.
- **D. V4 `dso_developer`:** DSR dispatches 437.5 MWh; DSO → developer availability (38.36) and activation
  (43,750.00) equal the hand formula.
- **E. `energy_hub`** under each of the four keys (switched through the route, no re-solve): the checks
  and the rebuilt reconciliation hold, every shared item is split in full, each line's method is right,
  keyed shares = amount × key / Σ key to the cent.
- **F. V5 fixture:** the group bill and per-member metered energy match the worked arithmetic; all 21
  hand shares to the cent under each key; the ledger passes every check.
- **G. V6 group net import:** the `energy_net_group` row = the billed `net_energy` (1,240,541.71); the
  objective gap ~2e-10 %; the billing gap has no gate and nothing unattributed.
- **H. Corruptions on the real V1 ledger:** a swapped payer / payee on the import-energy line and a
  dropped BESS cost source each turn coverage, reconciliation and `ok` False.
- **I. Bundle round trip** of the V2 project: after save → load and after bundle export → import,
  `value_flows`, the ledger (18 lines) and the flags are identical.

## Findings and carried items

- **ADR-0002 live-API probe: NOT RUN (owed, with P2's).** This environment has no provider credentials
  (no Anthropic key, no local Ollama). P3's chat changes (`get_results` with `result_kind="value_flows"`,
  `detail` / `offset` / `limit`; `define_participants`) are exercised in process through `DISPATCHERS` and
  the guard suites, not against a live API. Before the chat surface is called done, someone with
  credentials runs the P2 procedure (findings `2026-09-29-ic-p2-billing-contracts.md`) plus one P3 turn:
  on a solved project with a commercial config, ask the assistant to set up participants from the
  `single_owner` template and then show the value flows; expected calls `define_participants`
  (`template`) and `get_results` (`value_flows`); expected result a stored config (the value-flows route
  reads status ok) and a summary under 4,000 characters. Record probe name, date, model and outcome here
  and in the plan.
- **Recorded follow-ups (not blocking):**
  - `replacesInline` trusts the solver-config PUT's ref check; a hand-edited `solver_config.json` is not
    re-checked on load (WP3.7a round 2 INFO) — P4 hygiene.
  - The capacity fee's shape (unit, a single all-year period) is checked (`fee_eur_per_mw_year`) at the
    solve, not at the PUT (WP3.7c rounds 2–3) — the editor offers only the supported kind and units and
    states the one-period rule; the PUT check is P4 hygiene.
  - The contracts editor's staleness baseline is the raw stored list: a P0-era untagged list normalised by
    the editor's own connection save reads as "changed" once (a reload recovers it); the pre-save read
    and the save are two requests (WP3.7c round 2, accepted).
  - `appendContracts` reads the config twice (WP3.6 #8, LOW).
- **Carried from P2, unchanged:** the DSR slack cost as a cost row (P4 decision); the windowed-tier split
  residue; annual bands on monthly tiers.
- **Scope boundaries** (plan): annual cash flows, multi-party co-optimisation, DR on assets, allocation over
  periods longer than a month, realistic-dispatch ledgers, and net revenue items on the group net meter
  are not P3.

## Gate assessor verdict

_pending_
