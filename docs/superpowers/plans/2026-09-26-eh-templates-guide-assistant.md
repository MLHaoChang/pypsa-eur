# Energy Hub — templates, walkthrough guide and assistant support (P19–P22)

**Status:** in progress on `claude/epic-allen-k2t1c4`.
**Requested:** 2026-09-26, after P10–P18 closed (see [`2026-09-25-eh-post-seal-implementation.md`](2026-09-25-eh-post-seal-implementation.md)).
**Working rules:** the same as the parent plan.
- TDD, red then green.
- One independent QA gate per phase.
- Honesty over completeness.
- Build on existing engines.

## What was left after P18

Every planned phase (P10–P18) is closed.

**Still deferred on purpose.** Each is recorded in the parent plan:
- per-frontier-point FMEA (spec §9);
- inline profile upload (P15);
- a bidirectional-import energy cap (P17 v1 refuses it);
- readiness for an explicit `dtc_config` (readiness derives configs from tags).

**Explicit non-goals.** These are not work items:
- climate-year packs;
- spare-lead severity;
- planned-outage MC;
- joint MILP;
- in-tree EMT;
- multi-area MC.

A cross-phase end-to-end review (P10–P18) runs alongside P19. Its verified findings are fixed and recorded here under **E2E review**.

---

## P19 — Energy Hub project templates

**Why:** a new user should not have to hand-parameterise imports, tags, outage data and stress scenarios before an EH study is possible.

**Templates.** One per archetype, each ready to run. Each is built by `project_templates/_build.py`, feasibility-checked at build time like the existing templates, and exported unsolved.

| id | Name | Recommended pack | What it demonstrates |
|---|---|---|---|
| `eh_datacenter` | Data Center Energy Hub | `weak_flexible` | Capped grid import with outage data. Critical IT on its own bus behind a transformer Link, next to non-critical cooling and offices. UPS battery, genset fleet with outage data, rooftop PV. An extendable genset/battery candidate set. Stress scenarios: heatwave and grid-weak week. |
| `eh_h2_hub` | Industrial Hydrogen Hub | `strong_grid` | Grid-connected hub with wind and PV. Electrolyser → H₂ store → fuel cell (the `eh_role` conversion roles). Critical process load and H₂ offtake. A multi-energy report. |
| `eh_microgrid` | Island Microgrid | `off_grid` | Islanded by the pack via a tagged tie Link. PV, wind, battery, diesel fleet with outage data. Critical hospital bus next to residential load. Stress scenario: dunkelflaute. |

**Contract.**
- The network carries the P14 tags: `eh_poc`, `eh_critical`, `eh_sk_mva`, `eh_ibr_mva` and `eh_role`.
- Occurrence data (`outage_rate_value`, `mttr_hours`) is set on thermal units and on the import Link where the archetype certifies with it.
- The weighting is a representative 168 h week, with snapshot weighting 8760/168 so `nyears ≈ 1`. This keeps MTTR ≤ the modelled hours (the P11 MTTR floor).
- A `template.json` sidecar declares the recommended archetype, pack overrides, stages and the ready-to-use stress scenarios.
- `POST /api/projects/from_template/<id>` also copies the allow-listed sidecars:
  - the stress-scenario registry;
  - template metadata, which the GUI reads to preselect the archetype and pack settings.
- The New Project wizard lists the templates. The chat `create_project_from_template` names them.

**Tests.**
- Each builder is feasible and tagged.
- Readiness on each template predicts that every default stage runs.
- `from_template` copies the sidecars.
- The wizard lists the templates.

**P19 status:** implemented. `tests/test_energy_hub_templates.py` has 13; FE adds 3 banner tests.

- [x] **Builders.**
  - `project_templates/eh_templates.py` holds the builders, and `_build.py` builds and exports them with the existing feasibility check.
  - Each template carries its tags and outage data:
    - datacenter: genset fleet, import, site transformer;
    - H₂ hub: import, electrolyser, fuel cell;
    - microgrid: diesel fleet, hospital feeder.
  - Each uses a 168 h week at weight 8760/168.
- [x] **Sidecars, committed and drift-pinned against the builder.**
  - `eh_template.json`: the recommended archetype, pack overrides, stages, `dtc_attribution`, study notes and provenance ("synthetic illustrative data").
  - `adequacy_stress_scenarios.json`: validated by `stress._validate`.
  - `solver_config.json`: VOLL 5000, because frontier and fmea_top need VOLL > 0.
- [x] **`from_template`.**
  - It copies the allow-listed sidecars only, and a stray file is not copied.
  - It loads the template's solver settings (through `_solver_config_from_dict`, so the pack-only fields stay stripped).
  - `GET /api/projects/{name}/eh_template` serves the metadata, or 204.
- [x] **Readiness.** It predicts that every default stage runs on all three templates with no warnings, and the MC boundary is OK.
- [x] **FE.**
  - The wizard lists the templates.
  - The EH panel shows a template banner with study notes and provenance.
  - "Use recommended settings" sets the archetype, pack form, stages and DtC attribution.
- [x] **Chat.** `create_project_from_template` enumerates every template id; this is pinned to the registry.

## P20 — Every EH feature on every template (e2e)

This runs live on each template through the real HTTP API (no stubbed driver):
1. readiness;
2. `eh_study` with the recommended pack and the full stage list, including frontier, mc_certify, fmea_top, redundancy, levers, dtc_stress (bus aggregate and `per_load`) and dtc_planning where the archetype allows it;
3. the report and sibling tables;
4. the Class-C FMEA sweep with the template's stress registry;
5. the JSON export.

Assertions:
- honest completeness;
- solves ≤ budget and equal to Σ charged;
- certification coherent with its verdict;
- the session network and cfg are untouched.

Findings are fixed test-first.

## P21 — In-app walkthrough guide (FMEA / Energy Hub)

**Why:** the new features have many inputs whose meaning is not obvious. Users need "what goes where, and why" at the point of use.

- **Single source.** One guide catalogue, `backend/data/guides/eh_fmea_guide.json`. It holds tours, and each tour has steps with a `target` (a `data-testid`), a title, a body and an optional "what to enter" line.
- **Serving.** `GET /api/guides/{topic}` serves it. The chat reads the same text (P22), so the tour and the assistant cannot drift.
- **FE `GuidedTour`.**
  - Step-by-step coach marks that highlight the target, show the text, and offer Back / Next / Skip.
  - Keyboard: Esc and arrow keys.
  - "Seen" state is kept per tour in `localStorage`, which is try/catch safe.
  - A step whose target is not on screen is skipped with a note, not crashed on.
- **Tours.**
  - "Energy Hub study": archetype, readiness, pack settings, DtC attribution, energy budget, run, report headline, completeness, pipeline, exports.
  - "FMEA worksheet": sweep, rows and badges, mitigability, class D, stress scenarios.
  - "Network tagging": Bus / Link EH sections.
- **Entry points.** A "Guide" button on the EH panel and on the FMEA tab. The Bus and Link cards get hover tips on every EH field (InfoTip), in the same wording as the catalogue.

**Tests.**
- The catalogue's targets all exist in the rendered components; this is pinned so a renamed test id breaks the build.
- The tour navigates, skips missing targets, and persists "seen".
- The route serves the catalogue.

## P22 — Assistant support: explain, analyse, recommend, apply

- **`get_feature_guide(topic)`** (read). The same catalogue as P21, so the assistant can explain any field.
- **`review_eh_study`** (read). It analyses the latest EH report, its sibling tables and readiness. It returns findings; each carries a severity, the evidence (the numbers it read), a recommendation, and an `action`.
- **`action`** names an existing tool and its exact arguments, for example:
  - `run_eh_study` with adjusted `pack_overrides`, budget or stages;
  - `update_component` to tag a bus `eh_critical`;
  - `put_stress_scenarios` to add a scenario.

  The assistant applies an action only when the user asks, through the normal confirmation card for write tools. It never applies anything silently.
- **Rules** cover at least:
  - `not_established` sections and their reasons;
  - a budget that is exhausted or tight;
  - an inconclusive or failing MC verdict;
  - frontier knee vs target;
  - a dominant fmea_top mode;
  - DtC critical unserved > 0, and a DtC priority caveat;
  - an ineffective lever or a lever above the pack cap;
  - an SCR warning;
  - an ENS target not met;
  - missing outage data;
  - a template's recommended next step.
- **Prompt.** A new guide part (the `_ADEQUACY_GUIDE` pattern) covers:
  - how to support the EH/FMEA workflow;
  - to call `review_eh_study` after a study;
  - to present findings with numbers;
  - to offer, never auto-apply;
  - to cite `get_feature_guide` for "what is this field".

**Tests.**
- The rules fire on constructed reports.
- Every `action` validates against the target tool's schema.
- Chat tool registration and the schema match.
- The prompt part is present.
