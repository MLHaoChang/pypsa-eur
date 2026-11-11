<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Library coverage: research-to-implementation specification

Status: proposed implementation backlog; no new dataset admitted or runtime implemented by this package.
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`, fetched again on 2026-10-25.

## Purpose and entry points

Turn each gap in the [assessment](baseline/assessment-2026-10-25.md) into a bounded research-and-build assignment. Start with [the queue](README.md), then the individual ticket. The [research protocol](research-protocol.md), [validation matrix](validation-matrix.md), [machine-readable plan](plan.json), and [handoff](HANDOFF.md) are part of every brief. Test IDs below describe **planned acceptance cases**, not executed tests.

The five modelling areas remain Capacity expansion, AC power flow & grid, Reliability, Commercial & finance, and Control. The current master design also retains Hand-off as a sixth card. These research groups are work packages, never additional cards, modes, or a parallel Library. Follow `.scratch/project-workflow/modular-cards.md` and current owner decisions; source research does not make a currently unavailable simulation sub-option runnable.

## Observed baseline and reuse

Master already has defaults packs, regional overrides, an explicit EU reference reader, lookup/compare/apply, approval cards and effect receipts, versioned org items/series, pins and bundles. Two partial TenneT tariff items deliberately refuse a complete total. These paths must be extended rather than rebuilt. `equipment`, `process_recipe`, `control_preset`, and `lifecycle_law` are not existing general Library item kinds. A CSV cannot create such a kind.

At this baseline the latest generic pack has 308 rows/96 technology-variant IDs, including equipment variants; 68 rows are illustrative. There are 81 campus equipment entries with 32 assumed CapEx values. EU reference data contains 24 technology/year records. These are historical inventory observations, not source-validation counts or future coverage targets. See `baseline/native-inventory.json`.

The previous assessment ran selected existing library tests: 379 passed, no failures/errors/skips. `baseline/validation-summary.json` preserves that scope. It is **not** validation of these proposed adapters, source values or future UI journeys. Do not sum historical or overlapping suite counts.

## Work sequencing and gates

1. **Audit:** ticket 01 creates one source-to-consumer register, rechecks master/PRs, and reconciles status. Ticket 02 owns the existing money-year issue as one coordinated change. Ticket 18 closes regional monetary coverage only after that basis is settled.
2. **First useful data:** tickets 04–07 select small equipment, storage and H2 references. Source/rights/field admission precedes copying data into a common pack. Missing OEM evidence remains unavailable. Ticket 04 consumes #712 only after it lands; it cannot claim #711 scene-generation runtime exists.
3. **Commercial pilots:** tickets 08/09 choose one complete EU/US tariff each. Unsupported clauses remain partial/refused. Ticket 10 is a separate conditional minute-boundary engine extension. Ticket 11 needs ticket 03's semantic manifest before monetary use. Ticket 12 refreshes benchmark evidence, never invents a contract quote.
4. **Reliability and demand:** tickets 13–16 separate service metrics, equipment failures, modelled profiles, process assumptions and control/event traces. A data reference cannot enable an absent dynamics engine.
5. **Workflow:** ticket 17 proves existing manual/AI journeys for each admitted collection. Ticket 19 audits existing live lookup and implements only a confirmed confined-fetch gap, with its own security gate.

Ticket dependencies in `plan.json` are prerequisites for implementation completion. Desk research may proceed earlier if it changes no runtime, copies no restricted source data and records unresolved fields. `ready-for-agent` denotes a bounded research assignment that can start; `needs-info` denotes source/decision prerequisites, and `needs-triage` denotes conditional scope requiring review. It does not mean the entire research group is approved for production use.

For code touching gridspine, finance/investment, adequacy or IC engines, obtain the repo's plan review before implementation. UI changes require the current UX review. No reviewer/coordinator contact is authorized by this document; the owner/coordinator routes those reviews. Source admission/rights decisions and independent domain review remain real gates, not assertions supplied by an implementing agent.

## Common data contract

Every selected resource has the fields in `research-protocol.md`: exact resource revision/hash, source row/cell, units and transformations, money year/currency/basis, technology/scale/test conditions, temporal semantics, permitted scope and a native consumer. Keep the raw licensed artifact outside Git unless redistribution is cleared; store permitted fixture extracts or a reproducible fetch recipe. Attribution for this specification is not a licence for the source dataset.

Preserve old pack versions, numerical hashes and saved-project pins. No silent project repin, new global default, EUR fallback for missing USD/CAD/GBP, zero replacement for missing data, or universal linear equipment scaling. Cost changes require an explicit case/region/basis selection and the existing reviewed native apply path.

Ticket 03 proposes a separate versioned semantic manifest alongside the numeric series pin. This must reconcile with the existing rule that descriptive provenance/verification/scope do not change value hashes. Hash only interpretation-critical semantic identity in the new manifest; confirmation state remains metadata. A unit, currency, market product, revision or interval-label change requires a new semantic version and explicit new binding. Legacy items retain their original byte/hash identity and are labelled interpretation unknown where needed. Do not silently rewrite storage or advertise legacy numerical hashes as financial-semantic proof.

## User experience and assistant behavior

Use the existing field editor/reference drawer, commercial Library, profile importer and completion ledger. Guided shows relevant choices; Expert exposes details with the same validation. Retain Inter, current tokens and density. Display source/version, applicability, missing fields and original/effective/projection/money years where relevant. Labels distinguish OEM-reported, observed, benchmark, modelled, synthetic and assumed. A user confirmation establishes acceptance, not independent measurement or engineering certification.

The companion may find compatible references, explain gaps, and propose field changes. Native code checks owner/project/org access, current component identity, native units and field compatibility, freshness and the exact approval digest. A source URL, model agreement or historical receipt is not authority to apply an edit. Preview is read-only and must not invalidate a solve. Writes use ordinary approvals, read-back and durable effect receipts. Cancellation/denial/stale scope apply nothing. A source update may propose an update; it never edits a project automatically. Keep edited project fields unless explicitly selected for replacement.

Checks must refuse numeric assertions when a total is partial, no compatible native consumer exists, basis is ambiguous, source terms are insufficient, a requested site differs from the reference cohort, or the data is unavailable. Fetched/uploaded content is untrusted data, not instructions. Live lookup remains off unless configured, exact-host confined, bounded, audited and disabled by the kill switch. Ticket 19 does not authorize live crawling or paid calls during this specification work.

## Output and rollout

One ticket or tightly scoped admitted collection per implementation PR, starting from fresh master with current file scope confirmed. Do not merge the documentation branches #491/#524 to obtain runtime dependencies. Preserve their source IDs and record aliases in ticket 01. Each PR adds a release fragment, tests first, exact input/fixture hashes, independent expected values and a coverage delta. Separate source research complete, parser complete, reference available, approved native apply and domain calibrated states. Report unsupported fields explicitly.

Start with read-only reference/preview, then reviewed apply only for demonstrated native consumers. Do not introduce a new item kind until a concrete editor/engine consumer, hashing/migration/export/ACL contract and separate schema review exist. Control/failure laws and private measurements may remain project/org scoped or research-only indefinitely. No full coverage/domain approval claim follows from passing software tests.

The final acceptance loop for each collection is source → parser → reference preview → explicit native use → pinned evidence → native result/bill oracle → export/reload. Applicable full CI, generated inventory/control-matrix checks when touched, and fresh premerge remain required. A path-filtered green job proves only its actual executed steps. Post-merge review/measurement remains separate.

## Boundaries

This package includes research briefs, implementation steps and planned tests only. It does not admit a new dataset, update defaults, implement tariffs, buy data, contact publishers, run paid models, enable live fetch, simulate a site or start a full 3D pipeline. Independent domain review and real-use calibration remain pending by the owner's choice. Research failure is an acceptable outcome when public evidence cannot support a field.
