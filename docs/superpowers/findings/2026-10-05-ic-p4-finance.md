# Edge Investment Case: Phase 4 end-to-end QA (the single-owner finance engine)

**Plan:** `docs/superpowers/plans/2026-09-30-edge-investment-case-p4.md`. **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md`, §6 and §15.
**Branch:** `claude/energy-tool-features-research-fdixs0`. **Head at the gate:** the commit carrying this note.

## What Phase 4 delivers

A pure-numpy project-finance engine for one owner (`services/finance/`), validated against NREL SAM
"Single Owner", fed from the P3 value-flow ledger through an adapter, run as a study through the routes,
explained through four chat tools, exported as xlsx and shown in the Investment tab.

| WP | Deliverable | Review rounds → final verdict |
|---|---|---|
| 4.0 | SAM oracle fixtures (S1, S1b, S1l, S2, S2c, S2t, S3, S3d, S3f) and their mapping, plan amendments, the C1 tripwire, the hourly fixture | **PASS WITH CONDITIONS** (4 binding, all fixed; no second round was run — the fixtures are exercised by every later WP's SAM parity and by this gate's section A) |
| 4.1 | Timeline, operating cashflows, escalation / indexation / tenor, terminal value | conditions → **PASS** |
| 4.2a/b | Debt: amount / gearing, annuity / level, fees, IDC, DSCR sculpting, the gearing cap, DSRA, reserve interest | conditions → two findings → **PASS** (round 3) |
| 4.3a | Tax and depreciation; `us_federal` and `eu_de` packs | conditions → **PASS** |
| 4.3b | `eu_nl` and `ca_federal` packs (a second `ca_federal` version for S.C. 2026 c. 3) | FAIL (`ca_federal`) → **PASS** |
| 4.4 | Incentives with dated rules (ITC / PTC / grants; §45Y / §48E, FEOC, PWA, the CA Clean Tech ITC) | FAIL → two minor → two → **PASS** (round 4) |
| 4.5 | Metrics, solve-for-PPA, the WACC gate, the counterfactual / lifecycle split, LCOE | FAIL → one → **PASS** (round 3) |
| 4.6a | The adapter: ledger → `FinanceCase`, the counterfactual (C13), template checks | FAIL → **PASS** |
| 4.6b | Finance inputs route (If-Match), the study runner and routes, staleness, persistence, the report | six findings → conditions, closed |
| 4.6c | Chat tools: `run_investment_case`, `get_investment_case`, `solve_ppa_price`, `explain_cashflow` | conditions → **PASS** |
| 4.6d | xlsx export (`GET /results/investment_case/export.xlsx`) | reviewed inside WP4.6b's rounds (filename and formula injection, control characters, the About sheet); the gate driver found the Summary-sheet gate bug below |
| 4.7a/b | Frontend: finance inputs editor, run / results / export | four findings → FAIL (two, from the 4.6b report changes) → **PASS** (round 3) |

Each round's findings, and what was done about them, are recorded in the plan under the WP.

## Gate evidence (2026-10-05)

| Check | Result |
|---|---|
| `tests/qa_investment_case.py` | **243/243** (30 s) at 21da322; **245/245** after the assessor's conditions (b45ef40 and the conservation gate) — the sections below |
| The P4 regression list (the finance, investment-case and chat-IC files plus their billing / value-flow / registration neighbours) at 1979589 | **1,771 passed, 18 skipped** |
| **Full backend suite (`-m "not slow"`, Python 3.12 venv)** at 1979589, 12 chunks | 8,322 passed, 31 skipped, 11 deselected `slow`, **1 failed**: `test_hourly_assumption_audit` — the adapter's two `8760` sites (the C3 annual check and the `annualise` factor) were unlisted. Both are units; listed with that reason and the inventory re-pinned (8116895, a test-only change). |
| Chunk 07 re-run at 8116895 (the audit's chunk) | **809 passed, 7 skipped, 0 failed** → the full suite is **8,323 passed, 31 skipped, 0 failed** at the final code |
| **All QA drivers** (`tests/run_qa_drivers.py`) at 8116895 | **26/26 passed** (the 25 of P3 + `qa_investment_case`; `qa_value_flows`, `qa_billing_contracts`, `qa_commercial_lp` among them) |
| Frontend `vitest run` | **253 files, 2,812 tests passed** |
| Frontend `tsc --noEmit` | clean |

### The driver — `tests/qa_investment_case.py` (245 checks)

- **A (114): SAM to T.** S1, S1b, S2, S3 and S3f through `run_case`; every per-year array, the IRRs
  (≤ 1e-5; pre-tax against a stdlib IRR of SAM's own pre-tax cash), NPV, LCOE, debt size and DSCR. S1b's
  solved price is SAM's 144.6559 $/MWh. S3f: our debt is exactly 0.6·TIC and SAM's minus ours is exactly
  0.36·f·TIC (1,109,473.20); everything downstream is compared at SAM's debt.
- **B (8): the recorded deviations sized** (S1l, S2t, S3d — below).
- **C–G (24): F1–F5 by hand** in stdlib arithmetic: IDC and fees; the DE GewSt / KSt loss pools and the KSt
  rate path; §163(j) carryforward and the 80 % NOL; escalation / indexation / tenor; terminal value and
  replacement.
- **H (27): F6, the counterfactual on a toy site,** set up through the config routes and built by the
  adapter, against an hour-by-hour working: both bills, the demand charge, the commodity on both sides,
  incremental 557,700 $/yr, S = 503,700, year 2, LCOE 176.18 $/MWh; the per-kWh levy variant (S 547,500)
  and the non-owner heat pump (incremental 725,600, its draw disclosed).
- **I (34): the integration fixture through the routes** — finance PUT (If-Match) → solve → value flows →
  `POST /results/investment_case` → poll → report (export and `detail=full`) → xlsx round trip. A finite
  equity IRR (the pin 0.36207517, matched to 1e-8 — a pin, not an oracle); Σ cashflow lines =
  `cash.equity_post_tax` in every year; the counterfactual block and the CFADS definition on the About sheet;
  the year-1 incremental-EBITDA identity with both bills rated by hand; the COD-year lines = the owner net
  of `/results/value_flows`; the LCOE from its definition (143.23). The production path with the
  `us_federal` pack (no test hook) gives a finite IRR and reconciles.
- **J (4): staleness** — a finance edit → `changed == ["finance"]`; a discount-rate edit →
  `["solver_config"]`; each revert → current.
- **K (7): chat** — the summary and `explain_cashflow` (reconciles; stream PVs sum to the NPV) on the stored
  report; `solve_ppa_price` on S1b returns SAM's price and changes nothing stored.
- **L (25): refusals and C12** — the 7-day fixture refused `template_not_annual:168` through the routes
  (headlines None, `not_established` in the xlsx); with `annualise`, a number flagged
  `template_annualised:52.14` and the demand charge 12 × the monthly charge; a None ledger line and a
  missing escalation class → operating `not_established`; None is never 0 in the export view, the payload,
  chat or the xlsx.

The F6, LCOE, bill and F1 / F4 oracles were spot-checked as independent arithmetic by the WP4.6a reviewer
(round 2). Labelled as not independent: the integration IRR (a pin from the adapter test) and its LCOE
check (it takes the product's incremental and equity cash, so it verifies the LCOE definition and the cost
rule, not the cash).

**Found by the driver:** the xlsx Summary sheet read `wacc_vs_discount_rate_consistent` and
`conservation_ok` from the dump's top level (they live under `gates`), so it always wrote them
`not_established` — fixed (it reads the export view) and tested. **Found by the assessor** (condition 3c):
P4 never set `gates.conservation_ok`, so those rows were `not_established` even when the ledger
conserved. The adapter now carries the P3 ledger's conservation result on the case
(`FinanceCase.conservation_ok`, additive) into the report's gates; a driver step asserts True on the
integration fixture's About sheet and export view.

**Found by the assessor and fixed at the gate** (the driver now 245 checks):
- **Condition 1 — solve-for-PPA "ok" at a price whose IRR is not the target.** NPV = 0 at the target is
  necessary, not sufficient: the truncated equity cash can have several IRRs (S3 at 15 % / year 12 → 71.01
  at an IRR of −10.5 %). `solve_ppa` now checks the C9 IRR at the solved price; if it is not the target
  the status is `solve_ppa_irr_ambiguous:<irr>`, the candidate price kept as information
  (`solve_ppa_candidate_price`), the headline None; the chat refuses with its own kind. A unique target IRR
  over several sign changes stays ok, flagged. Tested on S3 (and S2's targets stay clean). 7b62fdd.
- **Condition 2 — C12: an unknown ledger line dropped.** A not-established line has no `CashflowLine` (an
  amount is a number — the contract is unchanged); it was missing from the lines, the xlsx and the
  per-counterparty totals (the retailer showed −1,800 over an unknown 23.7 M energy charge). The project
  payload now lists `lines_not_established` (key, stream, counterparty, source, years); that
  counterparty's total is None (`counterparties_not_established`); the xlsx writes a `not_established` row
  per year; the frontend shows those cells "not established". Tested: backend unit, a driver L step
  through the adapter, frontend pivot and view. b45ef40.

## SAM deviations (each recorded in the plan; sized by a test except where the size column says it is from a review probe)

| Deviation | Ours | SAM | Size | Oracle |
|---|---|---|---|---|
| Gearing with a closing fee (C8) | D = g·TIC (`gearing_base="capex"`) | D = g·TIC·(1 + g·f), a one-step | SAM − ours = 0.36·f·TIC exactly (1,109,473.20 on S3f); S3f equity IRR at our debt 0.3296 vs SAM 0.3431 | S3f |
| The DSRA in the gearing base | `gearing_base="capex"` excludes it; `"total_uses"` reproduces SAM to 1e-7 | D = g·(TIC + DSRA(D))·(1 + g·f) | the DSRA term: SAM − ours = 1,232,519.76 on S3d | S3d |
| Salvage in CFADS | CFADS = revenue − costs − replacement (terminal excluded) | sculpts on the salvage when the tenor reaches the last year | SAM D − ours = salvage / 1.3 / 1.07^25 exactly | S2t |
| A negative sculpting basis | pays 0, flags `sculpt_basis_negative_no_service` | books a negative service (a negative debt) | case-dependent; no oracle can express it (tested by hand) | — |
| The remaining tax basis at the end | written off in the last year (`remaining_basis_written_off`) | dropped | IRR +0.23 pp on S1l (SL-39); 0 on every other oracle | S1l |
| PTC rounding | the statute: the base amount to 0.05 ¢ | $0.001/kWh | ±$65k a year, +0.036 % over the term — **from the WP4.4 review probe; the test pins our rounding** (2027: 0.6294 → 0.65 ¢), not the size | — |
| A taxable grant's year | taxed in the year received (index 0) | year 1 | −0.94 bp of IRR on S1 + $5M — **from the WP4.4 review probe; the test pins our year**, not the size | — |
| Not modelled (zeroed in the oracles) | — | property tax, insurance, working-capital / receivables / equipment reserves, state ITC, CBI / IBI / PBI, TOD revenue factors, the mid-quarter convention, construction financing cost | stated | — |

No tolerance was loosened for any of these (plan: "a SAM convention not followed is a recorded deviation —
never a loosened tolerance").

## The counterfactual (C13)

Returns are on the owner's **incremental** cash: the owner's total operating cash minus a **counterfactual
supply cost** — the same site, tariff and connection agreement **without the owner's investable assets**.
The adapter builds one **only when the owner is the site party** (the bill payer); otherwise there is no
counterfactual and incremental = total (the payload's block says so with its basis):

- **The meter:** import = the site-side electric loads' demand minus the actual dispatch's shed (the served
  load; the same shed on both sides, since P4 does not re-dispatch; disclosed `load_shed_excluded:<mwh>`,
  never valued), **plus the electric draw of non-owner conversion Links** (a heat pump the site keeps;
  `counterfactual_includes_conversion_load:<mwh>` — WP4.6a B2); export 0. An owner conversion asset serving
  a non-electric load, a lossy PoC chain, an unknown draw or a hub allocation → not established.
- **The bill:** the same tariff rated by `billing.rate_meter` (factored out of `bill_site`; every P2 bill
  pinned unchanged — 117 calls identical in the reviewer's harness).
- **The commodity:** the grid-supply generator's marginal cost × the served load, keyed like the actual
  commodity line (so it nets as a saving, never an asset cost — WP4.6a B1), cross-checked against the
  ledger to max(0.01, 1e-9·|ledger|).
- **What exists without the investment:** connection fees, contracts on no owner asset
  (`counterfactual_keeps_contract:<id>`) and the cost lines of site assets the owner does not own (a boiler
  and its gas).
- **Degradation (C5):** S = the counterfactual − actual volume-billed import items (per-kWh on import, levies
  and certificates included — WP4.6a B7) + commodity, carried as a pair degrading with the asset
  (`source="degradation"`, value — never an own cost in the LCOE). S is None when the counterfactual is
  blocked.
- **Provenance:** the report's project payload carries a `counterfactual` block (basis, lines, sources, what
  is not established, reasons, flags) and a `counterfactual_hash` over the tariff, served load, connection
  and commodity; the cashflow lines carry the counterfactual's lines negated (`counterfactual:*`), so each
  year's lines sum to the post-tax equity cash. The lifecycle NPV is on the owner's **total** cash.
- **Money year (B4):** contract lines are in their period's money (P2 indexes contracts per period); tariff,
  connection, export and cost lines in the base year.

## Pack sources (C11: every rule cited; pinned per version in `pack_hashes.json`)

| Pack | Versions | Sources |
|---|---|---|
| `us_federal` | 2026-01-01 | IRC (26 U.S.C.) as amended through Pub. L. 119-21 (2025) (§11, §168 MACRS and bonus, §172 NOL 80 %, §163(j), §45Y / §48E with the termination, phase-out, FEOC and PWA rules, §48(c)(6) storage); IRS Pub. 946 (2024); the Federal Register notice and the Notice 2025-42 vacatur for the beginning-of-construction dates |
| `eu_de` | 2026-01-01 | KStG (§23 as amended by the 2025 investment act), SolZG 1995 (§4), GewStG (§8 Nr. 1, §10a, §11), EStG (§4h Zinsschranke, §4 Abs. 5b, §7, §10d) as amended through BGBl. 2025 I Nr. 161; BMF AfA-Tabelle AV (2000) |
| `eu_nl` | 2026-01-01 | Wet Vpb 1969 (brackets, loss relief, earnings stripping incl. the 20 % cap), Wet IB 2001 (2026); Belastingdienst |
| `ca_federal` | 2026-01-01, 2026-03-26 | ITA and Income Tax Regulations (CCA classes 43.1 / 43.2, the accelerated first-year programmes, loss rules, the Clean Technology ITC with its acquisition-date rule); S.C. 2026 c. 3 (the second version); CRA; Natural Resources Canada |

Pins at the gate: `ca_federal` 2026-01-01 `fb50f1d768b16ce8`, 2026-03-26 `0810f6d03bb2eb7e`; `eu_de`
`b06c3df900697d3a`; `eu_nl` `e9d993387ec050e0`; `us_federal` `77ad0d6516c8d403`.

## Stated limits carried forward (not P4)

- B2's conversion load uses the actual dispatch's draw (the C13 no-re-dispatch rule); B5 counts billing
  months on the tariff clock (a week straddling two months scales ×6, flagged); B3 makes every period None
  on a blocking P3 flag (P3's flags are site-wide) — narrowing to contracts that can involve the owner is a
  P5 item, as is `ledger_conservation_failed` as a None line.
- The staleness key digests the whole solver config (minus finance / commercial): a solve-only setting also
  marks the report stale — the safe direction, stated in the UI. Staleness is reported by
  `GET /results/investment_case` (the frontend and the chat show it); `GET …/report` and `export.xlsx`
  carry only the assumptions hash, so a workbook exported after an edit shows the earlier figures with that
  hash as the only tell (an About "current at export" row is a P5 item).
- No tax pack covers a financial close before 2026-01-01 (`tax_pack_not_found`); earlier closes need a
  pack version.
- Review items noted and not taken (each recorded under its WP): in `carryforward` mode the lifecycle tax
  never offsets the supply-cost losses; the unlevered book-value terminal carries the levered basis;
  §163(j) / Zinsvortrag interest still carried forward at the end of the axis is not reported; the
  slow-contraction debt fixed point can stop up to 6e-5 relative from the root; the interest caps use an
  EBITDA proxy; `test_finance_debt._close` compares arrays at an array-max relative tolerance (the
  driver's per-year T covers the gate).
- openpyxl writes 16 significant digits: a 17th-digit ulp can move on read-back (the driver compares the
  xlsx at relative 1e-15).
- Tax equity (P7); per-asset CODs, staged builds, archetype streams, a generic production incentive (P5);
  realistic-dispatch revenue (P6); scenarios and P50 / P90 (P7).

## ADR-0002 live probe

**NOT RUN — owed, with P2's and P3's.** This environment still has no provider credentials (no Anthropic
key, no local Ollama). P4's four chat tools are exercised in process through `DISPATCHERS`, the four guard
suites (schema match, argshape, endpoint map, identity), the error-kind manifest (15 new kinds, all
`inline`; the frontend manifest test) and the driver's section K — not against a live API. **The chat
surface is not done until this is run.** Someone with credentials runs, at a head that includes P4, and
records (probe name, date, model, the expected and actual calls, the outcome and the transcript) here and in
the plan:
1. **The manifest probe** (P2 step 1) — it now also carries `run_investment_case` (`execution_long_running`),
   `get_investment_case` (`detail` / `page`), `solve_ppa_price` (with `owner`) and `explain_cashflow`; a
   vendor rejecting that schema breaks every tool call. Then P2's and P3's steps.
2. **The investment-case turns:** "run the investment case" (one `run_investment_case`, Guided confirmation,
   then `get_investment_case` once done); "what PPA price gives 11 % in year 20?" (`solve_ppa_price`, the
   answer quoting `money_year` and that nothing was saved); "why is the equity IRR so low?"
   (`explain_cashflow`, the answer quoting its stated method and rate basis, never summing cashflow pages
   whose `equity_post_tax_cash` is not established).

## Assessor

**Assessor (independent; two rounds).** **Round 1 (21da322): PASS WITH CONDITIONS.** On an archive copy:
the driver 243/243; 486 finance / IC tests passed; the P1–P3 drivers 30/30, 49/49 and 235/235; the IC vitest
subset passed; `tsc` clean. Probes confirmed: an abort mid-run; an edit during a run (reported stale); a
two-period case end to end (no step at the switch, contracts ending at their tenor, Σ lines = equity cash);
debt + DSRA + ITC + counterfactual reconciling to 3e-8; every dated pack edge (US bonus, the `ca_federal`
version, the §48E 2033–36 shares, the wind/solar termination, FEOC, the DE degressive window); and
solve-for-PPA with debt on S2, S2c, S3d and S3f. Three conditions: (1) solve-for-PPA returned `ok` where the
truncated equity cash has several IRRs and the engine's own IRR is not the target (S3 at 15 % / year 12 →
71.01 at −10.5 %; at 20 % / year 15 → −8.5 %); (2) C12: an unknown ledger line was dropped from the cashflow
lines, the xlsx and the per-counterparty totals (the retailer showed −1,800 over an unknown 23.7 M energy
charge); (3) note accuracy (WP4.0's verdict, deviation sizes taken from review probes,
`gates.conservation_ok` never populated, the site-party scope of the counterfactual). **Round 2 (e4d629e):
PASS.** The driver 245/245; 672 backend tests passed (the guard suites included); 137 IC vitest tests
passed; the manifest tests passed; `tsc` clean. Re-probed: S3's two ambiguous targets are now
`solve_ppa_irr_ambiguous` with no headline price (a candidate price is kept as information; the chat refuses
with its own kind), while the other S3 targets and all six S2 targets stay `ok` at the exact target. The
unknown line is now listed in `lines_not_established` (15 years); the retailer's total is None; the xlsx has
15 `not_established` rows; the frontend cells read "not established". `conservation_ok` is True in the
export, the full report and the About sheet. The note corrections are made. Non-binding and stated: the JSON
`cashflow_lines` still sum to a number unless `lines_not_established` is read; the xlsx and `GET …/report`
carry no staleness marker (P5). The ADR-0002 live probe is owed.

After the round-2 fixes the P4 regression list ran again at e4d629e: **1,774 passed, 18 skipped**.

**Phase 4 is closed for the single-owner finance engine.** The chat surface (P2's, P3's and P4's tools)
stays **not done** until the ADR-0002 probe above is run and recorded.
