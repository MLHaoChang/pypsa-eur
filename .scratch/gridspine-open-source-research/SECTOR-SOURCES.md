# Additional primary-source observations for sector planning

Retrieved 2026-10-20. This extends the earlier 35-repository research by four candidates. Sources were read, not installed or run. Cached raw files, metadata/SHAs and trees: `evidence/sector-extension/`.

Revision: independently reviewed against the owner's five modular cards. Exact-component H2Integrate source findings are in ADAPTER-READINESS.md and the review; a repository recommendation is not blanket component admission.

## New candidates

| Repository | Inspected default-branch SHA | License observation | Decision |
| --- | --- | --- | --- |
| [H2Integrate](https://github.com/NatLabRockies/H2Integrate) | `17a25b7a7876a15c2def3d4d81c680b46577bb25` | BSD-3-Clause LICENSE | First molecular/component feasibility candidate; broad dependencies, package marked beta; selected model/reference only |
| [HOPP](https://github.com/NatLabRockies/HOPP) | `7d1905ea2b1db17600eeb04be02d7a59e365540a` | BSD-3-Clause LICENSE | Hybrid performance benchmark if needed; overlaps native planning |
| [OpenMines](https://github.com/370025263/openmines) | `acd77e23de2c3b743485ec87cabdbbf7779ceb3e` | MIT LICENSE | Truck-dispatch simulator; optional later schedule/duty reference, not generic mine energy engine |
| [WaterTAP](https://github.com/watertap-org/watertap) | `5909a60409d25944f73d5b9293abf3a6adf21def` | Actual LICENSE has BSD-style conditions plus enhancement-contribution provision; GitHub says NOASSERTION | Conditional treatment/desalination models; review exact solver/platform/license dependencies before adoption |

### H2Integrate: concrete fit and limits

- [Overview](https://h2integrate.readthedocs.io/en/latest/intro.html): hybrid engineering/technoeconomic component framework including hydrogen, ammonia and other products. This supports a component-adapter feasibility study; it does not validate a GridSpine integration.
- [Pinned packaging](https://github.com/NatLabRockies/H2Integrate/blob/17a25b7a7876a15c2def3d4d81c680b46577bb25/pyproject.toml): Python >=3.11,<3.14, broad OpenMDAO/PySAM/Pyomo and other dependencies; use an optional worker first.
- [Ammonia models](https://github.com/NatLabRockies/H2Integrate/blob/17a25b7a7876a15c2def3d4d81c680b46577bb25/docs/technology_models/ammonia.md): simple model and stream-table synloop approach; ammonia and purge streams need precise boundary mapping.
- [Synloop constraints](https://h2integrate.readthedocs.io/en/latest/technology_models/ammonia_synloop_dynamics.html): configurable turndown, ramps and startup losses. They default permissively unless configured. They approximate operating behavior; do not infer first-principles reactor validation. Documentation states zero-production startup hours consume no feedstocks, which needs checking against the customer's startup/standby boundary.
- [Iron mine model](https://h2integrate.readthedocs.io/en/latest/technology_models/iron_mine.html): simple/NRRI iron-pellet models, not general mining. The inspected documentation cites regional/legacy data and an AI-assisted electrical breakdown. Treat as an illustrative reference until independently validated for a site. Do not reuse as an authoritative generic mining default.

### Other fit checks

- [HOPP documentation](https://hopp.readthedocs.io/en/v3.4.2/api/hybrid_simulation.html): plant performance/dispatch functionality overlaps the native core; reference comparison before production integration.
- [OpenMines README](https://github.com/370025263/openmines/blob/acd77e23de2c3b743485ec87cabdbbf7779ceb3e/README.md): models mine truck dispatch and events. An energy/charging conversion still needs a valid model and dataset.
- [WaterTAP setup](https://watertap.readthedocs.io/en/stable/getting_started.html): separate environment and solver setup recommended; platform support varies. Current documentation is not a guarantee of native Python/environment compatibility.

Other candidates, licensing/dependency issues and native code links remain in [RESEARCH.md](RESEARCH.md) and [REPOSITORIES.md](REPOSITORIES.md). Source-reading and presence of upstream tests are not test execution or model validation.

## Native update observations

Read GitHub master `51ddf8b0bf11f5375b30c0bf84825ff411ae5d45`; fetched without switching active HEAD. Reviewed the diff from `c7be951` and new library/checked-results designs. Queried pull418 directly: merged=true, merged_at `2026-10-20T17:21:26Z`, merge SHA `0171f1d61cba6c76f3d22ce7ffe72572eaa9f23e`. Local baseline source includes the ammonia tracer, process schema and relevant native tests. No native tests were run in this planning task.

Relevant native references:

- [Ammonia source](https://github.com/MLHaoChang/pypsa-eur/blob/51ddf8b0bf11f5375b30c0bf84825ff411ae5d45/pypsa-gui/backend/project_templates/eh_ammonia.py)
- [Existing grid-to-process backlog](https://github.com/MLHaoChang/pypsa-eur/tree/51ddf8b0bf11f5375b30c0bf84825ff411ae5d45/.scratch/grid-to-process/issues)
- [Parameter library and confined lookup design](https://github.com/MLHaoChang/pypsa-eur/blob/51ddf8b0bf11f5375b30c0bf84825ff411ae5d45/.scratch/project-workflow/library-and-live-lookup.md)
- [Checked-results audit](https://github.com/MLHaoChang/pypsa-eur/blob/51ddf8b0bf11f5375b30c0bf84825ff411ae5d45/.scratch/checked-results/audit.md)

## Five-card modularity research and newer master

Fresh master for this amendment: `90c7b8ffcec104397383361212da0aa21f545f1b`. Diff from prior `51ddf8b` is docs-only IPP PR453. Read its [IPP spec](https://github.com/MLHaoChang/pypsa-eur/blob/90c7b8ffcec104397383361212da0aa21f545f1b/.scratch/ipp-renewables-bess/spec.md), including actual missing PPA-share/site-sold settlement/price-series/revenue features, full-hourly example, grid/control defaults and post-solve seller-PPA limitation. It is a spec, not implemented-template proof. Its earlier Hand-off grouping is reconciled with the owner's latest five-card instruction, not automatically renamed Reliability.

Read native `services/result_views.py`, process-unit schema and existing project-flow design: result registry is data-only and grants no execution authority; field ownership and section completion differ from optional analysis selection. The proposed feature registry references these native services instead of duplicating authority/storage.

Primary engineering references supporting the design choices:

- [PyPA plugin discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/) and [entry-point specification](https://packaging.python.org/en/latest/specifications/entry-points/): installed package metadata can identify extensions. This supports optional future discovery, not arbitrary project-supplied code execution. No new plugin framework is required for curated v1 adapters.
- [JSON Schema conditional validation](https://json-schema.org/understanding-json-schema/reference/conditionals): structural conditional schemas complement server admission. `dependentRequired` operates on property presence, so it alone does not express “selected=true” or permissions/runtime readiness.
- [PyPSA 1.1.2 Link documentation](https://docs.pypsa.org/v1.1.2/user-guide/components/links/): directed conversion ports and fixed-ratio multi-input/output semantics support retaining native process planning while external models provide admitted envelopes. Native restrictions still govern; upstream documentation does not override them.
- [pandapower 3.1.2 time-series loop](https://pandapower.readthedocs.io/en/v3.1.2/timeseries/timeseries_loop.html): time-step controller loops support time-series electrical studies, but do not establish detailed transient/GFM compliance. Retain separate capability scopes.

Additional pinned source concerns independently checked by author and reviewer: simple ammonia ignores hourly H2 values for production, PEM water output has a declared-unit/source-conversion mismatch to reconcile, and controller clipping may leave other outputs inconsistent. See [the exact-component gate register](ADAPTER-READINESS.md) for pinned code links and fix-or-refuse tests. These are source observations, not numerical reproductions; no affected component is admitted as validated.
