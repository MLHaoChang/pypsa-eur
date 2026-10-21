# GridSpine research and implementation handoff

Status: published research snapshot; implementation and data ingestion are separate work.

This is the portable entry point for other agents, tools and implementation sessions. The materials were created with AI assistance and an independent AI review of the sector plan. They do not establish engineering certification, package execution, data reuse permission or production readiness.

## 1. Open-source capabilities and sector implementation

Start with [the sector handoff](../gridspine-open-source-research/SECTOR-HANDOFF.md), then:

- [Five modular analysis cards](../gridspine-open-source-research/FIVE-CARD-MODULARITY.md): Energy/capacity, Load flow & grid, Reliability, Commercial, Control strategies; per-example features, standalone providers, safe removal and migration.
- [Eight sector plans](../gridspine-open-source-research/SECTOR-DESIGN-PLAN.md): C&I, data centers, IPPs, green H2, ammonia, mining, microgrids and H2-to-products.
- [38 implementation slices](../gridspine-open-source-research/IMPLEMENTATION-BACKLOG.md) and [test/validation matrix](../gridspine-open-source-research/SECTOR-TEST-MATRIX.md).
- [Independent review and staged GO](../gridspine-open-source-research/INDEPENDENT-REVIEW.md) and [exact-component admission gates](../gridspine-open-source-research/ADAPTER-READINESS.md).
- [Original research](../gridspine-open-source-research/RESEARCH.md), [35-repository inventory](../gridspine-open-source-research/REPOSITORIES.md) and [four additional candidates/source findings](../gridspine-open-source-research/SECTOR-SOURCES.md).

The independent review binds seven files to their SHA-256 hashes in [review-candidate-hashes.json](../gridspine-open-source-research/review-candidate-hashes.json). Those document bytes are preserved unchanged. Older statements such as “local, not uploaded” describe their creation-time status; this index records their publication. Original `/workspace/` locations describe that session, not a required checkout path.

## 2. European and North American library data research

Start with [the library research index](../gridspine-library-research/README.md):

- [80 parameter families and native mappings](../gridspine-library-research/research/PARAMETER-INVENTORY.md).
- [109 source assessments in a structured catalog](../gridspine-library-research/source-catalog.json), covering technical components, CapEx/OpEx/lifecycle, outages/maintenance, prices/tariffs/PPA, profiles and economic/emissions references.
- [Proposed integration sequence](../gridspine-library-research/INTEGRATION-PLAN.md).

This newer research is a saved source-discovery package. Final cross-source review, complete field-to-source joins, current access/terms confirmation and actual adapter/data validation remain open. A source record is not an approved library value or permission to mirror a database.

## 3. Continuing safely

Read [AGENT-HANDOFF.md](AGENT-HANDOFF.md) before implementation. These documents preserve historical source baselines. The publication branch was prepared against master `21113ed57448c9b7a5b4c89391de25877db2de4a`; that does not re-certify every plan against all intervening implementation changes. Reconcile fresh master/open PRs before taking a backlog slice.

The PR publishes research and documentation only. It changes no native engine, application UI, permissions, source connector, populated production library or deployment.
