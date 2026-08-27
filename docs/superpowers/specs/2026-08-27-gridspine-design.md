# gridspine: planning-to-stability grid pipeline — design

Date: 2026-08-27. Owner: Hao (Hitachi Energy Power Consulting). Status: approved design, pre-implementation.

Source context: design discussion of 27 Aug 2026 (`grid_pipeline_handoff.md`, two mermaid diagrams). This spec supersedes the handoff brief where they differ.

## Purpose

Internal consulting accelerator chaining open-source engines (PyPSA, pandapower, ANDES) upstream of commercial dynamics tools (PowerFactory / PSS/E). Own IP: canonical schema, snapshot ranking, handoff contract, dynamic parameter templates, assumptions ledger, reporting, compliance rules (later). Engines stay off-the-shelf.

Two study variants over one shared spine:

1. **Planning → dynamics**: PyPSA capacity expansion + UC/ED → static analysis → snapshot ranking → handoff → dynamics → report.
2. **Connection study**: ingest host grid + connection request → static screening → grid-strength screen (later) → handoff → compliance (later) → report. PyPSA optional (dispatch-source choice).

From the user's perspective this is **one tool**: open the app, pick a study type, run the corresponding pipeline. Engine boundaries are invisible.

## Decisions locked

| # | Decision | Choice |
|---|----------|--------|
| 1 | Variant order | Shared spine first; both variants are thin drivers over it |
| 2 | Packaging | New top-level package `gridspine/` in the pypsa-eur repo; no Snakemake coupling in phase 1 |
| 3 | Load-flow network | **Option A**: detailed grid (CGMES/.raw/pandapower) is canonical; PyPSA projects onto it via `region_map` |
| 4 | PyPSA resolution | Nodal preferred (`region_map` = identity; PyPSA bus name == .raw bus name holds literally). Clustered supported later via commitment-aware disaggregation — off critical path |
| 5 | Validation oracle | PowerFactory, manual runs; results dropped as CSV fixtures. <1% match gate on IEEE 39-bus |
| 6 | UI | Existing pypsa-gui is the only front door; `gridspine` stays headless |
| 7 | Copilot | Chat agent gets parity across all study types via a shared action layer |

Defaults adopted for the brief's remaining opens: target grid scale / dynamics fidelity deferred until a real client grid arrives (phase 1 is 39-bus nodal RMS; EMT-only-if-flagged handles fidelity escalation). PowSyBl deferred behind an internal MPL licence check; ingest stage designed engine-agnostic so it can slot in.

## Architecture — Option A, spine of file contracts

Canonical element IDs are the **detailed grid's**. Everything downstream of stage 1 (pandapower, ranking, .raw export, PowerFactory) speaks detailed IDs only and never sees a PyPSA object.

The stage-1 → stage-2 contract is a **dispatch table keyed by detailed-grid unit ID** (`unit_id, hour, p_mw, q_mvar, status ∈ {0,1}`), not a PyPSA network. PyPSA is therefore a *producer plugin*, not a foundation — which is exactly what the connection-study variant needs (client-supplied snapshots are a second producer emitting the same table).

`region_map` (detailed_bus → pypsa_bus) is a data file consumed at the producer boundary. Nodal runs use the identity map. The nodal path dissolves the UC/inertia trap: unit on/off status and ΣH·S come straight from the solve — no pro-rata allocation destroying commitment state. (Pro-rata disaggregation of region MW reads "all units online" and corrupts min-inertia ranking; the clustered producer, when built, must dispatch actual units in merit order and carry per-unit status. Its allocation rule is a ledgered assumption.)

Nodal costs, accepted: UC MILP solve time at scale (mitigations: rolling horizon, expansion-clustered + UC-nodal, zonal UC with nodal pocket); a detailed-grid → PyPSA converter (mechanical; opposite direction to `pypsa_to_pandapower`); per-unit data burden (same data the .dyr and ledger need anyway).

## Package layout

```
gridspine/
  schema/          # canonical contract: network IDs, dispatch table, snapshot record
    network.py     #   detailed-grid element registry, stable-ID rules
    dispatch.py    #   dispatch table definition
    contracts.py   #   dataclasses + validators for every stage boundary
  ingest/          # stage 0: CGMES/.raw/pandapower → canonical  (+ region_map loader)
  producers/       # stage 1: things that emit a dispatch table
    pypsa_nodal.py #   solved PyPSA network → dispatch table (identity map)
    external.py    #   client-supplied snapshots (CSV/Excel) → same table
    # pypsa_clustered.py deferred — commitment-aware disaggregation
  static/          # stage 2: pandapower AC LF, N-1 full / N-2 LODF-pruned (lightsim2grid),
                   #          IEC 60909, SCR pre-check
  ranking/         # stage 3: severity metrics → top-k snapshot selection
  handoff/         # stage 4: raw_writer (v33), dyr_writer, contingencies.csv, ledger README
  templates/       # dynamic parameter YAML library + assumptions ledger logic
  readback/        # stage 6: PowerFactory/PSS-E result files → report figures
  drivers/
    planning.py    # variant 1 chain
    connection.py  # variant 2 chain
tests/gridspine/   # unit + 39-bus vertical-slice fixtures (incl. PowerFactory CSVs)
```

Spine rules:

- **Every arrow in the pipeline diagram is a file; every file has a validator in `schema/contracts.py`.** Stages communicate only via these artifacts; no stage imports another stage's internals. This is what makes manual-PowerFactory-today → API-exporter-later a one-module swap.
- Engine imports are caged: `pypsa` only inside `producers/`; `pandapower` only inside `ingest/`, `static/`, `handoff/`. Ranking and (later) the compliance rule engine see plain dataframes keyed by canonical IDs — solver independence by construction.
- Dynamic parameter templates are data (YAML), typed per generator class and connection code, every value tagged `measured / datasheet / assumed`. The ledger is the report appendix.
- `gridspine` lives beside `scripts/` and `pypsa-gui/`, touches neither. Pixi env gains `pandapower` + `lightsim2grid`.

## One tool: UI integration

`gridspine` has no UI code and no HTTP. The pypsa-gui backend is the only front door; `drivers/` is the only surface it calls.

User flow: open app → new study → pick type ([1] capacity expansion (existing flow, unchanged), [2] planning→dynamics, [3] connection study) → type-specific input form → run → per-stage progress → results, ranked snapshots, handoff bundle download, report. In [3], dispatch source is a dropdown; one option ("generate with PyPSA") quietly runs [1].

- Each driver: one job-shaped signature `run(study_config) → StudyResult` with per-stage progress callbacks. The backend wraps drivers in the **existing solve queue** — same status/abort machinery, no second job system.
- File-based stage boundaries give per-stage UI status and resume-from-last-artifact for free.
- Handoff bundle surfaces as a download; PowerFactory import stays manual; result read-back is a file upload in the same study view. The later API exporter replaces download+upload with a button; nothing else moves.
- Phase 1 is buildable and testable headless via a `drivers/` CLI. GUI wiring is a thin, late, path-limited backend change — deliberately the last increment (pypsa-gui is under active concurrent development).

## Copilot parity

The chatbot is a peer of the UI, not a feature of study type [1].

- **One action layer**: every study operation is a backend function first (`create_study`, `set_dispatch_source`, `run_pipeline`, `get_stage_status`, `list_ranked_snapshots`, `edit_template_param`, `get_assumption_ledger`, `fetch_result_figure`, `export_handoff_bundle`, …). UI endpoints and chat tools are both thin wrappers — parity is structural, not maintained by discipline.
- Chat tools register into the existing copilot registry, grouped by study type; the agent gets the toolset matching the open study. Every delegation keyword-bound (repo convention after the arg-shape drift audit).
- Chat edits carry ledger provenance `chat-edited`; UI edits `user-edited`. The ledger audits both interfaces for free.
- Chat-triggered runs go through the same solve queue (status, abort included).
- Sequencing: action layer + UI wiring first; chat tool registration is the increment immediately after (same functions — cost is registration + descriptions).

## Validation, testing, error handling

**Vertical-slice oracle (weeks 1–4)**: pandapower `case39` fixture (ANDES `ieee39` cross-check). Chain: PyPSA nodal dispatch → dispatch table → pandapower AC LF → .raw v33 → manual PowerFactory import → exported PowerFactory CSVs land in `tests/gridspine/fixtures/powerfactory/`. Gates:

- Bus voltages/angles + branch flows match PowerFactory **<1%**, asserted per-element, not aggregate.
- `.raw` round-trip: re-import via pandapower's own parser must reproduce the network (catches writer bugs without a licence seat).

**Per-boundary tests**: every contract validator gets tests including at least one deliberately-broken artifact that must be **rejected** — and mutate the fixture to prove the guard fires (a passing negative-guard test proves nothing otherwise).

**TDD throughout**, converters especially: expected pandapower net / .raw text for a 3-bus toy written before the code.

**Error handling**: stage failure = typed error artifact in the study directory (stage, element IDs, cause); UI and chatbot render the same artifact; runs resume from the last valid artifact. Non-convergent LF and infeasible UC are *results*, not crashes: recorded per-snapshot, treated by ranking as maximal severity, run continues.

## Phasing

1. **Weeks 1–4**: `schema/` + contracts; 39-bus vertical slice: PyPSA nodal dispatch → pandapower PF → .raw export → manual PowerFactory validation (<1% gate).
2. **Weeks 5–8**: snapshot selection over 8760 h dispatch; ranking metrics (min inertia, max IBR share, N-1 severity, peak load, max import).
3. **Weeks 9–12**: N-1/N-2 screening across snapshots (LODF prune + lightsim2grid AC verify); handoff bundle complete (.dyr, contingencies, ledger README); action layer; GUI wiring; chat tools.
4. **Later**: PowerFactory API exporter + read-back; grid-strength & interaction screen (SCR/WSCR/ESCR, impedance screen, RoCoF flag → EMT); compliance rule engine (YAML rules, VDE-AR-N 411x/412x/413x, RfG); mitigation sizing; batch orchestration; clustered producer (commitment-aware disaggregation); PowSyBl ingest (after MPL policy check).

## Deferred / revisit triggers

- Clustered producer: build when a study exceeds nodal-UC solve capacity.
- PowSyBl: build when CGMES ingest is needed AND the MPL check passes.
- Grid-strength screen + compliance engine: connection-study phase 2; both consume only canonical dataframes, so nothing in the spine changes.
- Target scale / dynamics fidelity: decide at first real client grid.
