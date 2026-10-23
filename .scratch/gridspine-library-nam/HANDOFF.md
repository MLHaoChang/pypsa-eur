# Handoff: North American commercial library

Status: research package, no runtime source admitted. Date 21 October 2026. Start with [overview](../../docs/research/gridspine-nam-commercial-overview.md), then [requirements](01-DATA-REQUIREMENTS.md) and [implementation/test matrix](05-INTEGRATION-VALIDATION.md).

## Decisions and scope

- NAM means US, Canada and Mexico. Extract requirements before choosing sources:48 native requirements;49 source families assessed against them.
- Build on native Library items, market series, pins, reference readers, cost packs, ordinary approvals and evidence exports. No second source database and no blanket new engine.
- Tariff completeness, market product/calendar, actual-versus-benchmark, currency/money year and rights are admission gates. Parser success is not a correct bill or a signed PPA.
- Keep independent domain review and real-use calibration pending. No paid calls, reviewer contact, account registration, deployment or production activation was performed.
- The five requested study areas remain modular; source availability does not force a user to enable every area. Preserve current frontend card contracts if they have since evolved.

## Recommended next session

Use [the six-collection implementation backlog](06-LIBRARY-ADDITIONS-PLAN.md) and [its structured records](library-additions-plan.json) to choose a bounded PR. LIB-01/02/03 form the proposed first release; none is already implemented by this research package.

Take **NAM-0**, then one bounded **NAM-1 tariff** or **NAM-2 price series** implementation. First read current `AGENTS.md`, steward instructions and relevant harness/Library contracts. Refresh master and current reference/common-library PRs. Do not rewrite another agent's branches or merge this research without applicable repo checks.

1. Compare current native fields and money paths with `native-model-snapshot.json`, `master-reconciliation.json` and `source-map.json`.
2. Audit numerical and semantic currency/product pins, intended commercial rights and current source-policy enforcement. Do not assume that existing unit metadata is absent; it exists in `SeriesMeta`.
3. Choose an actual supported tariff/customer class or one supported market product. Obtain an exact current source and independent oracle; do not pick the reactive-charge SDG&E record as a complete supported tariff.
4. Specify narrow files and failing semantic tests before code. Preserve all current unsupported-field refusals. Run current targeted regression suites, then repo-required checks/premerge and domain review where applicable.
5. Update this research's claims only for newly verified facts. Keep “benchmark”, “candidate” and “unknown” labels until the applicable gate genuinely passes.

## Most useful evidence

- `source-register.json`: intended use, rights, remaining gate and observation references.
- `source-map.json`: all 48 requirement IDs, candidate source IDs and gaps.
- `fetch-observations.json`:140 retrieval attempts, including failures. URLs and hashes identify downloaded evidence; no licence is implied.
- `sample-inspection.json`: seven bounded source-schema inspections: NYISO, MISO, CAISO, IESO nodal/zonal, CENACE original history and Bank of Canada FX.
- `workbook-inspection.json`: selected cached metadata from actual solar 2025/wind 2026 workbooks. Third-party offered indices/certificate prices remain segregated.
- `previous-research-map.json`: reconciles all 19 earlier NAM IDs from draft PR #491. Never sum earlier and current counts as independent coverage.
- `VALIDATION.json`: exactly what was checked and what was not.

## Reproduce research checks

Portable structural checks, from repository root:

```bash
python .scratch/gridspine-library-nam/validate_research.py
git diff --check
```

The sample scripts are offline and require local research evidence, deliberately **not** committed. They have no HTTP client and do not load data into the app:

```bash
python .scratch/gridspine-library-nam/inspect_samples.py /path/to/private/cache
python .scratch/gridspine-library-nam/inspect_workbooks.py /path/to/private/cache
```

Each raw file must be named `<observation_id>.raw` and match the corresponding SHA256 in `fetch-observations.json`. Obtain exact source files only under the then-current access/terms policy; do not assume another session retains this cache. Workbook inspection additionally needs `openpyxl`; it reads cached values in read-only mode and retains no external links. Do not install it as a new application dependency solely for these research checks.

Current session cache: `/workspace/scratch/nam-commercial-research` (ephemeral, not part of handoff correctness). Current research worktree: `/workspace/gridspine-nam-research`. Private raw files must remain outside Git. No process credentials are needed to interpret the authored package.

## Explicit unresolved items

Complete current industrial schedules and customer eligibility; full reactive/minimum/coincident bill support; exact source reuse/export rights; independent numerical bill/settlement oracles; end-to-end currency semantics; current EQR quarter/schema; fresh corrected CENACE and accessible CFE schedule; actual Banxico/INEGI series; private PPA/forward/connection/off-take/credit quotes; asset-level maintenance/failure calibration; production domain approval.

Current source restrictions must not be sidestepped with wrappers or tenant-scoped caching. If intended use is not cleared, keep a metadata/link reference or authorized user document path and identify the remaining gate. Do not invent a freely available live forward/PPA feed.
