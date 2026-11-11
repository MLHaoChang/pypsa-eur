# Five modular analysis cards: implementation contract

Date: 2026-10-20 (Europe/Berlin). Status: revised proposal for independent review. This document supersedes the earlier three-group sector layout. It specifies behavior to implement; it is not an existing API or evidence that the runtime already supports it.

## 1. Owner direction and reconciliation with master

The five top-level analysis cards are **Energy / capacity expansion**, **Load flow & grid**, **Reliability**, **Commercial**, and **Control strategies**. Examples preselect individual features within them. IPP controls are off by default. A microgrid may explicitly select eligible grid-forming studies. Both cards and their subfeatures must be modular.

These cards select analysis capabilities; they are not five independent project databases or a replacement for every input form. Current master's project-flow design has ten canonical input/run/results sections and one home per field. Keep those field owners and Guided/Expert editors; expose only the inputs needed by selected capabilities, with links to their canonical editors. The latest owner instruction changes the top-level capability grouping where older specifications differ.

Master's new IPP spec uses Capacity/Grid/Control/Hand-off/Commercial group names. Do not map Hand-off to Reliability. Preserve handoff/export as an output action outside these five analysis cards; migrate actual reliability goals/studies from their real stored fields. This amendment must be reconciled with existing workspace implementation before editing shared enums or routes.

Project identity, permissions, assets, carriers, parameters, planning/calendar context and provenance remain shared context. They are not an unavoidable sixth analysis card or a reason to force capacity optimization. Users may analyze an imported fixed network or a saved plan without running Energy again.

## 2. Cards and candidate feature IDs

IDs below are proposed stable IDs to reconcile with native names in F0/F1. Display labels may change; persisted identity cannot depend on labels or array positions. One feature has one owner card; cross-card consumers reference its artifacts/inputs.

| Card ID | Feature IDs / scope | Preserved boundaries |
| --- | --- | --- |
| `energy` | `energy.capacity`, `energy.process_detail`, `energy.demand_baseline`, `energy.thermal`, `energy.lifecycle` | Capacity includes the dispatch and cost inputs needed by its objective. Lifecycle is an optional evaluation; retained physical degradation assumptions remain model inputs. |
| `grid` | `grid.load_flow`, `grid.sizing`, `grid.contingency`, `grid.compliance` | Separate the native LOPF active-power proxy, actual AC study, contingencies and precise supported compliance checks. No generic “all compliant” result. |
| `reliability` | `reliability.adequacy`, `reliability.critical_service`, `reliability.production`, `reliability.stress` | Adequacy, uninterrupted service, lost production and stress sensitivity have different methods/data. |
| `commercial` | `commercial.tariff`, `commercial.contracts`, `commercial.finance`, `commercial.merchant_risk` | Optional analysis/reporting does not silently remove prices/constraints already bound into dispatch. |
| `control` | `control.policy_replay`, `control.grid_forming`, `control.process_restart`, `control.dynamic_validation` | Native GFM screening first; policy replay, process restart and detailed dynamics are distinct features, with distinct admission. |

Selecting a card reveals its options and uses the example's recommended subfeatures as a proposal. It never selects every feature automatically. A card is selected when at least one subfeature is selected. A nonselected card can be added later; a removed card remains discoverable under “Add analysis.” Deployment administrators may retire definitions, separately from a user's project selection.

## 3. Example selections

**S** = recommended initial selection, subject to actual availability/configuration. **O** = optional, initially unchecked. **—** = initially unchecked, not a claim it can never be useful. Intent, availability and readiness are different: an unavailable recommended feature stays visibly unavailable and cannot execute or appear completed.

| Example | Energy | Load flow & grid | Reliability | Commercial | Control strategies |
| --- | --- | --- | --- | --- | --- |
| C&I | S capacity; O measured baseline, thermal/process, lifecycle | O sizing, load flow, contingency/compliance | O adequacy, critical service, production | S tariff, finance; O contracts | —; O policy replay for flexible equipment |
| Data center | S capacity; O lifecycle and calibrated cooling | S sizing and supported readiness checks; O detailed flow/contingency | S adequacy, critical-service/stress studies | S tariff, finance; O procurement | O policy replay/GFM where architecture and models support it |
| IPP / hybrid | S capacity; O lifecycle | **O, initially off**, matching the new native IPP example | O adequacy/capacity-value stress, initially off | S contracts, finance, merchant exposure; applicable POI tariff | **Off by default**, no hidden control job or compulsory dynamics |
| Green H2 | S capacity/process; O lifecycle/thermal detail | O connection sizing and supported studies | O supply/delivery/production risk | S tariff/contracts and product finance | O electrolyser operating-policy replay |
| Ammonia | S capacity/process detail | O sizing/flow and relevant process-grid checks | S production/stress intent, unavailable until admitted | S product economics, contract/finance | O process restart; GFM/dynamics not implied |
| Mining/concentrator | S capacity/process detail | S weak-grid sizing intent; O flow, contingency and supported motor checks | S production/stress intent, after prerequisites | S tariff/product economics/finance | O process restart/pickup; no automatic haulage or GFM |
| Microgrid | S capacity; O lifecycle | S island/network configuration and supported flow checks | S critical service/adequacy/stress | S cost/finance; tariff only if applicable connection | O policy replay; **explicit GFM opt-in** when eligible |
| H2-to-products | S one admitted product/process | O studies justified by actual network | O delivery/production risk | S product contract/finance | O product-specific policy/restart |

No example is a permanent entitlement or lock. Users can change every supported selection. Suggested values remain editable in Guided and Expert. One card per example family; planning dates are inside. “Select all applicable” previews the supported, permitted features and their impacts; it cannot make an unavailable worker or missing model runnable.

## 4. Registry and server admission

Reuse the native capability/result/harness registries by reference. Add only the analysis-selection metadata they do not already hold. The feature registry grants no tool authority.

Each versioned definition must declare:

- Stable `feature_id`, `card_id`, schema/version and native implementation reference; supported project/snapshot/period modes and assessed phenomena.
- Input/artifact contracts with units, basis, freshness and acceptable provider alternatives; outputs, metrics, result-view IDs and assurance dispositions.
- `requires_all` / `requires_any` input capabilities, exclusions and bounded feedback semantics. Requirements target data, not “Energy card must be selected.”
- Native job/tool/effect references, permission and approval policy, artifact ownership and worker/image pins; deployment availability and platform support.
- Fields read or changed, retained configuration namespace, invalidation dependencies, migration/retirement policy and numerical acceptance record.

Schema validation checks structure and enabled-state conditions; the backend resolver checks live availability, references, permissions, freshness and acyclic dependencies. JSON Schema `dependentRequired` tests property presence, not whether a boolean is true, so it is not a substitute for selection semantics. A model/client cannot provide a Python import path, arbitrary plugin, executable script or worker image to make a feature available.

Initial definitions are curated in the application. Optional package discovery can use standard installed metadata in an approved worker/build, but discovery is not approval to import/run code. No plugin marketplace, general plugin framework or runtime pip installation is needed for v1. One missing optional dependency must not crash core discovery or silently hide an active selection.

Expose separate state dimensions: **selected**, **available**, **configured**, **runnable**, and **result freshness**. Reuse existing section states (`complete`, `warning`, `incomplete`, `not_needed`) for field completion and existing claim labels for assurance. These dimensions are internal facts, not a request for new user-facing badge vocabulary. Reasons include missing input/provider, unsupported scope, conflicting selection, unavailable worker, lack of permission, stale source and unsupported claim.

### Input alternatives and standalone execution

| Selected study | Acceptable source, if compatible and fresh | Not required |
| --- | --- | --- |
| Grid | User-specified fixed network/operating point, imported validated snapshot, or exact native saved dispatch | A new capacity-expansion run |
| Reliability | Fixed asset/load/outage/inventory model, or a native candidate with an admitted operating-policy basis | Selecting Energy, Grid or Control merely to populate checkboxes |
| Commercial | Metered/imported energy flows or saved dispatch with tariffs/contracts and money basis | A new capacity or control study |
| Controls | Admitted plant/controller model and initial state/disturbance, with a saved operating point if needed | A new optimization; a static network alone does not supply a dynamic plant |
| Energy | Required physical constraints and objective coefficients from shared project inputs | Selecting Commercial just to supply costs, or Reliability just to preserve an active reliability constraint |

Each input slot resolves to one explicit provider pinned by ID/hash/version and compatible basis. Ambiguous providers require an explicit choice, not arbitrary precedence. Previously checked output proves only its own metric; it does not bypass a consumer's scope/model validation. No auto-fetch, auto-solve or hidden prerequisite enablement.

## 5. Selection changes, removal and historical data

Implement operations through existing project mutation and job machinery:

1. **Preview:** request desired features with expected project revision and registry digest. Server checks permissions and resolves inputs/dependents. Return additions/removals, missing data, changed objective/constraints, invalidated active results and any affected running jobs. No effects yet.
2. **Apply:** user accepts a concrete diff. Revalidate against the same revision/registry and apply atomically, with audit and idempotent effect receipt. A stale preview fails; it does not silently apply a new plan. Cascade removal of dependent features requires explicit selection in the diff; alternatives include keeping the prerequisite or rebinding a compatible saved artifact.
3. **Run:** preflight repeats live admission. Persist an immutable execution manifest containing selected feature versions, provider hashes, project revision, dependency order, workers, constraints/price basis and expected output dispositions. Run only its admitted jobs in topological order. Iterative coupling is an explicit bounded job, not an unnoticed dependency cycle.
4. **Display:** bind each result to its manifest. Changed dependencies make the active result stale. Historical results retain their original scope/source and can be read where existing historical contracts allow; they are never relabelled as a current run.

**Deselect/remove from project** means stop requesting the analysis and collapse its UI; preserve parameter values, library pins and immutable run history by default. Re-enable restores configuration only after migration, validation and fresh admission. It does not automatically restore freshness or start a job.

**Remove physical assets, clear parameter values or purge history** is a separate destructive action under existing authorization, retention and ownership rules. No card checkbox performs it. Card removal cannot bypass security/physical validation or silently weaken active reliability goals, connection limits, degradation, startup constraints or objective price bindings. Such changes need a named diff and explicit acceptance; declining keeps them in force.

In-flight jobs run against their immutable manifest. Applying a new selection can leave the old job as historical or explicitly cancel it through the normal contract; it cannot rewrite its inputs. A completed old result cannot overwrite a newer revision's active state. Worker retirement drains or explicitly cancels affected work before binaries are removed; urgent revocation follows the existing cancellation/kill-switch policy and records the outcome.

Unknown selected features or incompatible imported schemas are retained as unavailable records, never silently dropped or dynamically installed. View unrelated valid context and historical data; block only new execution paths that depend on unresolved active semantics. Project migration has explicit field/ID mapping, a rollback record and no model/constraint changes hidden by a label rename.

## 6. Critical cross-card rules

- **Commercial off:** keep costs, fuel/commodity prices, tariff bindings or merchant price inputs already used in the native objective. Post-solve reporting/settlement selection is independent. A deliberate change to optimization prices is an explicit parameter change with a new solve. Native IPP seller PPAs settle after dispatch in v1; do not claim contract-optimal plant sizing/dispatch from comparing those settlements.
- **Reliability off:** omitting a separate adequacy report does not remove an active ENS/LOLE/reserve constraint. If the user wants to remove the constraint, show that separately. If a goal cannot be enforced without a disabled capability, preflight refuses or the user explicitly changes the goal.
- **Grid off:** keep physical POI/import/export/network constraints in the planning model. Do not infer detailed grid compliance from not requesting the study.
- **Controls off:** keep necessary physical asset/controller presets and process startup constraints. Do not unmaterialize units. Omitting controller analysis does not authorize optimally flexible dispatch beyond asset limits.
- **Energy off:** imported/fixed capacity is valid context for other studies. No background capacity solve is smuggled in as an input provider.
- **GFM:** opt-in maps first to the native screened settings/phenomena and requires valid converter/plant, rating, headroom/SOC/reserve, network/island and disturbance assumptions. Unsupported items stay unknown/unavailable. python-control is not a generic GFM validator. Black start, protection coordination, reconnection/synchronism and detailed RMS/EMT models require distinct evidence and are not checked automatically.

## 7. Additional scientific and data contracts

Reliability records producing method/version, horizon/annualization, weather IDs/weights, outage dependence/common-cause assumptions, seed, sample count, stopping rule and confidence interval method, or an explicit unsupported disposition. Retain sufficient per-sample evidence for checked metrics. Compare policy/scenario effects using matched draws where appropriate; stochastic monotonicity is not established by one random sample. No point estimate implies customer uptime/capacity accreditation.

Battery ageing takes elapsed calendar time and an actual or explicitly repeated chronological duty cycle. Optimization snapshot weights and investment-period weights are not physical time series. Declare inter-period operating assumptions, temperatures, calendar/cycle ageing overlap, replacement resets and partial replacement boundaries; never age replaced cells through their predecessor's history. Unsupported chronology yields a named limitation rather than a verified lifecycle result.

Worker inputs/outputs/logs/caches are tenant/project scoped; private meter/process data cannot be shared through a public cache or library promotion. Stage least-necessary fields with access checks and bounded retention; downloads and evidence artifacts recheck ownership. Record dataset rights separately from package licensing. Export manifests preserve provenance without leaking another tenant's files or secrets.

## 8. Release and review gate

All 32 selections of the current five cards must receive a supported plan or a precise refusal under defined provider fixtures; this is not a claim every selection is always runnable. Add per-example defaults, pairwise feature interactions and the named high-risk multi-feature combinations, removal/migration and direct API bypass tests. Extend when definitions change.

Every shipped adapter/version/supported platform needs a real pinned-worker execution, numerical reference and native round-trip in its claimed scope. Every newly enabled family needs actual enabled-path end-to-end evidence. Optional-dependency skips do not certify a module. Full details are in SECTOR-TEST-MATRIX.md and ADAPTER-READINESS.md.

A design-review go authorizes confidence in this staged plan, not a claim of implemented or validated engines. Begin with modular foundations and bounded feasibility. Later adapters remain conditional until their build and availability gates close; domain/customer calibration stays separate.
