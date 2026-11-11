<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Library gap implementation queue

This package expands the 2026-10-25 library assessment into **19 bounded research/implementation tickets and 95 planned acceptance cases**. It adds no data or runtime. [Start with the spec](spec.md), [source protocol](research-protocol.md), [validation matrix](validation-matrix.md), and [handoff](HANDOFF.md). The [baseline assessment](baseline/assessment-2026-10-25.md) explains the evidence; [plan.json](plan.json) is the dependency/case index.

First assignment: **LG-01 only**. It can run now as a read-only audit and documentation reconciliation. Other tickets have specific dependency, source-admission, engine/UX/schema or security gates; a candidate source is not approved for default/common import. Independent desk research may prepare without changing runtime or admitting data. The owner/coordinator assigns reviewers and implementation; this backlog does not contact them.

## Queue

| Ticket | Assessment group | Priority | Bounded deliverable | Completion depends on | Triage state |
|---|---|---|---|---|---|
| [LG-01](issues/01-source-consumer-register.md) | R0 | P0 | Reconcile the source-to-consumer register and shipped state | — | ready-for-agent |
| [LG-02](issues/02-money-year-and-pins.md) | R0 | P0 | Complete the coordinated money-year and template/study/regional repin | LG-01 | needs-info |
| [LG-03](issues/03-semantic-source-manifest.md) | R0 | P1 | Bind interpretation-critical source semantics without rewriting numerical pins | LG-01 | needs-info |
| [LG-04](issues/04-oem-equipment-and-footprints.md) | R1 | P1 | Add a small model-identified equipment and footprint reference subset | LG-01 | needs-info |
| [LG-05](issues/05-grid-technical-reference-gaps.md) | R1 | P1 | Replace supported grid-equipment assumptions and resolve the H2 voltage-pair gap | LG-01 | needs-info |
| [LG-06](issues/06-storage-lifecycle-costs.md) | R2 | P1 | Admit bounded storage cost and lifecycle references | LG-01, LG-02 | needs-info |
| [LG-07](issues/07-hydrogen-lifecycle-boundaries.md) | R2 | P1 | Expand hydrogen technical, O&M and replacement references | LG-01, LG-02 | needs-info |
| [LG-08](issues/08-eu-complete-tariff-pilot.md) | R3 | P1 | Add one complete representable European tariff pilot | LG-01, LG-03 | needs-info |
| [LG-09](issues/09-nam-complete-tariff-pilot.md) | R3 | P1 | Add one complete US C&I tariff and stage Canadian/Mexican coverage | LG-01, LG-03 | needs-info |
| [LG-10](issues/10-minute-boundary-tariff-extension.md) | R3 | P2 | Conditionally support minute-aligned tariff bands | LG-01, LG-03 | needs-triage |
| [LG-11](issues/11-historical-market-price-pilots.md) | R3 | P1 | Admit exact EU and US historical wholesale-price products | LG-01, LG-03 | needs-info |
| [LG-12](issues/12-ppa-and-renewable-benchmarks.md) | R4 | P2 | Refresh renewable-cost/PPA benchmark editions and comparability | LG-01, LG-03 | needs-info |
| [LG-13](issues/13-reliability-and-maintenance-evidence.md) | R5 | P2 | Separate supply-quality references from component outage/maintenance data | LG-01 | needs-info |
| [LG-14](issues/14-representative-demand-profiles.md) | R5 | P2 | Add one bounded calibrated-model C&I demand profile set | LG-01 | needs-info |
| [LG-15](issues/15-sector-process-reference-pilots.md) | R5 | P2 | Map mining, ammonia, hydrogen and DC process data to existing example consumers | LG-01, LG-07 | needs-info |
| [LG-16](issues/16-rapid-load-control-evidence.md) | R5 | P2 | Define the event/control evidence admission and adapter gate | LG-01 | needs-info |
| [LG-17](issues/17-guided-expert-copilot-journeys.md) | Shared | P1 | Prove bounded manual and AI library use across the existing workflow | LG-01, LG-03 | needs-info |
| [LG-18](issues/18-regional-monetary-coverage.md) | R0/R2 | P1 | Expand local-currency cost/O&M coverage without EUR substitution | LG-01, LG-02 | needs-info |
| [LG-19](issues/19-confined-official-source-lookup.md) | Shared | P2 | Audit and close only missing official-source lookup controls | LG-01, LG-03 | needs-triage |

## Delivery order

- **Wave 0:** LG-01 register, LG-02 coordinated money basis, LG-03 semantic identity; LG-18 then extends regional values.
- **Wave 1:** LG-04/05 equipment and technical gaps, LG-06/07 storage/H2, LG-08/09 complete tariff pilots, LG-11 price pilots. Each collection is a separate admitted subset/PR.
- **Wave 2:** LG-12 benchmark editions, LG-13 service/outage evidence, LG-14 demand and LG-15 processes; LG-16 starts with an event/control evidence decision only.
- **Cross-cutting:** LG-17 validates each admitted collection in the existing Guided/Expert/AI workflow. LG-10 minute bands and LG-19 live lookup are conditional gap-closing changes, not automatic engine/network expansion.

These waves describe priority, not permission to bypass dependencies or source/engine reviews. Cost, electrical correctness, reliability calibration and security each retain their own acceptance gate. Public evidence may justify a NO GO instead of a default.

Skipped here: source ingestion, runtime implementation, live/paid calls, reviewer contact and merge. Source rights, field-level suitability and independent domain/real-use calibration remain outstanding.
