# Reconciliation with master and parallel Library work

The requirements were extracted first at master `151fdc934d739bb790d0bd1237d85e7ac40e3719`. During source research, master advanced to `14d8c1a9bc8c073757928eacea551b1374d5a107`. No runtime changes are made on this research branch, and master was not merged into the branch merely because it moved.

Read-only inspection confirmed:

| Work | Current evidence / implication |
| --- | --- |
| [PR #499](https://github.com/MLHaoChang/pypsa-eur/pull/499) | Merged at `14d8c1a9bc8c073757928eacea551b1374d5a107`. Introduced `us_federal` and `ca_federal` alongside UK cost overlays. Reuse these, rather than recreate US/Canadian cost imports. |
| [PR #501](https://github.com/MLHaoChang/pypsa-eur/pull/501) | Merged at `4040537f4102edc618d4bed1428112e25942b84a`. EU research and narrow cost reference reader now exist on master; use that reference pattern where appropriate, not a separate PPA database. |
| [PR #491](https://github.com/MLHaoChang/pypsa-eur/pull/491) | Prior broader research remains draft at inspection. This package reconciles its 19 NAM commercial IDs in `previous-research-map.json`. It does not claim those research files are already merged. |
| PR #514 / #516 / #519 | Open reference listing, apply and reference-UI work at inspection. Re-read their final status before modifying the reference backend/frontend. Do not implement a duplicate drawer/apply path based on stale master. |
| PR #521 | Open common Library and TenneT tariff work at inspection. Reconcile final common-scope and permission behavior before admitting new common sources. |

Six of the seven native files in the extraction snapshot were unchanged at that master observation. `services/library/defaults_pack/loader.py` changed with regional overlays; the requirement bindings used here still exist. `master-reconciliation.json` records hashes and per-binding checks against that exact master.

The regional pack README documents **explicit selection** of `region`, no automatic geography inference, and `MissingRegionalRow` for absent regional money rows. Currency-less values may use disclosed EU fallback; regional finance defaults still inherit the EU base until separately sourced/cleared. These are important limits, not a claim that every project/UI route already selects the correct regional pack.

Existing source coverage is narrow:

- **US:** `cost_eia_reference_plants`, EIA AEO2025 reference engineering study, selected cases for utility PV, onshore wind and simple-cycle gas;2023 USD, EPC boundary without financing, including some generic connection costs that are not separable from the main table.
- **Canada:** `cost_canada_cer_ef2026`, Ontario/CM/2030 selected scenario rows;2025 CAD, connection costs excluded from overnight, specified other costs included in FOM.
- **Uncovered money rows:** battery power/energy decomposition, campus electrical equipment, diesel engine farm, electrolysis/fuel cell, ammonia/tanks and rooftop PV. A source's whole-system cost is not evidence for splitting those components.

The scoped owner clearance G2 names three existing technology-cost sources only. It does not clear all EIA-hosted content, new tariff PDFs, ICE, PPA workbook vendors or all public reports for common distribution. Preserve that boundary.

This comparison is a point-in-time source/design reconciliation, not a trial-merge or full application test. Refresh master, relevant PR states and repo instructions before implementation. Do not report premerge or all-CI success from this document.
