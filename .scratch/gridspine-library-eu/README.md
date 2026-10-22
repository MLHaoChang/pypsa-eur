# GridSpine: focused European library research and integration

**Result:** 44 parameter groups mapped against current native support; 36 focused source assessments with primary-source observations; an optional native Library reader containing 24 source-backed technology/year references. This extends the earlier [PR #491](https://github.com/MLHaoChang/pypsa-eur/pull/491) research. Counts overlap that catalog and must not be added together.

## What is actually integrated

The existing Library now has a **backend reference reader** for Danish representative onshore wind, fixed-tilt utility PV, commercial/industrial rooftop PV, 100 MW AEC and 100 MW PEM systems. It uses native `PackValue` / `CostPart`, preserves source cells, hashes and money years, and requires an explicit technology/year choice. No defaults or projects are changed. **UI/chat selection and approved application are not wired in this package.**

See [native reference documentation](../../pypsa-gui/backend/services/library/reference_data/README.md) and [implementation contract](IMPLEMENTATION.md). The two directly inspected DEA workbooks explicitly state CC BY 4.0. Other sources have their own rights/admission findings.

## What the research recommends next

| Priority | Addition | Benefit and main boundary |
| --- | --- | --- |
| 1 | Use the optional cost/performance references through the existing find/review/apply flow | Additional projection years and commercial PV/AEC/PEM distinctions; preserve money year and AC/input basis |
| 2 | Danish DataHub energy-tariff pilot | Structured official GLN/code/date/hour records; source terms, DKK, VAT and interval rules still need admission |
| 3 | Selected final DE/NL network schedules | Separate energy, capacity, peak, metering and connection components; upstream TSO cost cannot be counted twice |
| 4 | FR/ES/IT and then AT/PT tariff subsets | Useful industrial coverage; calendars, excess/reactive terms and eligibility need explicit support and bill oracles |
| 5 | Rights-cleared PPA/CfD archetypes and official benchmarks | Native contract fields already exist; documents, auction awards and current negotiated prices are different products |
| 6 | Process/O&M/degradation depth | New H2 Observatory workbooks and DEA/PVPS references help; customer maintenance/OEM data remains necessary |

This is EU-first research, with deeper selected-resource work for DE/NL/FR/ES/IT/DK and official references for AT/PT. It is **not complete operational tariff coverage for all 27 EU countries**. Finland and other unavailable discovery leads remain labelled; non-EU European comparators are not relabelled EU.

## Read the package

1. [Parameters first](01-PARAMETERS.md) — 44 groups, existing family IDs and native boundaries.
2. [Source register](02-SOURCES.md) — individual research and prior-catalog reconciliation.
3. [DSO/TSO tariff findings](03-TARIFFS.md).
4. [PPAs, CfDs, swaps and commercial data](04-COMMERCIAL.md).
5. [Technology costs, H2, O&M and degradation](05-TECHNOLOGY-OM.md).
6. [Integration sequence and validation matrix](06-INTEGRATION-TESTS.md).
7. [Continuation handoff](HANDOFF.md) and [validation evidence](VALIDATION.json).

Machine-readable artifacts: [source-register.json](source-register.json), [parameter-map.json](parameter-map.json), [fetch-observations.json](fetch-observations.json). They retain failed access and rights uncertainty. Raw copyrighted pages/workbooks and restrictive contract files are not mirrored. The small attributed CC BY reference-cell subset is the only new runtime dataset.
