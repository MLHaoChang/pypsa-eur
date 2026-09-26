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

**P20 status:** done.
- `tests/test_energy_hub_templates_e2e.py` runs live over HTTP on all three templates: readiness → full 10-stage study → report and export → Class-C sweep. It asserts:
  - every stage `run`;
  - Σ charged = consumed ≤ budget;
  - honest sections;
  - `certified` consistent with the verdict;
  - the session network untouched by both the study and the sweep.
- Findings fixed:
  - **Data center.** The 40 MW connection is the physical rating, so the worksheet and the study see one system. The PoC carries SCR inputs, and the gate now reads `ok`, SCR 8.3.
  - **H₂ hub.** Levers were `not_established` because there was no StorageUnit, and islanded DtC planning was infeasible. It now has a site battery, an extendable fuel cell and wider candidates.
  - **Microgrid.** The Class-C sweep ran on the tie-connected system (severity 0). The tie is now normally open.
  - **DtC notes.** "No DtC (planning) contingency solved" now names each contingency's condition and, for infeasible ones, the likely fix.
- The honest outcomes on the templates are:
  - data center: MC `fail` (LOLE 12.4 h/yr vs 3). Grid-import unavailability alone exceeds the target, which is P22's showcase for recommendations.
  - H₂ hub: LOLE reported, not certified; strong_grid has no target.
  - microgrid: `inconclusive`.

## E2E review (P10–P18, cross-phase)

The verdict was **GO WITH CONDITIONS**, with 0 blockers. Every invariant held across 14 live HTTP study runs, and so did abort, the foreground solve after a study, and the chat path. Each finding is fixed test-first in `tests/test_energy_hub_e2e_review.py` plus FE tests:
- **M1.** A `profiles` scenario keyed on Loads/Generators not on the network was skipped silently. It ran unstressed with severity 0, reported as evaluated.
  - The sweep now returns `profiles_incomplete` with a note naming the unmatched components.
  - The pack picker labels each pack with its length, the names it swaps and "test fixture".
- **M2.** The default `import_cap` ladder's 0 MW rung failed preflight (`p_nom = 0`) and was charged as a solve.
  - 0 MW is now a "no import" rung (`p_*_pu → 0`, `p_nom` kept), so every default rung solves.
  - The "ineffective" check reads the pre-lever state.
  - A `validation_failed` refusal is not charged.
- **m1.** A stage exception was recorded as `aborted`. It is now `skipped` for a config refusal (any `ValueError`) and `failed` for an error. `aborted` stays reserved for the stop event.
- **m2.** The panel's LOLE target on strong_grid gave a 422 (factory metric `none`). A target now also sends `certification_metric: "mc_lole"`.
- **m3.** Readiness did not preview what runs. `GET /eh_readiness` now takes `pack_overrides` (JSON, validated like the study), and the panel sends its stages and overrides, debounced.
- **m4.** The FMEA tab silently swept class-B only when the registry was unreadable. It now refuses with the reason.
- **m5.**
  - The panel gains lever toggles (pack / on / off per archetype), including the P17 `import_energy` lever.
  - Chat `get_adequacy_results` reads `eh_redundancy`, `eh_levers`, `eh_dtc` and `eh_dtc_planning`, and the route inventory lists them.

## P21 — In-app walkthrough guide (FMEA / Energy Hub)

**Why:** the new features have many inputs whose meaning is not obvious. Users need "what goes where, and why" at the point of use.

- **Single source.** One guide catalogue, `backend/data/guides/eh_fmea_guide.json`. It holds tours, and each tour has steps with a `target` (a `data-testid`), a title, a body and an optional "what to enter" line.
- **Serving.** `GET /api/guides/{topic}` serves it. The chat reads the same text (P22), so the tour and the assistant cannot drift.
- **FE `GuidedTour`.**
  - Step-by-step coach marks that highlight the target, show the text, and offer Back / Next / Skip.
  - Keyboard: Esc and arrow keys.
  - "Seen" state is kept per tour in `localStorage`, which is try/catch safe.
  - A step whose target is not on screen first tries its `reveal` control, then is shown centred with a note. Optional steps are skipped. It never crashes.
- **Tours.**
  - "Energy Hub study": archetype, readiness, pack settings, DtC attribution, energy budget, run, report headline, completeness, pipeline, exports.
  - "FMEA worksheet": sweep, rows and badges, mitigability, class D, stress scenarios.
  - "Network tagging": Bus / Link EH sections.
- **Entry points.** A "Guide" button on the EH panel and on the FMEA tab. The Bus and Link cards get hover tips on every EH field (InfoTip), in the same wording as the catalogue.

**Tests.**
- The catalogue's targets all exist in the rendered components; this is pinned so a renamed test id breaks the build.
- The tour navigates, skips missing targets, and persists "seen".
- The route serves the catalogue.

**P21 status:** implemented. There are backend tests (`tests/test_guides.py`, 5) and FE tests (`GuidedTour.test.tsx`, 4, plus 2 panel tests). FE suite: 2027 passed.

- [x] **Catalogue.** `backend/data/guides/eh_fmea_guide.json` holds three tours:
  - `eh_study`, 13 steps: pack, template, readiness, settings, targets, DtC, levers, stages, run, completeness, verdict, pipeline, export;
  - `fmea`, 6 steps: sweep, ranking, mitigability, class D, stress scenarios, adding one;
  - `eh_tagging`, 2 steps: bus tags, Link role.

  It also has a `fields` help map for every EH tag, pack override, stage, status, failure class and stress field.
- [x] **Serving.**
  - `services/guides.py` validates and caches the catalogue.
  - `GET /api/guides` and `/api/guides/{topic}` serve it from an allow-list, 404 otherwise.
  - The route inventory is updated.
  - The spec ships `data/guides`, and `check_bundle.ROOTED` requires it beside `alembic.ini`.
- [x] **Pins.**
  - Every tour target and reveal is a `data-testid` a component really renders.
  - Every EH tag column and the key pack overrides have help text.
  - A malformed catalogue is refused.
- [x] **FE.**
  - `components/GuidedTour.tsx`:
    - highlight ring and popover with a "What to enter" line;
    - Back / Next / Skip, plus Esc and the arrow keys;
    - reveals then re-checks a hidden target; skips optional steps; shows a missing target with a note;
    - "seen" kept in `localStorage`, try/catch safe, with a dot until finished.
  - Guide buttons: EH panel ("Guide", "How to tag the network") and FMEA tab ("Guide").
  - New tour anchors: `fmea-sweep`, `fmea-table`, `fmea-expert-form`, `eh-bus-fields`, `eh-link-role`.
  - Bus/Link EH hover tips read the catalogue wording (`useGuideField`), with the old text as an offline fallback.

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

**P22 status:** implemented. `tests/test_energy_hub_review.py` has 12, including a live loop on the data-center template. FE adds 2 tests: the composer seed and the panel button.

- [x] **`services/adequacy/eh_review.py`.** It reviews the stored report plus the study record and emits findings sorted by severity (high / medium / low / info).
  - Each finding has evidence numbers quoted from the report, a recommendation, and `actions` (tool + exact args) only where the arguments are fully determined.
  - Rules:
    - certification fail (a tighter ENS re-plan with redundancy and storage levers), inconclusive (more MC draws), no target (certify at 3 h/yr);
    - ENS missed;
    - not_established by reason: budget → a bigger budget; VOLL → `update_solver_config`; missing tags, SCR data, outage data or an infeasible islanded plan → a recommendation only;
    - frontier knee;
    - dominant fmea_top mode (price redundancy);
    - DtC critical unserved (run dtc_planning), the DtC priority caveat and the planning build;
    - the cheapest lever option and ineffective options;
    - an SCR warning;
    - a tight budget;
    - DSR opted in but unused.
  - Re-run actions keep the user's previous request: stages, budget, overrides and attribution.
- [x] **Chat tools.**
  - `review_eh_study` (read). It falls back to the study record's copy when a later solve cleared the stored report.
  - `get_feature_guide` (read). It is the P21 catalogue: index, tour or field.
  - `get_eh_template` (read).
  - `put_stress_scenarios` (write, confirmation card).
  - The endpoint map and safety tiers are pinned.
- [x] **Prompt.** A new `_EH_GUIDE` part.
  - FACTS, shown without tools: the vocabulary, "not_established ≠ zero", the certification rule, "templates are synthetic".
  - CHAINING: explain via `get_feature_guide`; pass template settings unchanged; after a study call `review_eh_study`; present findings with numbers; OFFER actions and never auto-apply; run exactly the agreed action, then review again.
- [x] **Actions are valid by construction.** Every emitted action validates against its tool's schema. For `run_eh_study` it also passes the study request's own validation (`EhStudyRequest`, `apply_pack_overrides`, `McOptions`, the attribution resolver, stages).
- [x] **Live loop (data-center template).**
  1. chat `run_eh_study` → poll → `review_eh_study` gives `certification_fail` (LOLE > target).
  2. Its action is applied verbatim.
  3. `review_eh_study` again: the new ENS target is in force and LOLE did not get worse.
- [x] **FE.**
  - An "Ask the assistant (to review)" button on the EH panel opens the assistant with the request **prefilled but not sent** (`chatStore.composerSeed`), so the user stays the one who sends and confirms.

## QA gate — P19–P22

**GO WITH BINDING CONDITIONS**, now closed. The reviewer checked and found sound:
- the template numbers and tags;
- the sidecar allow-list, with pack-only fields stripped;
- the guide text against the code (stage order, the CI rule, top-5 fmea_top, 5 % priority, IBR default);
- `put_stress_scenarios` at write tier;
- the "offer, never apply" prompt;
- the fresh-checkout build of the EH templates (on-demand, commit `4eeae01`).

Each item was fixed test-first.

1. **BINDING — the headline recommendation was a no-op.**
   - On the data center, the plan already serves all demand (achieved ENS 0 ‱). "Tighten the ENS target" therefore changed nothing: LOLE stayed 12.38 h/yr and cost and sizing were identical. The live test only asserted `<=`.
   - `certification_fail` now tells the two cases apart:
     - **Energy-limited** (achieved ≥ ½ cap): keep the tighter-ENS re-plan.
     - **Outage-driven:** say so and list the firm-capacity options for the user to choose, including N+1, a candidate `p_nom_min`, outage-duration storage and a lower dominant outage rate. The action sizes islanded operation (adds `dtc_stress` + `dtc_planning`).
   - The live loop now asserts the applied action yields NEW evidence: the DtC section goes from absent to `ok`.
   - A reserve-margin action was probed and rejected, because preflight refuses it on this network.
2. **BINDING — re-runs dropped `mc`, `dsr_buses` and `dtc_config`.** The study record now stores the whole request. `_Rerun` carries these fields, and `mc` merges (keeping the seed when draws are raised).
3. **BINDING — every `ValueError` counted as a refusal.**
   - `_stage_exception` now treats only the engines' own refusal classes as `skipped`: lever, redundancy, DtC stress / planning / config, pack and hub boundary.
   - Anything else is `failed`. This is pinned with a numpy broadcast error.
4. **BINDING — no hover help on the main EH form.**
   - Every pack control carries an `InfoTip` with the catalogue wording: archetype, ENS / LOLE targets, import cap / energy, budget, MC draws / seed, DSR buses, DtC attribution, levers and stages.
   - `mc_seed` was added to the catalogue.
   - A test pins every key the panel uses to the catalogue.
5. **Non-binding, fixed.**
   - **Tour behaviour.** It ignores keys typed in form fields, focuses Next, and announces the step body (`aria-live`). Post-run steps are marked `after_run`: "appears after a study has run".
   - **fmea_top recommendation.** The redundancy wording is qualified: it prices GENERIC N+1 options, indicatively, not a spare for the named Link.
   - **Levers.** `import_cap` rungs above the connection's current rating are flagged `exceeds_pack_cap` and excluded from "cheapest option".
   - **Template project defaults.** A template project preselects its recommended settings once, if the form is untouched, so "open the panel, press Run" runs the right pack.
   - **Chat draft.** A composer seed no longer overwrites an unsent draft.
   - **Wording.** The data-center "weak" comment is corrected: the connection is capacity-weak, and its SCR of 8.3 passes.
6. **Non-binding, recorded rather than changed.**
   - **Sizing.** The sizing section counts `grid_supply` (the wholesale source beyond the PoC). Sizing is P6 behaviour across every network; restricting it to the hub side belongs with the sizing engine, not this phase.
   - **Template review rule.** The "template's recommended next step" rule in `review_eh_study` is not wired. The template's notes reach the user through `get_eh_template` and the panel banner instead.
   - **Chat context in tests.** The chat context is not a product bug: chat turns run under the request's bound context. The live P22 loop uses `install_network`, because direct tool calls in tests fall back to the default context.
