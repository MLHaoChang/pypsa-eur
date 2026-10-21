# Bounded implementation plan and validation matrix

This is a proposed implementation sequence, not an implementation claim. Do not start by adding every feed or engine. Use existing Library items/series, immutable references, regional packs, approvals and evidence exports. Reconcile current master and pending Library PRs before each session. Human/domain review and real-use calibration remain pending.

## Data contract and UX

Keep five requested areas modular: **Energy/capacity**, **Electrical grid**, **Reliability**, **Commercial**, **Control strategy**. The data library is shared infrastructure, not a new workflow that forces every study to enable all modules. If the active frontend now presents additional/separate work cards, preserve its current card contract rather than reverting it to this research's labels.

An imported source envelope should preserve, using native fields where possible:

- Source/release/file/row identity, retrieval time and hashes; publication, effective, observation, delivery, projection and money years as distinct values.
- Territory, customer class/voltage, capacity/connection limits, eligible product, currency/unit, AC/DC or input/output capacity basis, price basis and period resolution.
- Timezone or explicit source time standard, interval-start/end convention, DST fold and revision/finality/quality status.
- Data class (`actual published tariff`, `historical market`, `reported transaction`, `executed benchmark`, `offered index`, `scenario`, `illustrative`, `private contract`) and uncertainty/missingness.
- Source-specific permitted commercial use/cache/display/derivative/export scopes, obligations, expiry and an explicit clearance decision. A source hash or software licence is not a clearance.
- Transformation lineage: raw units, conversion/index/FX inputs and pinned versions, rounding, resampling rule, supported/unhandled clauses and reason codes.

`SeriesMeta` already has unit/source-unit metadata, and `Participant`/`FinanceInputs` have currency fields. Extend only after proving a specific deficiency. `PriceSeriesRef` has no dedicated currency/unit field and several commercial amounts are EUR-named; audit the complete execution and export paths before designing migrations. A new semantic envelope may need its own pin because a numerical values/timestamps hash alone is not proof of unchanged currency/product interpretation.

Show concise scope/status badges in the Library: “historical benchmark”, “supported tariff subset”, “customer document required”, “rights review required”. A user selects geography, utility/customer class and product, previews clauses and basis, then explicitly applies an admissible value to the active project. Keep raw contract uploads and quotes private. The chatbot can explain verified Library evidence and propose supported changes through ordinary approval controls; it must refuse to invent a quote or mark an incomplete bill “checked”.

## Value by example and user group

| Example / group | First useful data | Optional module boundary |
| --- | --- | --- |
| C&I site | Exact utility bill, interval demand, tariff choice, export conditions, fuel and finance assumptions | Energy and Commercial first; detailed grid/reliability/control only when requested. |
| Data centre | Large-load service eligibility, connection entitlement, demand/standby/reactive charges, private redundancy/UPS and fuel assumptions | Reliability and grid constraints may be critical; public tariffs do not determine uptime guarantees or actual failure rates. |
| Renewable IPP / IPP+BESS | Node/hub DA/RT series, capture/basis risk, supported PPA benchmark and exact project cost rows | Add Commercial; reserve/capacity revenue only with accreditation/performance evidence. Control strategy is not automatically required. |
| Mining | Territory-specific industrial tariff and supplements, service voltage/interruptibility, process load, delivered fuel | Reliability and islanding may be useful; actual mine equipment/maintenance parameters remain separate customer inputs. |
| Hydrogen / ammonia | Electricity/PPA profile, process utilisation, supported cost basis, water/compression/storage, private off-take/support terms | Process detail and Commercial may matter more than grid/control detail. No automatic green certification or inferred off-take price. |
| Island / remote microgrid | Delivered fuel, customer tariff or avoided cost, equipment costs, replacement/maintenance and verified outage scenarios | Reliability/control can be enabled explicitly. A mainland spot-price series need not be relevant to an isolated study. |

These are presets and applicability checks, not separate databases or mandatory bundles. Single-period versus staged planning should select observation/projection/delivery vintages within the same example. Do not duplicate examples merely to carry a different price vintage. Historical stress-week inputs and synthetic scenarios must retain their labels and cannot be sold as forecasts.

## Sessions and dependencies

| Session | Deliverable / likely code seams | Completion gate |
| --- | --- | --- |
| NAM-0: admission audit | Review `models/commercial.py`, `models/finance.py`, Library `items.py`, `series_io.py`, `series_store.py`, current regional packs and commercial/finance export paths. Specify rights/product/currency pinning changes only where needed. | Demonstrate USD/CAD/MXN or explicit refusal at every money boundary; no broad engine changes based solely on schema names. Source policy cannot be bypassed by copying org data into common scope. |
| NAM-1: US tariff pilot | Reuse `services/library/urdb.py` and existing tariff/item storage. One actual supported commercial/industrial rate plus utility-source clauses and independent bill oracle. The recent reactive-charge record is a rejection fixture unless complete support is added separately. | Full chosen bill supported and independently reproduced, or clear refusal. No dropped clause, unexplained residual or fabricated demand interval. |
| NAM-2: one historical market series | Existing series import/store and `PriceSeriesRef`; prefer a supported region such as CAISO if exact intended rights are cleared. Pin one product/node/day before longer history. | Source calendar/component/revision tests, semantic identity pin, meaningful coverage report, no live trading/activation. Expand NYISO/MISO only with explicit native-market behavior assessment. |
| NAM-3: PPA benchmark references | Reuse the reference-reader pattern landed by PR #501 and review subsequent reference/apply UI work. Author a small, rights-cleared selection from executed solar and potentially wind evidence, not every workbook sheet. | No offered/executed or real/nominal confusion; reference display versus contract application separate; supported form only, source/assumption visible. |
| NAM-4: Ontario pilot | IESO product reader plus exact distributor tariff source and separate GA class rules if implemented. | Renewed-vs-legacy separation, delivery dates, product flags and a source-backed bill oracle. Class A historical inputs required; unsupported ICI refuses. |
| NAM-5: reported contracts | FERC EQR one current-quarter extract using current specification, join keys and revisions. | Reproduce an independently checked record/aggregate by product/unit with current Order 917 treatment; no all-PPA coverage claim. |
| NAM-6: Mexico pilot | Current CFE division/month/class source, corrected CENACE day and admitted FX metadata. | Actual current source retrieval and exact rights/time/MXN semantics. Historic original ZIP alone cannot pass. |
| NAM-7: cost/finance coverage extension | Add separately versioned regional rows for evidence-supported technologies; scoped CPI/FX/rate references and date-aware incentive inputs. | No missing currency fallback; equipment basis and cost inclusion reconciled; legal eligibility remains project-specific. |

NAM-0 precedes numerical imports. NAM-1/2/3 can be separate PRs after their admission contracts are agreed. NAM-4/5/6 should not block the initial useful US release. NAM-7 first reconciles PR #499 and current reference/apply work; do not duplicate either. Financial swaps, complete forward curves, collateral simulation and detailed reactive/minimum-bill engines are separate scopes, not hidden parser extensions.

Every implementation session should start with a failing semantic test for the actual behavior, preserve current refusals, run the relevant existing suites, add a release fragment and follow current repo review/premerge rules. This research adds no application code and does not assert that the matrix below already passes.

## Test and validation matrix

| ID | Input / scenario | Required outcome / independent evidence |
| --- | --- | --- |
| A01 | USD/CAD/MXN amounts through tariff, PPA/CfD, solver valuation, finance, result cards and exports | Correct source/case currency, declared FX conversion or explicit refusal. No EUR-labelled numeric relabelling. Independent small arithmetic oracle. |
| A02 | Same values/timestamps with changed currency, unit, node, product or effective date | Distinct semantic identity/version or stale/refusal; numerical series hash alone cannot certify equivalence. |
| A03 | Source permits internal use but not onward raw/derived distribution | Common seed, tenant-sharing, chat quoting and export obey exact policy; wrapper does not bypass it. |
| A04 | Mutable source publication / approval revoked / superseded rate | Existing project pin remains reproducible; new adoption requires reviewed version; no silent mutation. |
| T01 | Hand-computed supported tariff: energy tiers, TOU, fixed fee, demand charge, adjustment | Component-by-component bill equals independent source worksheet under declared currency/rounding. Bill total alone insufficient if components cancel errors. |
| T02 |15/30/60-minute demand; hourly source missing underlying peak | Exact supported interval computes correctly; insufficient resolution refuses, never reconstructs an unobserved peak. |
| T03 | Weekend/holiday, season boundary, DST repeated/missing hours, leap year | Exact tariff calendar rule applied; unsupported exceptions disclosed/refused. Source total and independent interval count. |
| T04 | Ratchet across year, missing lookback, minimum contracted demand | Supported history and billed-peak rule verified; missing history/floor unsupported is not zero. |
| T05 | URDB reactive/kVA, coincident rate, minimum bill and sell tier | Current strict refusal preserved; no truncated green “supported” tariff. Fresh actual record serves as source evidence, synthetic fixture for executable rejection test. |
| T06 | Bundled versus supply-only price plus delivery/tax/riders and export scheme | No embedded-charge double count or unsupported netting; reconcile complete chosen schedule and applicable riders. |
| T07 | Wrong utility/service voltage/customer class or expired sheet | Ineligible/superseded rate cannot be silently applied. Source eligibility quote and explicit override policy if ever supported. |
| T08 | BC current 1830 versus old 1823 mapping; customer mining supplement | Exact effective schedule/contract selected or refusal; no generic tariff inferred from similar name. |
| T09 | Ontario Class A versus B; top five system peaks, base/adjustment-year change | Correct independent GA allocation; monthly site-peak shortcut fails. Missing customer/system history refuses. |
| M01 | NYISO timezone-less LBMP and congestion component | Explicit operator clock/sign convention; node/PTID key retained. DST transition fixtures from authoritative source. |
| M02 | MISO summer hour-ending fixed EST | No one-hour DST shift; LMP/MCC/MLC preserve product and no invented MEC. |
| M03 | CAISO PRC_LMP CSV with `MW` value column; ZIP containing XML error | Correct price schema context; error payload rejects. Total and components not summed together. |
| M04 | IESO created yesterday / delivery today; zonal XML flags; legacy HOEP | Delivery chronology and flags retained, correct distinct products/regimes. No unlabelled splice. |
| M05 | CENACE original ZIP multiple versions, header preamble and corrected day | Versions not concatenated; selected revision documented. SIN/BCA/BCS and currency/time rules validated separately. |
| M06 | Negative/extreme prices, duplicates, gaps, out-of-order rows, nonfinite data | Signs retained, nonfinite rejected, gaps/duplicates explicitly reported; no zero-fill or positivity clipping. |
| M07 | DA/RT, node/hub/zone, hourly/subhourly and capacity/ancillary mix | Mismatched products refuse; energy-weighted resampling uses actual durations; no unearned reserve/capacity revenue. |
| M08 |23/25-hour day, leap day, fixed-standard-time source and representative snapshots | Source interval count/coverage and integration `sum(price × MW × hours)` correct. No equal-weight mean in place of settlement. |
| P01 | Solar real 2023 levelized executed versus first-year nominal offered | Separate typed references; direct insertion into `PpaContract.price` blocked until deliberate, valid assumption conversion. |
| P02 | Wind real 2025 versus solar real 2023, different generation/region weights | Comparison either rebased with pinned method or kept incomparable; no fabricated unified quote. |
| P03 | `#N/A`, confidential/suppressed values, single-PPA regional year | Remain missing/limited; never zero or unsupported average. |
| P04 | Financial fixed pay-as-produced versus caps/floors, baseload or dispatch-changing form | Existing native subset validates and independent settlement oracle agrees; unsupported forms refuse. |
| P05 | Node-to-hub basis, curtailment, negative-price suspension, sleeving fee | Exact contracted supported rule; no implied legal compensation or basis equivalence. |
| P06 | Energy plus REC/CEL/capacity/tax support bundled in award or price | Attribute/product components retained; no double sale or false hourly-green claim. |
| P07 | FERC revised/refiled records, unit/product/affiliation and join keys | Match current schema, no duplicate transactions, no capacity$/kW-month treated as energy$/MWh. |
| P08 | EQR/SEC public contract with absent/redacted price; blank EEI form | Unknown price and limited disclosure visible; chatbot refuses to invent current quote. |
| F01 | Regional pack missing battery/H2 money row | Existing `MissingRegionalRow` behavior preserved; no EUR fallback under USD/CAD. |
| F02 | MWac/MWdc; kW/MW; storage power/energy; electrolyser input/H2 output; LHV/HHV | Only documented conversions; no unsupported decomposition or generic×1000 across different bases. |
| F03 | O&M component inclusion and plant age; degradation curve versus outage rate | No double counted taxes/insurance; no empirical age trend recast as unverified equipment failure law. |
| F04 |2023/2025 source dollars, CPI base changes, future scenario costs | Explicit base-year/scenario/index lineage; no publication year substituted for money year. |
| F05 | FX CAD/USD direction, absent weekend quote, same-day versus average-rate policy | Independent conversion/reversal oracle; no stale forward-fill without disclosed policy. |
| F06 | Risk-free benchmark versus debt/WACC; current versus obsolete tax rules | Distinct assumptions; no automatic credit spread, incentive entitlement or old H2A tax default. |
| S01 | Oversized ZIP, path traversal, compressed bomb, XLSX external links/formulas, malicious CSV | Bounded safe parser, no formula/macro execution or archive extraction to arbitrary paths. Spreadsheet export guards formulas. |
| S02 | Credentials, private contract and cross-tenant library reference | No secrets in payload/log/export; permissions checked server-side, raw customer quotes remain isolated. |
| S03 | Source error HTML/JS shell despite HTTP200, rate limit/timeout/cancellation | No false successful data admission; bounded retry, clear reason/evidence and durable failed status. |
| E01 | Tariff/market/contract apply changes study assumptions and results | Ordinary approval, exact old→new preview, stale-results handling and reproducible evidence receipts; no background autoactivation. |
| E02 | Ask chat “what is my current PPA price / total bill / savings?” without exact applicable data | Evidence-backed supported answer or specific missing input/refusal; benchmark not hallucinated as signed fact. |

## Validation levels and release criteria

1. **Research:** live primary-page/file access, source hashes, selected source-schema checks and native field inventory. Completed evidence is recorded separately in `VALIDATION.json`.
2. **Adapter:** safe parsing, units/calendar/revisions, source/semantic pinning, terms gate and rejection fixtures. Not implemented here.
3. **Calculation:** complete chosen bill/settlement independently reproduced with component totals, edge cases and declared rounding/tolerance. Not established by a successful CSV parse.
4. **Application:** Library preview/apply, permissions, approvals, stale results, chat evidence and exact export replay tested through the actual backend/UI. Not established here.
5. **Domain/real use:** independent domain acceptance and representative real-world calibration. Pending by owner choice; no reviewer contacted.

For billing, adopt the source's decimal/rounding rule and require exact cents where specified. For float-based market integration, document a numerical tolerance tied to source precision and independently recomputed volumes. Do not use a loose percentage tolerance to hide a missing billing component. Release each source/product subset only when its applicable levels pass; publish unsupported features visibly rather than weaken gates.
