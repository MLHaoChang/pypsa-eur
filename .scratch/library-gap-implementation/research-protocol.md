<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Research and admission protocol

## Resource record

For each actual candidate, create a machine-readable source record (a ticket output, not a new runtime database):

| Group | Required information |
|---|---|
| Identity | Stable existing source ID or alias; publisher; exact resource URL and edition; publication/retrieval dates; SHA256 of inspected bytes; archived resource location if permitted. Landing-page inspection alone leaves resource hash/fields unverified. |
| Rights | Exact resource licence/terms and evidence location; permitted common/org/local scope; redistribution/derivative/API limits; attribution; third-party exclusions; existing clearance reference. Unknown rights = no common admission. Keep `automatic_import_approved=false` unless separately authorized. |
| Extraction | Exact workbook sheet/cell/row or JSON/CSV key; original value/unit; parser version; transformations and rounding; formulas/cached-value semantics. Do not execute macros, workbook links or downloaded instructions. |
| Economics | Currency; original and target money years; nominal/real; projection/COD/contract vintage; scale; input/output, AC/DC, power/energy basis; fixed/variable O&M; included/excluded EPC, tax, replacement and connection costs. Unknown fields stay unknown. |
| Technical | Model/revision; region; ratings/frequency/base; test/operating point; ambient/installation; chemistry; duration; pressure and LHV/HHV; envelope/clearance/land distinctions. |
| Temporal | Timezone; interval start/end convention and duration; DST handling; DA/RT/product/node; components; source finality/revision. Whole-second series storage cannot accept millisecond events. |
| Applicability | Observed/OEM-reported/benchmark/modelled/synthetic/assumed; cohort and exposure; uncertainty method; limitations; known missing fields. An assumed range is not a statistical quantile. |
| Use | Existing native module and exact field; read-only versus apply; conversion refusal; pinned version; consumer test and independent expected value; source-admission decision/evidence. |

Do not conflate publication, code licence, dataset licence and customer permission. Re-check exact new editions even where an earlier edition was cleared. PNNL and Enedis are user-scoped in the current register; do not promote them to common by inference. Existing owner decisions in `docs/agents/owner-decisions.md` and `.scratch/gridspine-library-research/terms/` outrank these candidate lists.

## Bounded investigation

1. Read the ticket, current AGENTS/steward/owner rules and the relevant source-to-consumer register. Recheck actual master and related PR heads. Record the baseline SHA.
2. List the native fields that the selected example actually consumes. Establish a prioritized, finite gap list before searching. Rank by example usage, materiality, uncertainty and realistic rights/availability.
3. Inspect official primary resources; public repositories can identify methods or parsers but do not establish data rights. Compare exact resource releases, avoid bulk downloads and record inaccessible/429 resources. Use permitted official alternatives without bypassing access controls. No publisher contact or paid data/model call.
4. Select the smallest useful sample, with exact model/customer class/region/year. Return GO for a specified read/reference/apply scope, NEEDS INFO with named missing evidence, or NO GO with the reason and safe fallback. A recommendation is not independent domain approval.
5. Submit the source/admission record, field map, licence evidence, bounded fixture/extraction recipe and independent oracle for review. No source-value guess or fallback import.
6. Once admitted and any engine/UI plan gates are met, write the planned red tests, extend existing native paths, run focused regressions and publish one bounded PR. Follow release-note/CI/premerge rules. Never loosen tests to make a dataset fit.

## Required ticket completion record

Record source/revision hashes and allowed scope; inspected master and PR heads; research decision and unresolved cells; changed files; red-before-green test evidence; local commands/results; exact source head and remote CI evidence; actual review verdicts; deployment/ingestion state; next ticket and remaining limitations. Never label a planned validation as passed. Put durable outputs in Git in the feature's own directory. Large/restricted raw files stay outside Git with a reproducible permitted retrieval recipe.

## Independent oracles

Expected results must be calculated from original source facts or a separately reviewed small fixture, not by calling the production parser/engine to generate its own expected output. Decimal arithmetic is suitable for bills/unit conversions; explicit timestamp tables for DST; dimensional arithmetic for equipment; hand cost/replacement cashflows for lifecycle; exhaustive small-state probabilities for outage screening. Document precision and tolerance from units/stored precision. Preserve exact index/identity/NaN/missing-field semantics.
