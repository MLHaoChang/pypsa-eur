# NAM commercial and public-data library research

**Start with the [overview](../../docs/research/gridspine-nam-commercial-overview.md).** NAM here means US, Canada and Mexico. Checked 21 October 2026.

For the concrete implementation backlog, read [the six Library additions plan](06-LIBRARY-ADDITIONS-PLAN.md): tariffs, historical market prices, PPA benchmarks, reported contracts, finance references and technical/O&M references. The first release targets one complete tariff, one CAISO product and one benchmark collection after shared admission checks.

The user requested extraction of the tool's requirements first, then deep public-data research, particularly tariffs, wholesale prices and PPA contract prices. The authored requirements predate source selection. This package extends the prior library research, reconciles its 19 NAM commercial IDs, and records changes already merged into master during research. It introduces no runtime connector, data seed or new calculation engine.

| File | Purpose |
| --- | --- |
| [01-DATA-REQUIREMENTS.md](01-DATA-REQUIREMENTS.md) / [data-requirements.json](data-requirements.json) | 48 requirements and actual native class/field boundaries |
| [native-model-snapshot.json](native-model-snapshot.json) | AST field/line inventory and source hashes at extraction baseline |
| [02-SOURCE-CATALOGUE.md](02-SOURCE-CATALOGUE.md) / [source-register.json](source-register.json) | 49 source families, intended uses and remaining gates |
| [03-COUNTRY-FINDINGS.md](03-COUNTRY-FINDINGS.md) | US/Canada/Mexico tariff and price findings |
| [04-CONTRACTS-COSTS.md](04-CONTRACTS-COSTS.md) | PPA, swap, cost, maintenance and finance distinctions |
| [source-map.json](source-map.json) | All 48 requirements assessed, including unsupported/private gaps |
| [previous-research-map.json](previous-research-map.json) | One-to-one reconciliation of previous 19 source IDs |
| [fetch-observations.json](fetch-observations.json) | Exact requested/final URLs, sizes, hashes and failures for 140 observations |
| [sample-inspection.json](sample-inspection.json) / [inspect_samples.py](inspect_samples.py) | Seven bounded offline schema inspections; raw records not published |
| [workbook-inspection.json](workbook-inspection.json) / [inspect_workbooks.py](inspect_workbooks.py) | Selected workbook metadata/cell evidence, cached values only |
| [05-INTEGRATION-VALIDATION.md](05-INTEGRATION-VALIDATION.md) | Prioritized implementation sessions and numerical/admission test matrix |
| [06-LIBRARY-ADDITIONS-PLAN.md](06-LIBRARY-ADDITIONS-PLAN.md) / [library-additions-plan.json](library-additions-plan.json) | Six prioritized collections, dependencies, bounded deliverables and acceptance-test IDs |
| [MASTER-RECONCILIATION.md](MASTER-RECONCILIATION.md) | Baseline versus incoming regional cost/reference work |
| [HANDOFF.md](HANDOFF.md) / [VALIDATION.json](VALIDATION.json) | Next actions, portable verification and limits of this research |

No raw third-party workbook, market CSV, tariff PDF, secret or account token is included. SHA256 evidence identifies bytes actually retrieved but is not a licence. A source's website terms are evidence for admission review, not a legal opinion about all facts in every linked filing.
