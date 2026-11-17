<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Expert mode on one parameter catalogue: spec

Status: draft for the owner (EXPERT-CAT-1). Builders start after the owner has seen it as a page and answered the open questions (§9).
Base: master `882d7114`. Paths are under `pypsa-gui/` unless they start with `.scratch/` or `docs/`.
Owner decisions applied: EXPERT-CAT-1, RAW-NAMES-1, WIKI-1, EXPERT-GUIDED-1, HOLISTIC-1, DENSITY-1, UI-COHESION-1, E2E-TEMPLATES-1.

## 0. Goal, in the owner's words

> "make sure that the expert mode user experience is also properly tested and redesigned so that all the necessary parameters can be edited and set by the user in a way that they are properly grouped, easy to find (maybe some kind of search bar would be good to find them or through the chat agent) and if needed user can go into all the details needed for the parametration, a kind of wiki would be good to explain different meaning of the parameters. In addition, let's avoid using any direct parameter or variable names from the tools but paraphrase them with new names for the user interface. Also let's make sure the canvas of the different maps such als SLD layouts, map view, 3d view are also updated to reflect the same artistic layout. Note especially that different functions such als capacity exapansion and load flow may need to add different or additional assets into the canvas and overall system design that these assets and parameters can be integrated easily is key."

> "the redesigned expert mode should be consistent with the guided mode so any changes should reflect in both modes." (EXPERT-GUIDED-1)

One sentence: **every asset type and every parameter is declared once, in a backend-served Parameter catalogue; Guided, Expert, the three canvases, search, the help drawer, the chat assistant and reports all read that one declaration, so adding an asset or a parameter is one entry, not a change in eight places.**

## 1. Words used here

These go into `pypsa-gui/CONTEXT.md` with slice 01.

- **Parameter catalogue** (code: `catalogue` inside `services/asset_schema/`): the one declaration of every asset type and every user-settable parameter, served at `GET /api/catalogue`. _Avoid_: schema (that is the route shape), field labels, tool catalogue (that is `harness/catalogue.py`), Library (that holds values, not declarations).
- **Entry**: one parameter in the catalogue. It has a stable **entry id** (`generator.size`), a **plain name** ("Capacity") and a **model name** (`p_nom`, the raw engine column; shown only on the "In model files" line).
- **Asset type**: one kind of thing a user places (PV array, grid transformer, battery, candidate line). Several asset types map onto one PyPSA Component class (Component stays the backend word, CONTEXT.md:44).
- **Function**: what a study does with the model: Dispatch, Capacity expansion, Load flow, Short circuit, Grid compliance, Adequacy, Finance. A function needs some entries and ignores others.
- **Depth**: how much of an asset's catalogue a view shows: *Key* (Guided card, ≤3 decisions, design.md §3.5), *Basic*, *Advanced*, *Full detail*.
- **Function lens**: a filter that shows only the assets and entries one function reads (§4.4).
- **Parameter guide**: the in-app help drawer generated from the catalogue (WIKI-1). One **guide entry** per catalogue entry, asset type, group and function.
- **Assumed**: a value the user has not supplied, filled from a Generic default (CONTEXT.md:515). It is marked in every view and listed in the Assumptions ledger.

## 2. What exists today (audit)

| Concern | Today | Problem |
|---|---|---|
| Parameter declarations | `backend/services/asset_schema/fields.py`: `FieldSpec` (:42) with key, group, label, help, units, scale, kind, options, required, `key_param`, derived, `reveal_when`, part, blank. 11 classes, ~133 fields (`_BY_CLASS` :670). Served per class at `GET /api/network/asset_schema/{component}` (`routers/network.py:776`). | Good seed. No stable id independent of the raw column, no depth beyond `key_param`, no ranges, no functions, no synonyms, no wiki text, no default source. Project-level settings (site, connection, goal, optimisation, finance) are not in it at all. |
| Bus extras | `models/energy_hub.py:37` `EH_CUSTOM_COLUMNS` (`eh_sk_mva`, `eh_poc`, ...) | Declared a second time, with its own kinds, outside `FieldSpec`. |
| Asset types | `frontend/src/utils/assetTypes.ts` `TYPES` (~24 entries: id, label, colour hex, icon, match rules) | Frontend-only: chat and reports cannot read it; hard-coded hex. |
| Labels | `utils/fieldLabels.ts` `FIELD_LABELS` (~35 keys, hand-kept) + `ledgerFieldLabels()` overlay from template ledgers; 154 `*_LABELS` / `*_WORDS` maps in `src/` | Guided and Expert can name the same field differently; a rename has no single place. |
| Help | `help` strings in `fields.py`, shown as tooltips; chat reads `attrs_doc.py` (generated) and `component_discovery.py` | No long-form explanation, no search, chat and UI explain differently. |
| Search | `components/CommandPalette.tsx` (⌘K / ⌘P, `App.tsx:644`) finds commands and projects | Finds no parameter. |
| Raw names | `pages/CapacityBoundsEditor.tsx:170` shows `p_nom_extendable=True`; `PropertiesPanel.tsx:263` "MC" | `uiCopyGuard.test.ts` covers 20 surfaces (:17-37), checks ticket ids only, has an empty `ALLOW` (:64), and has no raw-name rule. |
| Panels | `layout/PropertiesPanel.tsx` (2183 lines) calls `useAssetSchema` per class and hides fields per type by hand (`hidden=` at :1044); `layout/properties/AssetFields.tsx` renders groups | Per-type code paths; no tiers; no function filter. |
| Canvases | SLD `pages/TopologyCanvas.tsx` (React Flow, 3700 lines, if/else on category at ~8 places, `AssetIcon` :470, `CARRIER_BUS_COLORS` :183 hex); map `pages/MapCanvas.tsx` (react-leaflet divIcons, `CATEGORY_STYLE` ~:134); 3D `pages/SiteCanvas.tsx` + `site3d/assetLibrary.ts` (`APPEARANCE` keyed by asset-type id), `templates.ts`, `heroes.ts` | Adding one asset type touches up to 9 files (assetTypes, assetTypeIcon, paletteIcons, assetLibrary, templates, heroes, TopologyCanvas `AssetIcon`, category records in two canvases). Only `CARRIER_BUS_COLORS` is pinned to tokens (`charts/chartThemeTokens.test.ts`). |
| Functions | No single enum. Fragments: `run_evidence.py:813` (`lopf`, `ac_pf`), `ADEQUACY_STUDY_ENUM` (`harness/catalogue.py:169`), results tabs | No way to ask "what does load flow need from this transformer". |
| Chat navigation | `ui_open_panel` (`harness/catalogue.py:2440`) opens panels, Guided steps and panel sections; frontend `components/uiNavigate.ts` | Cannot land on one field. |
| Tests | Backend `tests/test_asset_schema*.py`; vitest co-located; no Playwright in the frontend yet (the template-e2e session is adding it) | No test that Guided and Expert show the same words for a field. |

## 3. The Parameter catalogue

### 3.1 Where it lives

The catalogue grows **in place** from `services/asset_schema/` so the existing route, `useAssetSchema`, provenance and units code keep working:

- `fields.py` keeps the per-class tuples; `FieldSpec` gains the new attributes below (all with defaults, so the 133 rows change only where data is added).
- `asset_types.py` (new): `AssetTypeSpec` rows, moved from `utils/assetTypes.ts` `TYPES` (data only; the glyph code stays in the frontend, §5).
- `settings.py` (new): entries for project-level settings that are not Component columns (site, connection agreement, reliability goal, optimisation levers, finance inputs), each with a `scope` naming its owner object. `EH_CUSTOM_COLUMNS` reads its kinds from here instead of declaring them.
- `functions.py` (new): the `Function` enum and its guide text.
- `guide/<entry-id>.md` (new): one markdown file per entry, asset type, group and function (the long text). Short fields stay in Python, where tests and type checks see them.
- `catalogue.py` (new): assembles the above into the payload, computes a content hash, and is the one reader every consumer calls.

Route: `GET /api/catalogue` returns the whole catalogue (about 400 entries, ~150 kB uncompressed with guide bodies; served with `ETag` = content hash; the frontend caches it with `staleTime: Infinity` keyed by the hash). `GET /api/catalogue/guide/{id}` returns one rendered guide body, so the payload can drop bodies later if it grows. The old `GET /api/network/asset_schema/{component}` becomes a view of the catalogue with the same shape (no frontend break during migration).

### 3.2 An entry

| Attribute | Type | Meaning | Today |
|---|---|---|---|
| `id` | str | Stable entry id, `<asset-or-scope>.<slug>` from the plain name (`generator.size`, `connection.short_circuit_level`). Never shown. Frozen once shipped; a rename adds the old id to `former_ids`. | — |
| `model_name` | str | The raw column or setting key (`p_nom`). Shown only on the guide's "In model files" line. Used for writes, imports and search. | `key` |
| `scope` | str | Component class (`Generator`) or settings object (`connection`). | implicit |
| `plain_name` | str | Label on every screen, card, tooltip, chat reply and report. Unique within its group. | `label` |
| `short_help` | str | One sentence, ≤140 chars, shown under the field in Advanced/Full detail and in search results. | `help` (long today) |
| `guide` | file | Long text (§6.2). | — |
| `group`, `order` | str, int | Panel group and position (§3.4). | `group`, tuple order |
| `depth` | `key` \| `basic` \| `advanced` \| `detail` | Progressive disclosure (§4.2). `key` replaces `key_param`. | `key_param` |
| `unit_display`, `unit_storage`, `scale` | | As today; tonne-bus override stays in `size_units()`. | yes |
| `kind`, `options` | | As today; each option gets a plain name (`options_plain`). | partly |
| `range` | `{min, max, soft_min, soft_max}` | Hard bounds refuse a write (backend, all callers); soft bounds warn "Unusual for <asset type>: typical 10–40 %". | — |
| `default` | `{value, source}` | `source` is `library` (Generic default, Assumed), `engine` (the engine's own default; shown, not Assumed), `template`, or `none` (Unavailable until set). | Library packs |
| `functions` | `{function: "needs" \| "uses"}` | Which functions read it; absent means ignored. "needs" blocks that function's run when missing; "uses" improves it. | prose in help |
| `aliases` | tuple[str] | Search synonyms ("VOM", "fuel cost", "OPEX", "rating", "Pmax"). Never shown as labels. | — |
| `reveal_when`, `derived`, `required`, `blank`, `part` | | As today. | yes |
| `related` | tuple[id] | Guide cross-links. | — |

### 3.3 An asset type

| Attribute | Meaning |
|---|---|
| `id`, `plain_name`, `plural`, `category` | As `TYPES` today (category: supply, storage, demand, conversion, network, site). |
| `component`, `match` | The Component class and the rule that recognises an existing row (carrier, role, `eh_role`), moved from `assetTypes.ts`. |
| `icon` | A glyph id from the closed glyph set (§5.2). |
| `symbols` | `{sld: <symbol id>, map: <marker id>, site3d: <appearance id>}`; each falls back to the category default. |
| `ports` | `[{id, plain_name, model_name: "bus0", carrier: "AC"\|"any"\|..., side}]`, so a 3-port electrolyser or a transformer's HV/LV sides draw the same in every view. |
| `groups` | Group ids and order for this type; entries are filtered by `scope` + `match`. Replaces the `hidden=` props in `PropertiesPanel.tsx`. |
| `functions` | Functions this type takes part in; `only_for` marks a function-only type (a load-flow shunt, a capacity-expansion candidate) that the canvas shows only through that function's lens or once placed. |
| `palette` | Flow section and palette group where it can be added; creation defaults (entry id → value). |
| `colour` | A token name (`--color-carrier-h2`, `--color-asset-storage`), never a hex. |

### 3.4 Groups (one list, all asset types)

`GROUPS` today: identity, size, investment, fom, variable, performance, replacement, provenance. The catalogue renames and extends them with plain names, in this order:

1. **General** (name, connection point, type, in service)
2. **Size and limits** (capacity, min/max output, energy, ramp limits)
3. **Electrical** (voltage, impedance, taps, reactive power; Load flow and Short circuit)
4. **Operation** (efficiency, standing losses, start-up, availability, unit commitment)
5. **Running cost** (variable cost, fuel price, emissions)
6. **Investment** (overnight cost, lifetime, fixed O&M, sizing on/off, candidate bounds)
7. **Reliability** (failure rate, repair time, criticality; Adequacy)
8. **Replacement and life** (vintage, renewals)
9. **Source** (provenance; read-only)

Order is fixed for every asset type; a type shows only groups with entries. Guided cards group by the same ids, so a field never moves group between modes.

### 3.5 Coverage rule ("every user-settable parameter")

- Every Component column the GUI or chat can write is an entry, or is listed in `not_offered.py` with a one-line reason (engine-internal, output, unsupported). A test walks `attribute_catalog.py`'s PyPSA input columns for the 11 classes and the `EH_CUSTOM_COLUMNS`, and fails on any column in neither.
- Every project-level setting an endpoint accepts (flow targets in `FIELD_LABELS`, the reliability goal, optimisation levers `models/energy_hub.py:151`, connection agreement, finance inputs in section 7) is an entry in `settings.py`. Slice 01 starts with the ~35 `FIELD_LABELS` keys and the levers; a test lists the remaining write routes without entries as `KNOWN_GAPS` (shrink-only).
- Owner question Q3 (§9) decides whether Full detail offers every PyPSA input column or only the ones our engines read.

### 3.6 Plain names (RAW-NAMES-1)

- `plain_name` is written for a planner, not a modeller. Starting table for review by UX (23); slice 01 fills the rest and UX OKs the full list as one table in the PR.

| Model name | Plain name (proposal) | Group |
|---|---|---|
| `p_nom` | Capacity | Size and limits |
| `p_nom_extendable` | Let the optimiser size it | Investment |
| `p_nom_min` / `p_nom_max` | Smallest size / Largest size | Investment |
| `p_max_pu` / `p_min_pu` | Available output (share of capacity) / Minimum output | Size and limits |
| `marginal_cost` | Running cost per MWh (Q1 decides vs "Marginal cost") | Running cost |
| `capital_cost` | Yearly capital cost (derived) | Investment |
| `efficiency` | Efficiency | Operation |
| `bus` / `bus0` / `bus1` | Connected to / From / To (ports name these per asset type: "Grid side", "Hydrogen out") | General |
| `carrier` | Energy type | General |
| `s_nom` | Rating (MVA) | Size and limits |
| `x`, `r` | Reactance, Resistance | Electrical |
| `eh_sk_mva` | Short-circuit level at the connection (MVA) | Electrical |
| `committable` | Must start and stop (unit commitment) | Operation |

- The guard (§7.2) fails on a model name in user-facing text; the guide's "In model files" line is the one allowed place (and the import mapping dialog, which shows both columns by design).
- Chat: tool results carry `plain_name` beside every `model_name`; the system prompt tells the model to use plain names; the chat renderer replaces any backticked or bare token that exactly matches a catalogue `model_name` with its plain name at render time (deterministic, no model trust), and the assistant pilot scorer counts raw names in replies as a failure.
- Reports and the Assumptions ledger use `plain_name` + unit from the catalogue; a report appendix lists "Parameter guide" entries for every parameter the report shows (one line: plain name · short help).

## 4. Expert editing

### 4.1 One field component for both modes (EXPERT-GUIDED-1)

- A single `CatalogueField` (new, `src/catalogue/`) renders any entry: plain name, unit, input by `kind`, range check, Assumed chip, (i) button, provenance chip, short help at Advanced depth. Guided cards (`pages/hubDesign/cards/*`), `FieldDrawer.tsx` and Expert `AssetFields.tsx` all render through it. No view defines its own label, unit or help for a catalogued entry.
- Values are read and written through the existing Asset write path (`utils/assetWrite.ts`) and the server validates against `range`, so a value set in Guided, Expert or chat is the same value with the same refusal text ("Capacity: must be at least 0 MW").
- Template ledgers reference entry ids; their labels come from the catalogue. `ledgerFieldLabels()` and `FIELD_LABELS` are removed once every caller reads entries (slice 03). A template may still give a value, a range and a source; it may not rename.
- Guided shows `depth: key` entries per section card (≤3 decisions); any other entry stays reachable through the Guided field drawer (§4.3, search, chat), never through a Guided copy.

### 4.2 Depth (progressive disclosure)

Properties panel per asset, in Expert:

```
PV roof · PV array                                 [ Find a parameter… ⌘K ]
Lens: All ▾                                        Key · Basic · Advanced · Full detail
▸ General             Connected to: Site LV bus · Energy type: Solar
▾ Size and limits     Capacity [ 2.4 ] MW (i)   Available output: profile "Roof PV" (i)
▸ Running cost        1 assumed
▸ Investment          Sizing: off · 3 assumed
  Advanced (6) ›      Full detail (11) ›
```

- **Key**: the Guided set. **Basic** (open by default in Expert): what most studies set. **Advanced**: folded per group, with a count. **Full detail**: every remaining entry incl. engine-default ones, shown with "engine default" when unset.
- The depth selector is sticky per user (localStorage convenience), never per project. "Expert never shows less than Guided" (review-ux.md, #1193) holds by construction: Basic ⊇ Key.
- A folded group header shows a one-line summary (value of its first entry, count of Assumed values, a "needs" marker if the current lens function needs a missing entry).
- Per-function view: the lens (§4.4) filters groups to entries the function reads and shows "Load flow needs 2 more" with each missing entry named and a "›" to it.
- Section placement follows design.md §2.1: asset entries live in section 5 (Assets & sizing) panels; settings entries live in their section (connection → 3, goal → 8, finance → 7). The catalogue's `scope`/`section` decides, and search lands there.

### 4.3 Parameter search

One entry point (owner rule UX-flow), two scopes. Owner question Q2 picks between this and a dedicated bar.

- **Global: the ⌘K palette** gains a "Parameters" source next to commands and projects. It indexes catalogue entries and the project's assets: a query matches plain name, aliases, short help, guide text, and model name ("p_nom" finds "Capacity"; the row shows "matches the model-file name", never the raw name). Ranking: exact plain name > alias > word prefix > guide text; entries the current lens needs rank first.
  - A row reads `Capacity · PV array · Size and limits` (an entry) or `PV roof › Capacity · 2.4 MW` (an asset's value). Enter opens the asset's panel, sets the depth needed to show the field, unfolds its group, scrolls, focuses the input and flashes it (request-then-scroll, review-ux.md Navigation). Shift+Enter opens the entry in the Parameter guide instead.
  - An entry with several assets of that type lists them ("Capacity on 4 PV arrays") and jumps to the chosen one; with none, it offers "Add a PV array ›" in its flow section.
- **In-panel: a filter field** at the top of the Properties panel ("/" focuses it) narrows the open asset's fields as you type. It is a filter, not a second search.
- **In Guided**: the same palette. A hit on an entry shown on a Guided card opens that card's field drawer; a hit on any other entry opens it in the Guided field drawer (`FieldDrawer.tsx`) with "Also in Expert › Assets › PV roof › Advanced" as a link, so Guided never switches mode on its own (EXPERT-GUIDED-1, design.md §3.5).
- One jump function, `openParameter({entryId, asset?})`, used by the palette, the guide, chat navigation and the "needs" markers; it goes through `withReturn(...)` so "Back to <origin>" works.

### 4.4 Function lens

- A lens selector on the workbench (All · Dispatch · Capacity expansion · Load flow · Short circuit · Grid compliance · Adequacy · Finance) filters, in every view at once: canvas emphasis (assets the function ignores are dimmed, function-only assets appear), properties groups, search ranking, and the Readiness section's "needs" list.
- Default lens: All. The scenario's run functions (section 8) offer their lens as a one-click chip. Owner question Q4 decides global vs per-panel.
- Readiness: the flow endpoint (design.md §3.1) reads `functions.needs` per asset to state "Load flow: 2 inputs to do", each naming the plain name and linking through `openParameter`. Guided shows the same items (design.md §3.5: Guided never hides a blocker).

### 4.5 UX rules this follows

From `docs/agents/review-ux.md`: field refusals open with the plain name (#1199, #1424); "›" only on real links; disabled reasons as visible text; "—" with its reason; Assumed chips have a visible key; one money notation per section; ISO dates; no engine id in Basis text. DENSITY-1: the panel keeps 9–11 px Inter; the depth selector and lens are compact segmented controls.

## 5. Canvases: one visual language and the asset plug-in contract

### 5.1 Shared visual language

Coordinated with the ui-cohesion audit (session `session_01G7qCunZpDhHZhfRd2msNML`, which audits the canvases) and THEME-LIGHT slice (c) (canvas colours get light/dark pairs). This spec does not choose token values; it fixes where each view reads them.

- One module `src/canvas/visuals.ts` (new) answers `assetVisual(typeId, view, state)` → `{glyph, colourToken, size, ports, stroke}` from the catalogue. SLD nodes, map markers, 3D labels/billboards and palette tiles all call it.
- **Tokens only**: carrier colours (`--color-carrier-*`), asset-category colours (new `--color-asset-*`), state tokens and result bands. `LOADING_BANDS`, `NO_RESULT_COLOR`, `socColor` (`components/CanvasResultsContext.tsx:721-734`) move onto tokens. Canvas code that cannot read CSS variables (three.js, leaflet divIcon HTML) reads them through one `readToken()` helper, refreshed on theme change. The hex maps (`CARRIER_BUS_COLORS`, `CARRIER_LINK_COLORS`, `TYPES` colours) are replaced, and the existing pin test (`charts/chartThemeTokens.test.ts`) widens to "no hex literal in `pages/*Canvas.tsx`, `pages/topology*.ts`, `site3d/`".
- **States, the same in all three views**: selected (accent ring), hovered, **Assumed values present** (small dot), **candidate** (capacity-expansion option: dashed outline, "+" badge; after a solve, solid if built, grey if not), out of service (hatched), **needs input for the current lens** (warn dot), result overlays (loading band colour, flow direction animation honouring `prefers-reduced-motion`, review-ux.md #1335).
- **Glyph parity**: the SLD symbol, the map marker and the palette tile use the same glyph; 3D shows the model plus the same glyph on its label. IEC symbols for network elements (transformer two circles, shunt, series reactor, switch) live in the glyph set and are used by SLD and map.

### 5.2 Asset plug-in contract

A new asset type is **one `AssetTypeSpec` row plus its entries** (and guide files). With no frontend code it appears in:

| Surface | How it picks the type up |
|---|---|
| Palette / add menu | `palette` (section, group, creation defaults) |
| SLD | `symbols.sld` glyph, `ports` drawn as handles (replaces the single centre handle where a type declares more than one port) |
| Map | `symbols.map` marker built by one generic divIcon builder from the glyph + colour token |
| 3D | `symbols.site3d` → an existing `APPEARANCE`/template id; unknown falls back to the category block + glyph label |
| Properties panel | `groups` + entries by `scope`/`match`, depth, lens |
| Search, guide, chat | entries and the type's own guide file |
| Readiness | `functions.needs` |
| Reports | plain names, units, ledger |

Only a **new glyph** or a **new 3D shape** needs frontend code: one file in `src/canvas/glyphs/` registered in the exhaustive `GLYPHS` record (a test fails on a catalogue glyph id with no glyph), or one template in `site3d/templates.ts`. The category `if/else` chains in `TopologyCanvas.tsx` and `CATEGORY_STYLE` in `MapCanvas.tsx` are replaced by `assetVisual()`.

Worked examples the contract must carry (slice 04 tests each with a fixture catalogue row):
- **Capacity-expansion candidate line**: asset type `candidate_line` (`component: Line`, `functions: {expansion}`, `only_for: expansion`), entries: smallest/largest rating, cost per km, corridor. Shown dashed on SLD and map, hidden in 3D until built.
- **Load-flow shunt**: `shunt` (`component: ShuntImpedance`, `only_for: load_flow, short_circuit`), IEC shunt glyph on SLD, small marker at its bus on the map, none in 3D.
- **Series reactor** and **tap-changing transformer**: new glyphs; tap entries in Electrical, `functions: {load_flow: needs}`.

### 5.3 Results and functions on canvases

Function-specific overlays (load-flow loading and voltage, expansion "built / not built", short-circuit levels, adequacy criticality) register against the lens: an overlay declares the function and the result it reads; the lens shows it. This uses `CanvasResultsContext` as today; slice 04 only moves its colours onto tokens and adds the lens hook.

## 6. Parameter guide (WIKI-1)

### 6.1 Where it shows

- An (i) beside each field (Guided and Expert) opens its entry in a right-side drawer over the current view (no navigation; Esc closes; focus returns; review-ux.md #1405).
- The drawer has a search field (the same index as §4.3) and browses by asset type, group and function ("What does load flow need?").
- The chat assistant quotes `short_help` and links entries; the link opens the same drawer.
- The drawer is generated from the catalogue; there is no hand-written help page to drift.

### 6.2 A guide entry

```
Capacity                                              PV array · Size and limits
The largest output the array can deliver, in MW AC.
What it means       … (2–6 sentences)
Used by             Dispatch (needs) · Capacity expansion (starting size; the optimiser may add) · Load flow (uses)
Typical values      0.1–50 MW for site PV · hard limit ≥ 0
Default             none: Unavailable until set   |   Generic default 1 MW (Assumed; Library "PV 2025", 2025)
Related             Let the optimiser size it › · Available output › · Smallest size ›
Example             A 3 MWp roof with a 1.2 DC/AC ratio: 2.5 MW
In model files      p_nom
[ Open on PV roof › ]
```

`Used by`, `Typical values`, `Default`, `Related` and `In model files` are generated from the entry; `What it means` and `Example` come from `guide/<id>.md`. A test fails on a guide file with no entry, an entry with no guide file, or a model name outside the "In model files" line.

## 7. Chat assistant

### 7.1 Tools (harness contract, `harness/README.md` "The contract" :120)

- `find_parameter(query, asset_type?, function?)` → up to 8 entries `{entry_id, plain_name, asset_type, group, unit, short_help, guide_link, assets: [{name, value, assumed}]}`. Read-only. Lets the model answer "where do I set the transformer tap?" and quote the guide.
- `open_parameter(entry_id, asset?)` → emits a navigate event of a new kind `parameter`; the frontend's `uiNavigate.ts` calls `openParameter()` (§4.3). In Guided it opens the field drawer (EXPERT-GUIDED-1). Safety tier: navigation.
- Existing write tools keep taking model names (model-facing), but their results add `plain_name`, and refusals open with the plain name.
- `component_discovery.py` / `attrs_doc.py` read their text from the catalogue instead of their own copies; `attrs_doc.py`'s generated literal is regenerated from it.
- Per the README: new tool blocks at the end of `TOOLS`, `TOOL_ROUTES` entries, inventory `--check`, route-parity declarations under `smoke/assistant_route_parity_declarations.d/`, any new error kind under `tool-error-kinds.d/`.

### 7.2 Raw-name guard (RAW-NAMES-1)

- Backend writes `frontend/src/catalogue/modelNames.generated.json` (sorted list of every `model_name` and `former_ids`), checked current by a backend test.
- `uiCopyGuard.test.ts` widens from its 20 surfaces to every `.ts`/`.tsx` under `src/` except tests, generated files and `src/catalogue/`. New rule: a string literal or JSX text that contains a model name as a whole word (snake_case names; short names like `x`, `r`, `bus` only inside `<code>`, quotes or `=`) fails. Allowlist: `src/uiCopyGuard.allow.json` with `{file, text, reason}` rows, shrink-only (a test fails on a row that no longer matches).
- Existing offenders (`CapacityBoundsEditor.tsx:170`, "MC" in `PropertiesPanel.tsx:263`, and whatever the widened scan finds) are either fixed in slice 02 or listed in the allowlist with the slice that fixes them.

## 8. How it fits the whole tool (HOLISTIC-1)

| Part of the tool | What changes | Who |
|---|---|---|
| 10 flow sections (design.md §2.1) | Entries carry their section; search and readiness land in the right section. Section states unchanged (server-owned). | slices 01, 03 |
| Guided | Cards and the field drawer render `CatalogueField`; Key depth = Guided set. | 03 |
| Expert | Depth, groups, lens, in-panel filter. | 03 |
| Canvases | `assetVisual()`, tokens, plug-in contract, lens. | 04, with ui-cohesion and THEME-LIGHT (c) |
| Chat | `find_parameter`, `open_parameter`, plain names in results and replies. | 01 (tools), 02 (render-time names, navigate kind) |
| Reports & Assumptions ledger | Plain names/units from the catalogue; guide appendix. | 03 (labels); appendix is a follow-up |
| Templates | Ledgers reference entry ids; templates give values, not names. Every template must pass the template-e2e Expert pass. | 01 (ids), 03 (labels), template-e2e |
| Library / Generic defaults | `default.source = library` marks Assumed; values stay in the Library. | 01 |
| Import / export | Import maps file columns by `model_name` and `former_ids`; the mapping dialog shows plain name beside the file column. | 01 (data); dialog is a follow-up |
| THEME-LIGHT | Canvas colours become tokens in 04, after slice (c); 02/03 touch `components/` and `layout/` only after (b). | sequencing |
| ui-cohesion audit | Its canvas findings feed slice 04's state list; 04 starts after the audit's canvas report. | sequencing |
| template-e2e (E2E-TEMPLATES-1) | Adds an Expert pass driven by `GET /api/catalogue` (§10). | session `session_01G7qCukL5Afn6M67ai7Wyuf` |
| grid-compliance, study-harness, adequacy | Their inputs become entries with `functions: grid_compliance / adequacy`; their profiles can read `needs` instead of hand lists. | follow-ups per stream |

## 9. Open questions for the owner

Each goes to the coordinator as options; the recommended option is first.

- **Q1 Naming depth.** (a, recommended) Plain planner words, keeping standard industry terms where a planner uses them ("Capacity", "Efficiency", "Capital cost"; "Running cost per MWh" for marginal cost): readable for newcomers, still familiar to engineers. (b) Industry terms everywhere ("Marginal cost", "Nominal power"): closest to textbooks, harder for non-modellers. (c) Everyday words only ("How big", "Cost to run"): friendliest, but engineers must translate.
- **Q2 Search entry point.** (a, recommended) Extend the ⌘K palette with parameters + a filter field in the Properties panel: one global search the user already knows, one local filter. (b) A dedicated always-visible "Find a parameter" bar in the top bar: more discoverable, a second global search next to ⌘K. (c) Palette only: simplest, no in-panel narrowing.
- **Q3 Full detail coverage.** (a, recommended) Every input our engines read (dispatch, expansion, load flow, short circuit, adequacy, finance), each with a plain name and guide; other PyPSA columns are listed as "not offered" with a reason: complete for our functions, no untested knobs. (b) Every PyPSA input column: maximal control, ~2× entries to name and document, some untested. (c) Only today's ~133 fields plus settings: fastest, leaves gaps for load flow and unit commitment.
- **Q4 Function lens scope.** (a, recommended) One workbench-wide lens that filters canvases, panels, search and readiness together: one switch, consistent picture. (b) A filter in each panel and canvas separately: more control, can disagree between views. (c) No lens, a "Used by" tag on each field only: least UI, function-only assets always visible.

## 10. Testing

### 10.1 Unit and contract tests

- **Catalogue completeness** (backend): every entry has plain name, group, depth, unit (or `kind` with none), short help ≤140 chars, guide file, `functions`; ids unique and in `catalogue.ids.txt` (append-only; a removed id must appear in a `former_ids`); defaults inside `range`; coverage rule §3.5; no model name in plain name, help or guide body.
- **Validation parity**: one test per `kind` that a write out of `range` is refused with the same plain-name text via the REST route, the chat write tool and the Guided card's save.
- **Raw-name guard** (§7.2) over all of `src/`.
- **Mode parity** (EXPERT-GUIDED-1): for each template fixture, render every Guided card and field drawer and the Expert panel for the same project; for every entry both show, assert identical plain name, unit and displayed value, and the same Assumed mark. A static test fails when a file under `pages/hubDesign/` or `layout/properties/` defines a label for a catalogued model name.
- **Plug-in contract**: a fixture catalogue with three new asset types (§5.2 examples) renders in palette, SLD, map, 3D, panel, search and guide with no code change; `GLYPHS` exhaustiveness; no hex in canvas files.
- **Search**: ranking table tests (plain name, alias, guide text, model name) and the jump (`openParameter`) in both modes.
- **Chat**: `find_parameter`/`open_parameter` schema, parity and inventory tests per the harness README; a pilot-eval scorer counting raw names in replies.

### 10.2 Template e2e: the Expert pass

Contract for the template-e2e session (`session_01G7qCukL5Afn6M67ai7Wyuf`), which owns the journeys:

- For each template and each function the template's scenario runs: `GET /api/catalogue?function=<f>&depth=all` gives the needed and used entries; for every asset in the project matching an entry's type, the journey opens the field via ⌘K search (typing the plain name), sets a valid in-range value, saves, and asserts: the value reloads; the Guided view shows the same plain name, unit and value where the entry is Guided-visible; the guide entry opens from (i); the function's readiness line no longer names it.
- Selectors: `data-testid="param-<entry id>"` on each `CatalogueField` input, `param-group-<group id>`, `param-guide-<entry id>`.
- Runs after slices 01–03 merge; slice 04 adds a canvas check per function lens (function-only assets appear, ignored ones dim).

## 11. Slices

Four builder PRs. Each is one reviewer area and ≤1,500 changed lines excluding generated files, guide text and shots (PR-SIZE-1). Issue files: `.scratch/expert-catalogue/issues/01..04`.

| # | Slice | Area / reviewers | Depends on | Avoids / waits for |
|---|---|---|---|---|
| 01 | Catalogue core, guide files, `GET /api/catalogue`, validation, chat `find_parameter`/`open_parameter` | Backend + harness · GK B | — | Harness tool block after #1575/#1590/#1586 merge (or rebase onto them); no frontend files |
| 02 | Frontend catalogue client, Parameter guide drawer, ⌘K parameter search + `openParameter`, chat render-time names + `parameter` navigate kind, raw-name guard over `src/` | UI · UX + GK B | 01 | New files under `src/catalogue/`; `CommandPalette.tsx` and `ChatPanel.tsx` edits after THEME-LIGHT (b) and #1556 |
| 03 | `CatalogueField` in Expert panels and Guided cards/field drawer, depth, groups, lens in panels, readiness "needs", removal of `FIELD_LABELS`/ledger labels, mode-parity test | UI · UX | 01, 02 | After #1565 (PropertiesPanel, AssetFields, assetSchema) and THEME-LIGHT (b) |
| 04 | Canvas visuals: `assetVisual()`, glyph set, tokens for canvas colours/states/bands, asset types from the catalogue, multi-port handles, lens on canvases, plug-in fixtures | UI · UX | 01; 02 for the lens store | After THEME-LIGHT (c) and the ui-cohesion canvas report; avoids #1578 (campus electrical) files |

**Keeping both modes in step per slice (EXPERT-GUIDED-1):** 01 gives both modes the same data; 02's search, guide and chat open fields in both modes (Guided through the field drawer); 03 switches Guided and Expert to the same component in one PR and adds the parity test; 04's canvases are shared by both modes already. No slice ships a mode-only label.

## 12. Out of scope / skipped

- Guide text quality beyond the first draft per entry: UX (23) reviews it in slice 01; deeper engineering text can follow per stream.
- Import mapping dialog and the report guide appendix: follow-ups listed in §8.
- No token values or colour choices (THEME-LIGHT and ui-cohesion own them).
- No change to section states or the flow endpoint's contract beyond reading `functions.needs`.
