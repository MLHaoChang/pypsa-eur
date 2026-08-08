# Asset editing: an editable bottom grid, catalog-driven parameters, drop-on-a-bus — design

**Date:** 2026-08-08
**Branch:** `feature/asset-editing` (worktree `.claude/worktrees/asset-editing`, base master `c2cc4510`)
**Status:** design, awaiting review

**Upstream input, not relitigated here:** `.superpowers/pipeline/asset-editing/ledger.md`
— the request in the user's words, decisions 1–15, and human rulings 16–19 which
amend them. **Evidence base:** `.superpowers/pipeline/asset-editing/recon.md`
(Phase 0, cleared 3/3). Every file, line number and measured claim below is
traceable to recon; section references are given as `recon §n`.

## The problem

Three things the user asked for, in their words:

1. Drag-and-drop of palette assets does not work on the map, and dropping
   anywhere does not attach the new asset to the bus it was dropped on.
2. The right-hand parameter form exposes a fixed subset of each component's
   attributes, with no way to add the rest.
3. The bottom table cannot be edited cell by cell. Changing a value on many
   assets means selecting rows and typing into a toolbar box, which the user
   does not want; copying one value onto many assets is impossible.

All three are true today. `Sidebar.tsx:287-288` hit-tests `closest('.react-flow')`
and returns silently on the Leaflet views, and no drop on any canvas hit-tests a
bus (recon §17). `GeneratorCreate` declares 28 of Generator's 42 materialised
columns and Pydantic's `extra='ignore'` drops the rest without an error
(recon §0, §14 risk 10). `AssetTable` renders read-only cells and defers all
mutation to the bulk toolbar at `BottomPanel.tsx:433-470` (recon §13).

## Goals

- Any cell of any bottom-panel asset tab can be edited in place, and a value can
  be copied onto many assets with the clipboard, round-tripping through Excel.
- Every attribute PyPSA defines for a component is reachable from the UI, and
  the ones a solve requires are marked as required at the moment they become so.
- Dropping a palette asset on a bus attaches it to that bus, on every canvas.

## Non-goals

- The shared engine rewriting all eight `PropertiesPanel` cards, and the six-step
  per-class migration that existed to de-risk it. Both cut in the ledger.
- Preflight-derived coordinate warnings and a coordinate migration. `utils/geo.ts`
  already quarantines out-of-range coordinates; D28 is the whole fix.
- Virtualising the bottom panel. The 1000-row render cap stays (D1).
- Refactoring the per-km/absolute split for line impedance (ruling 19, D15).
- Making the asset-results tab editable — `2026-07-31-asset-detail-results-design.md:569`
  keeps `PropertiesPanel` as the write path, and this spec adds a second write
  path in the bottom panel only.

## What recon measured that this design has to obey

| Measured | Where | Consequence |
|---|---|---|
| `coerceForColumn('')` → `null`, unconditionally; a numeric column with unparseable input also → `null` | `utils/coerce.ts:16,19` (recon §1) | Blank is an explicit clear, not "leave unchanged". A typo silently clears. D12. |
| `null` on a numeric column becomes `inf` for `*_max`/`lifetime`, `-inf` for `e_sum_min`, `NaN` otherwise | `routers/network.py:2015-2020` (recon §1b) | "Blank a cell" means three different things by column name. D12. |
| `clean_scalar` maps every non-finite float to `null` on the way out | `services/serialization.py:38-47` via `df_to_json` (recon §12, `2026-07-31-asset-detail-results-design.md:432-435`) | `NaN`, `inf` and `-inf` are indistinguishable in the GET payload. D12. |
| `PATCH /_bulk` applies one scalar per column to every named row (`df.loc[names, col] = value`) | `routers/network.py:2043-2044` (recon §2) | A row-by-row paste cannot be expressed in the current body shape. D9. |
| The undo middleware coalesces pushes inside a 500 ms window; the changelog does not | `undo_service.py:55,90-102`, `main.py:605`, `network.py:2049` (recon §2) | Two gestures inside 500 ms yield one undo step and two History rows. D11. |
| Zero optimistic-update precedent: no `cancelQueries`, one `onMutate` that touches only local state, one `setQueryData` inside `onSuccess` | recon §4 | D10 is net-new infrastructure with nothing in-repo to copy. |
| 20 of Generator's 53 attributes are `varying=True`; `get_switchable_as_dense` returns the series, so the static column is dead when one exists | recon §12, §14 risk 2 | A "successful" edit that changes nothing about the solve. Ruling 18, D14. |
| The extendable capital-cost rule is a **disjunction**: `capital_cost > 0 OR overnight_cost > 0` | `services/validation_service.py:364-402` (recon §16) | A naive "extendable ⇒ capital_cost required" over-reports. D22. |
| Pydantic v2 default `extra='ignore'`; no Update models — every PUT reuses the Create model | `models/schemas.py`, `routers/network.py:785-787` (recon §0, §14 risk 10) | A newly-exposed attribute returns 200 and never persists. D20. |
| `EditShell`'s `children` slot is a free render seam, but each card's payload enumerates its keys and `...current` overwrites the rest; `toFS(gen, [26 keys])` closes the form seed | `cardKit.tsx:781-783`, `PropertiesPanel.tsx:143-171,181-193,144,206-212` (recon §15-C) | Decision 9 is additive in the DOM and non-additive in the data path — three layers. D20. |
| `LinePanel` and `TransformerPanel` use none of the four shells; local `numInp`/`txtInp` into a plain `<Section>` | `PropertiesPanel.tsx:1705,1836,1847,1866`, `:2023,2140,2149,2170` (recon §15-B) | Two families of edit form, both in scope for D20 and D22. |
| The 1000-row cap truncates only `displayed`; `filtered`, `sorted`, select-all and bulk edit already run on the uncapped set | `BottomPanel.tsx:181-219,258-262` (recon §13) | Decision 5's "paste reaches rows past the cap" already holds structurally. D1. |
| `App.tsx:478-481` handles Escape on `window` with no editable-element guard; `Dialog.tsx:148` binds Escape at capture with `stopPropagation` | recon §7, §14 risk 6 | Escape-cancels-edit collides. D5, D18. |
| Ctrl/Cmd+C, Ctrl/Cmd+V and all four arrow keys are unbound globally | recon §7 | The grid's clipboard and navigation keys have no global competitor. |
| `routers/network.py` is a declared change hotspot, 4000+ lines; multiple agent sessions share this worktree; path-limited commits only | `.cursor/rules/pypsa-gui-backend.mdc:27-29`, `CLAUDE.md:702-712` (recon §11, §14 risk 12) | Backend work is surgical and test-first. D3, D9, D20. |
| `GET /api/network/timeseries` already returns, per component and attribute, the exact column names that have a series | `routers/network.py:3018-3042`; client `api/network.ts:241` | Ruling 18 needs no new endpoint, only two more components in that list. D14. |
| `navigator.clipboard` is undefined outside a secure context and `read()` needs a gesture plus a Firefox permission | `LocalSettings.tsx:87-97`, `ChatPanel.tsx:1070-1085` (recon §9) | The grid uses `ClipboardEvent` and never `navigator.clipboard`. D6. |

## Sequencing

**C → A → B.** C is the smallest and its only shared file is `Sidebar.tsx`, which
nothing else in this feature touches. A is the largest and owns `BottomPanel.tsx`
and `PATCH /_bulk`. B depends on A's `utils/attributeCatalog.ts` and on the new
catalog endpoint, so it runs last and inherits both already characterised.

Within each scope, characterization tests are the first task, not cleanup (D30).

## Vocabulary, and where these decisions are recorded

**There is no glossary, context map or ADR directory in this repo** — recon §10
checked eighteen candidate locations in the worktree and the main checkout and
found none. The de-facto ADR log is `docs/superpowers/specs/`, where each spec
carries a `D1…Dn` decisions list. **This spec adds its decisions to that log and
creates no new location.** The terms it introduces:

| Term | Meaning | Settled by |
|---|---|---|
| **Paste target** | The set of rows a paste writes to: the checkbox row selection if non-empty, otherwise the active cell's row. Rows past the render cap are included. | D7 |
| **Fill gesture** | One value applied to every cell of the paste target in the active column. Produced by a 1×1 paste or by a Ctrl/Cmd+Enter commit. | D7 |
| **Block paste** | An N×M clipboard matrix mapped row-by-row and column-by-column onto the paste target. | D7 |
| **Series-shadowed cell** | A cell whose attribute is `varying` in the catalog *and* for which a time series exists on that specific asset, so the static value is dead. Never editable. | D14 |
| **Editability override list** | The short named list of attributes whose editability differs from their catalog `status`, each with its reason. Exactly two entries today. | D13 |
| **Extras section** | The appended block on an edit card holding attributes beyond the card's curated set. | D20 |
| **Terminal prefill** | Seeding `bus` (or `bus0`) on the creation form from the bus a palette item was dropped on. | D27 |
| **`data-bus-name`** | The DOM attribute, emitted by both canvases' bus renderers, that carries the bus name for drop hit-testing. | D25 |

**One prior spec's wording is scoped, not reversed.**
`2026-07-31-line-parameters-and-carrier-icons-design.md:262-264` records that the
properties form presents `r`/`x`/`b` per km while PyPSA stores them absolute.
D15 scopes that statement to the properties form and states the grid's opposite
convention; the storage convention, `SUBMIT_TRANSFORM` and the rescale behaviour
(B1–B4 of that spec) are unchanged.

---

## Decisions

### Cross-cutting

**D1. `AssetTable` is extended in `BottomPanel.tsx` in place. No virtualisation.**
The `<table>` markup, sticky checkbox column, shift-range selection, the
selected-row cap-splice (`:212-217`), the `truncated` notice (`:423-430`),
`rowRefs`/`scrollIntoView` (`:317-320,325-333`), `TAB_COLUMNS` and the 1000-row
cap all stay. Rationale, measured: the cap guards DOM node count only, and
selection, sort, search and bulk edit already operate on the uncapped `sorted`
array (recon §13), so decision 5's reach-past-the-cap requirement holds without
virtualising. Virtualising would mean replacing the table markup, re-doing the
sticky column and re-doing scroll-to-selection, against zero test coverage.

**D2. Three new pure modules, two new hooks, and `utils/coerce.ts` is not modified.**

| Module | Owns | Contains no |
|---|---|---|
| `frontend/src/utils/clipboardTsv.ts` | TSV parse and serialise; shape detection against the paste target | React, DOM |
| `frontend/src/utils/gridEdit.ts` | Per-cell validation and coercion; wraps `coerceForColumn` | React, DOM |
| `frontend/src/utils/attributeCatalog.ts` | Editability resolution, series-shadow lookup, unit labels, reveal rules | React, DOM |
| `frontend/src/hooks/useAssetDrag.ts` | The pointer-drag + drop hit-test currently duplicated in `Sidebar.tsx` and the dead `AssetPalette.tsx` | — |
| `frontend/src/hooks/useCatalog.ts` | `useQuery` over the catalog endpoint | — |

`gridEdit` **wraps** `coerce.ts` rather than replacing it, so today's blank
semantics are preserved by construction and its ten existing tests
(`utils/coerce.test.ts`) stay green unmodified. The invariant restated:
`coerceForColumn('')` returns `null` before any type dispatch, and `null` is
re-interpreted per column name by the backend (D12).

**D3. One new backend endpoint and one new backend service module. The four
existing catalog readers are left untouched.** `GET /api/network/catalog/{component}`
lives in `routers/network.py` as a thin route that calls a new
`backend/services/attribute_catalog.py`; all catalog logic lives in the service
(`.cursor/rules/pypsa-gui-backend.mdc:10-12`). Recon §12 notes four existing
sites that already do `status.str.startswith("Input")` (`network.py:186-192`,
`:2456-2461`, `:2724-2731`, `vintage_service.py:234,470`) and suggests
consolidating them. **They are deliberately not consolidated in this build**:
`routers/network.py` is a declared hotspot, all four have their own
try/except "fall back to all columns" behaviour, and none is covered by a test.
Recorded under Out of scope rather than left unmentioned.

### Scope A — the editable grid

**D4. One cell editor is mounted at a time; a single click opens it.**
Non-editing cells render as they do today. The draft is a single
`{ name, col, raw }`, not the flat draft map `CarriersTable` uses
(`BottomPanel.tsx:1065,1099`). Rationale: `CarriersTable`'s always-on controlled
input is correct for a handful of carriers, but at the render cap it would mount
1000 × 15 inputs, which is the DOM-node budget the cap exists to protect
(`BottomPanel.tsx:202-206`). Because a fresh editor mounts per cell, the
uncontrolled-input staleness rule (`CLAUDE.md:586-587`) is satisfied structurally
and no `key`-remount trick is needed. Commit on blur and on Enter, no round-trip
when the committed text equals the cell's current display text — the no-op skip
`BottomPanel.tsx:1122` already does.

**D5. The keyboard map, and the two global Escape handlers are guarded.**

| Key | Editor closed | Editor open |
|---|---|---|
| Arrow keys | move the active cell | move the caret inside the input; never navigate |
| Enter | open the editor on the active cell | commit, close, move down one row |
| Ctrl/Cmd+Enter | — | commit as a **fill gesture** over the whole paste target, close |
| Tab / Shift+Tab | move the active cell right / left | commit, move right / left |
| Escape | clear the active cell marker | discard the draft, close |
| A printable character | open the editor seeded with that character | inserts normally |
| Ctrl/Cmd+C, Ctrl/Cmd+V | copy / paste (D6) | native input copy / paste |
| Ctrl/Cmd+S | not handled by the grid | commit the draft, `stopPropagation`, toast "Cell saved — press again to save the project" |

Escape-cancels-edit must survive two unguarded global handlers. The grid calls
`stopPropagation()`, and — because a capture-phase `document` listener runs
before any element handler regardless of registration order — the two global
Escape branches that lack an editable-element guard gain the one already present
three lines away at `App.tsx:485`: `App.tsx:478-481` and `AppHeader.tsx:281`.
Two inline guards using the in-file idiom, no new module, no arbitration layer.
`Dialog.tsx:148` is left alone; D18 keeps the grid's only confirmation out of a
`Dialog`.

Ctrl/Cmd+S is intercepted because `CLAUDE.md:650-666,812` requires that a save
never runs with a pending edit outstanding, and the grid cannot make its PATCH
land synchronously. Swallowing the first keypress is the honest behaviour.

**D6. Clipboard I/O goes through `ClipboardEvent`, never `navigator.clipboard`.**
Copy handles the `copy` event and calls `e.clipboardData.setData('text/plain', tsv)`;
paste handles the `paste` event and reads `e.clipboardData.getData('text/plain')`.
This needs no secure context (`LocalSettings.tsx:87-97`), no user-gesture
permission and no Firefox prompt (`ChatPanel.tsx:1070-1085`). The wire format,
since recon §9 found no rule to inherit:

- **Emit** `\r\n` between rows, `\t` between cells, no trailing terminator.
  CRLF matches the one adjacent house rule (`CLAUDE.md:575-576`, CSV export) and
  is accepted by Excel and Numbers on both platforms.
- **Accept** `\r\n`, `\n` or `\r` as a row separator, and drop exactly one
  trailing empty row.
- **No quote grammar.** A cell is the literal text between tabs. A value
  containing a tab or a newline cannot be represented; see Known limitations.
- **CSV-injection guard, scoped to string columns.** On copy, a cell in a column
  whose catalog `dtype` is neither numeric nor boolean and whose text starts with
  `=`, `+`, `-`, `@`, tab or CR is prefixed with a single quote
  (`CLAUDE.md:575-576`). On paste, exactly one leading single quote is stripped
  from a cell targeting a string column. Numeric and boolean columns are never
  prefixed, so negative numbers round-trip byte-exactly.

**D7. Three paste shapes, resolved against the paste target.** The **paste target**
is the checkbox row selection if non-empty, otherwise the active cell's row, in
the grid's current `sorted` order. Let the clipboard matrix be N rows × M columns
and the target be T rows.

1. **N=1, M=1 → fill.** The value is written to every target row in the active
   column.
2. **N=T, M=1 → row-by-row.** Value *i* goes to target row *i* in `sorted` order.
3. **N=T, M>1 → block.** Column *j* goes to the *j*-th visible column at or right
   of the active column; requires M ≤ the number of visible columns from the
   active one rightwards.
4. **Anything else** is rejected whole, with a message stating the clipboard
   shape and the target shape.

A **fill gesture** is shape 1 or a Ctrl/Cmd+Enter commit (D5) and is subject to
the same rules.

**D8. Rejection is whole-batch and names the offending cells.** A paste is
rejected, changing nothing, if any of the following holds: the shape does not
match (D7); a target cell is not editable (`name`, an `Output` attribute, an
override-list read-only entry, or a series-shadowed cell); or a value fails
`gridEdit` validation for its column. The message names up to five offending
cells as `row / column` and states the count of the rest. This matches the
backend's own all-or-nothing semantics (`network.py:1954-1957`) and
`CLAUDE.md:693-694`. FastAPI error arrays are formatted into readable strings
before display, never `String([{...}])` (`.cursor/rules/pypsa-gui-frontend.mdc:19`).
Cells that survive validation but whose value equals the current display text are
dropped as no-ops; if none remain, no request is issued and the grid reports
"No changes".

**D9. `PATCH /_bulk` gains an additive row-wise body form.** The current shape
applies one scalar per column to all names, so a row-by-row paste is
inexpressible in it. The route accepts either:

```
{ "component_class": "Generator", "names": [...], "updates": { col: value } }        # today, unchanged
{ "component_class": "Generator", "rows": [ { "name": ..., "updates": {...} }, ...] } # new
```

Both forms keep every existing guarantee: bulk rename refused (`:1944-1945`),
whole-batch 404 on any unknown name (`:1954-1957`), 409 on transient rows
(`:1967-1977`), 400 on unknown columns, dtype coercion before write, one lock
acquisition, and **exactly one changelog entry** (`:2049-2052`). The row form's
entry reads `Bulk: <k> field(s) across <n> row(s)`. The existing per-column
coercion loop is extracted, unchanged, into a module-level function inside
`routers/network.py` that both branches call — a mechanical extraction pinned by
the characterization tests written first (D28), not a refactor of the hotspot.

The client uses the `updates` form whenever every target row receives the same
value in every column (every fill gesture), and the `rows` form otherwise. One
gesture is always exactly one request.

**D10. Optimistic write with rollback, and the exact cache contract.**
There is no in-repo precedent (recon §4), so the contract is stated here in full.
`key = nk(projectId, TAB_TO_API_KEY[componentClass])` with `projectId` read from
`useUIStore.getState().currentProject` in non-React callbacks — the parity rule
at `queryKeys.ts:16-22` is what makes a wrong id return `undefined`.

- `onMutate`: `await qc.cancelQueries({ queryKey: key })`; capture
  `previous = qc.getQueryData(key)` and the current checkbox selection; write the
  new rows with `qc.setQueryData`; return both as context.
- `onError`: restore `previous` with `setQueryData`, restore the selection,
  surface the backend's `detail` (D8 formatting), then invalidate `key` so the
  screen re-reads the truth rather than trusting the rollback.
- `onSuccess`: invalidate exactly the four scoped keys the existing bulk mutation
  already uses (`BottomPanel.tsx:285-289`) — the tab's own key, `nk(projectId,'undoInfo')`,
  the deliberately unscoped `['changelog']`, and `results`. Not `ALL_NETWORK_KEYS`.

While a mutation is in flight, cell editors are disabled — the pattern
`ModelHorizon.tsx:907-913` already uses to prevent a double-blur race.

**D11. One paste or fill gesture is one undo step; keystroke edits may coalesce
(ruling 16).** Before issuing a paste or fill request, the grid waits out the
remainder of 500 ms since its own last successful network mutation, so the
middleware's `claim_push_slot` (`undo_service.py:55,90-102`) always grants a new
snapshot. Single-cell commits do not wait and are allowed to coalesce, matching
text-editor behaviour and avoiding thrashing a 20-deep, 500 MB-capped stack of
full-netCDF snapshots. The rejected alternative — a request flag that forces a
snapshot server-side — was declined because it changes `main.py`'s undo
middleware to serve a client-side timing concern.

**D12. The blank-and-infinity contract, stated once.**

- A numeric cell whose payload value is `null` renders **empty**. The API cannot
  distinguish `NaN`, `inf` and `-inf` (`clean_scalar`), and the grid does not
  guess: rendering `∞` for a value that might be missing is exactly the
  confident-wrong-number failure `2026-08-01-trustworthy-numbers-design.md`
  exists to prevent.
- Committing an empty cell sends `null` and the backend applies its own rule:
  `inf` for `*_max` and `lifetime`, `-inf` for `e_sum_min`, `NaN` otherwise
  (`network.py:2015-2020`). Each column's rule is stated in its header tooltip so
  the user is told which of the three they are choosing.
- `inf`, `+inf`, `-inf`, `infinity`, `∞` and `-∞` (case-insensitive) are accepted
  in a numeric cell and sent as the JSON strings `"inf"` / `"-inf"`, which the
  endpoint's `float(value)` already parses. No backend change, and this is where
  decision 3's `inf`-aware numeric lands.
- **A non-numeric string in a numeric column is rejected by `gridEdit` before
  coercion**, using the catalog `dtype` rather than a sampled value. This is a
  deliberate, named behaviour change from `coerce.ts:19`, which silently returns
  `null` and clears the field; `coerce.ts` itself is unchanged and keeps that
  behaviour for its existing callers.
- The numeric cell editor is `type="text"`, not `type="number"`: recon §15-D
  measured that `<input type="number">` cannot hold `inf` and reads back `''`.

**D13. Editability is the catalog `status` plus an override list of exactly two
entries (ruling 17).** Default: `Input (required)` and `Input (optional)` are
editable, `Output` is read-only and greyed. `name` is never editable in the grid
(decision 3, and `network.py:1944-1945` refuses it). The override list lives in
`utils/attributeCatalog.ts`, each entry commented with its reason:

| Entry | Editability | Reason |
|---|---|---|
| `Bus.control` | **editable**, against `status='Output'` | It selects the AC-PF slack, the app already exposes it at `CreationForm.tsx:74` and `PropertiesPanel.tsx:1636`, and D22's AC-PF rule requires it settable. |
| `Generator.committable` | **read-only**, against `status='Input'` | `PATCH /_bulk` writes `df.loc` directly and its own header comment names flipping `committable` as unsupported through that path. The right panel's per-row PUT remains the way to change it. |

`Bus.generator`, `Bus.sub_network` and `Generator.p_nom_opt` materialise as
columns and reach the frontend (recon §14 risk 5); they are `Output`, so the
default already makes them read-only and they need no entry.

**D14. A `varying` attribute is checked for an actual series before it is
editable (ruling 18).** `useCatalog` supplies `varying`; `networkApi.listTimeseries()`
(`api/network.ts:241`, backed by `routers/network.py:3018-3042`) supplies, per
component and attribute, the exact asset names that have one. A cell whose
attribute is `varying` **and** whose asset name appears in that list renders the
static value dimmed with a "series" badge, is not editable, and links to the
existing time-series flow. There is no clear-the-series action in this build.

`list_timeseries` currently iterates six components; `buses` and `transformers`
are added so the check covers every tab the grid renders. `carriers` has no `_t`
store and is skipped by the function's existing `getattr(..., None)` guard. The
deliberate side effect is that the Time-Series tab will also list bus and
transformer series that genuinely exist, which is correct, not a regression.

**D15. The grid shows absolute `r`/`x`/`b` with the unit in the column header;
the right panel keeps per-km (ruling 19).** Column headers use `COL_LABELS`
where an entry exists (it is curated and already carries units, e.g.
`v_nom: 'V nom (kV)'`) and otherwise the catalog `unit`, so Lines read `r (Ω)`,
`x (Ω)`, `b (S)`. Those three headers additionally carry an `InfoTip`
(`cardKit.tsx:52`, the one primitive immune to a scroll-container clip) reading
that the properties panel shows the same attributes per km. The split itself,
`SUBMIT_TRANSFORM` and the rescale behaviour are untouched.

**D16. `CarriersTable` is absorbed and deleted; tab names do not change.**
The `Carriers` tab renders through the shared grid. Two behaviours are carried
over rather than lost: the colour column keeps an `<input type="color">` editor
(the sole entry in a per-column widget map, keyed `Carrier.color`, matching
`BottomPanel.tsx:1204-1218`), and the tab keeps its help line
(`BottomPanel.tsx:1229-1234`). `Carrier` is already in `_COMPONENT_ATTRS`
(`network.py:277-288`), so bulk edits work on it. Tab labels stay exactly as they
are because a chat `ui_event` frame requests a bottom tab **by name**
(`2026-07-26-chat-compare-and-navigate-design.md:22,26`). The per-row deep link
into the asset results tab (`BottomPanel.tsx:533-542`) is preserved — it is one
of four entry points that spec depends on (`2026-07-31-asset-detail-results-design.md:336-341`).

**D17. `availableCols` stays derived from the data.** The catalog annotates
columns; it does not add them. A column absent from the DataFrame is 400-rejected
by `_bulk` ("has no column(s)"), so offering it would produce a guaranteed
failure. Per-tab visibility stays in `bottompanel:cols:<tab>` and `name` stays
pinned visible (`CLAUDE.md:560-561`). Newly-exposed attributes reach the grid by
being written once through the right panel (D20/D21), after which PyPSA
materialises the column.

**D18. The large-paste confirmation is a `confirmToast`, not a `Dialog`.**
It appears when the paste target exceeds **200 rows**. `confirmToast` is the
house answer for a consequential action (`CLAUDE.md:730`,
`2026-07-28-modal-a11y-primitive-design.md:127-129`) and it sidesteps
`Dialog.tsx:148`, whose capture-phase Escape would otherwise swallow the grid's
own Escape for as long as it is open.

**D19. Accessibility: native table semantics plus a roving tabindex.**
No `role="grid"`, `role="gridcell"`, `aria-rowindex` or `aria-colindex` is added —
the retained `<table>` markup already carries the correct native roles, and recon
§8 found no in-repo grid-a11y precedent to follow. Exactly one cell carries
`tabIndex=0` (the active cell) and every other carries `-1`, so focus stays on a
real element and the existing blur-commit and Escape handling work unchanged.
`aria-activedescendant` was rejected because it would require converting the
markup to explicit grid roles, which D1 rules out.

### Scope B — the parameter surface

**D20. Decision 9's extras section opens all three layers, on all eight forms.**
Rendering alone is a lie: an extras value would land in `form`, never reach
`payload`, and be overwritten by the `...current` spread at
`PropertiesPanel.tsx:144`. Each of the eight edit forms gets three one-line
changes:

1. **Seed** — `toFS(obj, [...CURATED_KEYS, ...extraKeys])` at the card's
   `startEdit` (Generator: `PropertiesPanel.tsx:206-212`), so an extras field
   opens showing its current value.
2. **Render** — `<ExtrasSection …/>` as the last child of `EditShell`
   (`PropertiesPanel.tsx:428, 642, 828, 987, 1299, 1643`), and inside
   `<Section title="Edit Parameters">` for `LinePanel` (`:1866`) and
   `TransformerPanel` (`:2170`), which use none of the shells.
3. **Save** — `Object.assign(payload, extrasPatch(form, extraKeys))` after the
   card's last unconditional assignment (Generator: after `:193`).

`extrasPatch` is a pure function exported from `cardKit.tsx` beside the existing
form-state helpers `toFS`/`nf`/`ni`/`no`; it is an addition to that file, not a
change to any existing export. No existing field's save semantics change.

**D21. The backend passthrough is catalog-whitelisted at the two generic CRUD
helpers.** Each Create model in `models/schemas.py` gains
`model_config = ConfigDict(extra='allow')` so unknown keys survive
`model_dump(exclude_unset=True)` instead of being silently dropped. The whitelist
then lands in exactly one place per operation — `_create_component` and
`_update_component` (`routers/network.py:199-224`), which
`pypsa-gui/README.md:214-218` already designates as the single home for
audit logging, undo and cleanup. A key survives only if
`services/attribute_catalog.py` reports it as an `Input` attribute of that
component class; anything else is dropped exactly as today. `extra='allow'`
without the whitelist would let an arbitrary key reach `n.add()`, which is why
the two changes ship together.

**D22. Six reveal rules in one table, mirroring the backend's actual logic.**
The table lives in `utils/attributeCatalog.ts`, each entry naming the backend
function it mirrors:

| # | When | Reveal | Require | Mirrors |
|---|---|---|---|---|
| 1 | `*_nom_extendable` is true | `*_nom_min`, `*_nom_max` | — | `_check_extendable_bounds` (`validation_service.py:364-402`) |
| 2 | `*_nom_extendable` is true | — | `capital_cost > 0` **OR** `overnight_cost > 0`, marked on the pair, reported only when both are unset or ≤ 0 | same |
| 3 | `*_nom_extendable` is true | — | `*_nom_min` and `*_nom_max` finite with min < max | same |
| 4 | `*_nom_extendable` is false | — | `*_nom > 0` | same |
| 5 | `committable` is true | the seven unit-commitment fields | — | already ships at `PropertiesPanel.tsx:411-419` |
| 6 | no bus in the network has `control == 'Slack'` | — | `Bus.control` marked required | `_check_pf` (`validation_service.py:337-359`) |

Rule 2 is a disjunction because the backend's is; a frontend that demanded
`capital_cost` alone would over-report against a network the solver accepts.
The table replaces the five derived booleans (`PropertiesPanel.tsx:225,226,521,725,1126`)
and the two inlined predicates (`:1934`, `:2191`) in the **edit** views, and it
also drives `CreationForm`'s render loop (`CreationForm.tsx:485`), which today
filters nothing — so the create and edit forms stop disagreeing about when
`p_nom_min`/`p_nom_max` are shown. The read view's separate null-out idiom
(`PropertiesPanel.tsx:234-235,264-269` relying on `Row`'s null guard at
`cardKit.tsx:149`) is left as it is; unifying it would mean rewriting six cards'
read paths, which the ledger cut.

**D23. "+ Add parameter" persists per palette type under
`creationform:extras:<paletteId>`.** House convention from recon §18: `:`
separator, feature-scoped namespace, dynamic segment last, every read and write
individually try/catch-wrapped. The value is `{ "v": 1, "keys": [...] }` and a
mismatched `v` drops the entry — versioning inside the value, never in the key
(`topologyLayoutStore.ts:19,41`). No regex sweep ships, because unlike
`network-diagram:*:state` this family is not project-scoped and has nothing to
clean up on project deletion. The picker lists the component's `Input` attributes
that the form does not already show, using the catalog's `description` as help
text and `unit` as the suffix.

**D24. The catalog query key is `['catalog', component]`, deliberately unscoped.**
`.cursor/rules/pypsa-gui-frontend.mdc:15-16` requires `nk(projectId, …)`; this is
a named exception on the same grounds as `['changelog']` (`BottomPanel.tsx:288`) —
PyPSA's attribute catalog is class-level metadata that is identical across
projects and cannot change at runtime, so project-scoping it would refetch nine
identical payloads on every project switch. `staleTime: Infinity` for the same
reason. The exception and its reason are recorded in a comment at the key.
The endpoint returns, per attribute: `name`, `type`, `unit` (`NaN` → `null`),
`default` (scrubbed by `clean_scalar`, so non-finite → `null`), `default_text`
(the exact text PyPSA holds, so `inf` is not erased), `description`, `status`,
`varying`, and `dtype` as its string name.

### Scope C — drop-on-a-bus

**D25. One `useAssetDrag` hook, and both canvases publish `data-bus-name`.**
The pointer-drag logic at `Sidebar.tsx:270-303` moves into the hook; the dead
`AssetPalette.tsx` copy is deleted, not migrated. On pointer-up the hook resolves
`document.elementFromPoint(x, y)` to one of three outcomes:

- `closest('[data-bus-name]')` → **a bus drop**, carrying that bus's name.
- else `closest('.react-flow')` → a schematic canvas drop (no bus).
- else `closest('.leaflet-container')` → a map canvas drop (no bus).
- else → cancelled silently, as today.

`data-bus-name` is emitted by `BusNode` in `TopologyCanvas.tsx` and by
`busDivIcon` in `MapCanvas.tsx:28-35`, which gains a `name` parameter; the name is
HTML-attribute-escaped in the `divIcon` HTML string. Using one attribute on both
canvases rather than React Flow's `data-id` avoids depending on React Flow's
internal markup and avoids having to distinguish `bus` from `assetGroup` nodes
(`TopologyCanvas.tsx:1786`) by class name.

**D26. Map drops carry no coordinates, and no global Leaflet handle is added.**
Decision 13 makes map drops prefill terminals only, so `containerPointToLatLng`
is never needed and there is no counterpart to `window.rfInstance` to build. The
third of recon §17's four missing map pieces therefore does not need building.

**D27. Terminal prefill covers 17 of 18 palette items and honours the field's
carrier filter.** `bus` is the terminal and has nothing to prefill; dropping a
`bus` on a bus behaves as a plain canvas drop. For the other 17, the prefilled
field is `bus` for the eleven single-terminal types and `bus0` for `line`,
`transformer`, `electrolyzer`, `fuel_cell`, `power_to_heat` and `chp` (recon §6).
The prefill is applied only if the target bus name is in
`filteredBusNames(bf.busCarrierFilter)` for that field — otherwise the form opens
unprefilled with the existing mismatch line ("No H₂ bus in network…" family,
`CreationForm.tsx:503-525`), so a hydrogen bus is never silently written into an
electricity-only terminal.

**D28. Schematic drops stop writing `x`/`y` (decision 14).**
`CreationForm.tsx:389-392` — the branch that seeds `init.x`/`init.y` from
`item.dropPosition` — is removed; `setPendingNodePosition` at `:449-451` is kept,
so the node still appears where it was dropped via the position cache. A bus
created by a drop therefore keeps the form's `'0'` defaults, lands as unplaced,
and is picked up by `UnplacedBusesPanel` exactly as
`2026-07-30-unplaced-buses-map-design.md` designed. No preflight rule and no
migration: `geo.ts:44-45` already quarantines the values, and this closes the
path that could otherwise write React Flow pixel coordinates into PyPSA's lon/lat
as a plausible-looking geographic position (recon §14 risk 13).

### Deletions

**D29. Five deletions, all measured dead or replaced.**

| Deleted | Evidence |
|---|---|
| `frontend/src/layout/AssetPalette.tsx` (294 lines) | Imported nowhere; also stale — its `hydrogen` item is labelled "P2G / Electrolysis" while `FIELD_MAP.hydrogen` is a StorageUnit (recon §0, §6) |
| `SimpleTable` in `BottomPanel.tsx:555-656` | No caller; its own comment calls it "legacy, retained for any future read-only callers" (recon §13) |
| `CarriersTable` in `BottomPanel.tsx:1063-1237` | Absorbed by D16 |
| The bulk-edit toolbar, `BottomPanel.tsx:433-470` with `onApply` `:300-315` | Replaced by paste-respects-selection and the Ctrl/Cmd+Enter fill gesture (ledger decision 5) |
| `FIELD_MAP`'s `link`, `generator` and `load` entries (`CreationForm.tsx:122-130, 156, 236-241`), and their sibling entries in `QUERY_KEY`, `COMPONENT_TYPE`, `AUTO_PREFIX` and `CREATE_FN` where present | Unreachable from the live UI: every `setCreationItem` caller reads `item.id` from `PALETTE_SECTIONS` or passes `null`; `generator` and `load` survive only because the dead `AssetPalette.tsx` lists them, and `link` is in neither palette (recon §6). They must go in the same change as `AssetPalette.tsx`, or deleting the palette alone leaves them merely unreferenced rather than wrong. |

### Testing

**D30. Characterization tests are task zero in each scope, before any edit.**
Recon's headline finding is that this repo has 82 frontend and 121 backend test
files and coverage stops precisely at this feature's boundary: `PATCH /_bulk`,
`BottomPanel.tsx`, `cardKit.tsx`, `PropertiesPanel.tsx` (beyond rescale),
`BusAutocomplete.tsx`, `CarrierSelect.tsx` and `DataGrid.tsx` have none.

| Scope | Written first | Pins |
|---|---|---|
| C | `layout/Sidebar.drag.test.tsx` | pointer-down/move/up over `.react-flow` sets `creationItem` with a `dropPosition`; a click without movement sets it without one; release outside cancels silently |
| A | `backend/tests/test_bulk_update.py` | rename refusal (`:1944-1945`); whole-batch 404 (`:1954-1957`); transient-row 409 (`:1967-1977`); unknown-column 400; the three-way blank rule (`:2015-2020`) on a `*_max`, on `lifetime`, on `e_sum_min` and on a plain numeric column; boolean string coercion; non-numeric 400; string-column cast; exactly one changelog entry per call |
| A | `layout/BottomPanel.test.tsx` | checkbox selection, shift-click range, select-all over the uncapped set, sort, search, the cap-splice at `:212-217`, the `truncated` notice. Needs the jsdom measurement stub declared above the component import, copied from `pages/results/asset/AssetTable.test.tsx:4-14` |
| B | `layout/PropertiesPanel.save.test.tsx` | the eight edit forms' save payloads — the enumerated keys are sent, and a field present in the cached object but absent from the enumeration survives at its old value (the `...current` behaviour at `:144`) |
| B | `layout/properties/cardKit.test.tsx` | `EditShell` renders arbitrary children into its 2-column grid and keeps the Save/Cancel footer — the seam D20 depends on |

New-module tests ship with their modules: `clipboardTsv.test.ts` (round-trip of
each row separator, the trailing-row rule, the three shapes and the four
rejection cases), `gridEdit.test.ts` (the blank rule, the infinity grammar, the
non-numeric rejection, and that `coerceForColumn` still owns the blank path),
`attributeCatalog.test.ts` (status default, both override entries, series-shadow
resolution, the six reveal rules), `hooks/useAssetDrag.test.tsx` (the four drop
outcomes), and `backend/tests/test_attribute_catalog.py` (endpoint payload
including `default_text` for an `inf` default and `null` for a `NaN` unit; the
whitelist accepting a catalog `Input` attribute and dropping a non-catalog key).

Two house rules apply throughout: `globals: false`, so every test imports its
own `describe`/`it`/`expect`/`vi` from `'vitest'` (`vite.config.ts:34-35`), and
no test may build its expectation by calling the function under test
(`2026-08-01-trustworthy-numbers-design.md:138-144`).

---

## Sequenced work, with the two canvases sized separately

**Scope C.** Schematic: one hit-test branch, one `data-bus-name` attribute on
`BusNode`, and the removal of the `x`/`y` seed — everything else it needs already
exists (`data-id` on nodes, node ids are bus names at `TopologyCanvas.tsx:2232`,
`screenToFlowPosition` pinned at `:2923-2924`). Map: three net-new pieces — the
drop must reach the Leaflet view at all (`Sidebar.tsx:287-288` returns early
today), `busDivIcon` must carry the bus name (`MapCanvas.tsx:28-35` emits a bare
`<div>` with a colour class), and the terminal-prefill path must work with no
coordinate conversion. D26 removes the fourth piece, the missing global Leaflet
handle. The map is a new drop surface; the schematic is a branch on an existing
one, and the estimate must not be uniform across them.

**Scope A.** The largest scope: three new modules, the grid's interaction layer,
the optimistic mutation, and the only backend contract change (D9) — all on files
with zero prior coverage, so the characterization tests above are a prerequisite
and not a nicety.

**Scope B.** Three one-line changes per edit form across eight forms, one reveal
table consumed by eight render sites (the seven edit forms that carry a rule,
plus `CreationForm`), one endpoint, one service module, one
Pydantic config change per Create model, and one whitelist at each of the two
generic CRUD helpers.

## Success criteria

Each item is independently verifiable.

1. Clicking a cell in any of the nine asset tabs opens an editor; Enter commits
   and moves down one row; Escape discards the draft **and leaves any open slide
   panel open**.
2. Committing a cell whose text is unchanged issues no HTTP request.
3. Copying a 3×2 region and pasting it back into the same region issues no HTTP
   request.
4. In a table of 3000 rows with all rows selected, pasting one value writes all
   3000 — including the rows past the 1000-row render cap — in exactly one
   `PATCH /_bulk` request.
5. Ctrl/Cmd+Enter on a cell edit with the same selection produces the same single
   request as item 4.
6. Pasting an N×1 column of distinct values onto N selected rows writes each row
   its own value, in exactly one request, and produces exactly one changelog
   entry.
7. Pasting a matrix whose row count differs from the selected row count changes
   nothing and reports both counts.
8. A paste containing one invalid value changes nothing, names the offending
   cell as `row / column`, and leaves the checkbox selection intact.
9. A paste whose target includes an `Output` column changes nothing and names
   that cell.
10. A mutation that fails restores the previous cell values and the previous
    checkbox selection, and displays the backend's `detail` as readable text
    rather than `[object Object]`.
11. Two pastes issued back to back produce two entries in `GET /network/undo/info`'s
    depth and two History rows; undoing once reverts only the second.
12. Copying a numeric column containing a negative number and pasting it back
    reproduces the same value byte-exactly.
13. A clipboard payload with `\r\n` separators and one with `\n` separators
    produce identical results, and neither creates a phantom trailing row.
14. Blanking a `p_nom_max` cell results in `n.generators.p_nom_max == inf`;
    blanking `e_sum_min` gives `-inf`; blanking `p_nom` gives `NaN`.
15. Typing `inf` into `p_nom_max` results in `inf`, and the request body carries
    the string `"inf"`.
16. Typing `12o0` into a numeric cell is rejected with a message and leaves the
    stored value unchanged.
17. A Generator with a `marginal_cost` time series renders that cell dimmed with
    a series badge, cannot be edited, and cannot be a paste target.
18. `Bus.control` is editable in the grid; `Generator.committable` is not; both
    appear in the override list with a written reason.
19. The Lines tab's `r` header reads `r (Ω)` and its tooltip states that the
    properties panel shows the value per km.
20. Pasting into more than 200 rows shows a `confirmToast`, and dismissing it
    changes nothing.
21. The Carriers tab renders in the shared grid, its colour cell still opens a
    colour picker, and the tab is still named `Carriers`.
22. Adding a catalog attribute through "+ Add parameter" on a Generator, saving,
    and reloading the project shows the saved value — proving the form seed, the
    payload builder and the Pydantic model were all opened.
23. The chosen extras persist across a reload under
    `creationform:extras:<paletteId>`, and a value whose `v` field is not `1` is
    discarded rather than read.
24. Ticking `p_nom_extendable` on a Generator with `capital_cost = 0` and
    `overnight_cost = 5` produces **no** required-field error; setting both to 0
    produces one naming the pair.
25. `p_nom_min` and `p_nom_max` are hidden in the **creation** form until
    `p_nom_extendable` is ticked, matching the edit form.
26. Dropping a Generator on a bus in the schematic view opens the creation form
    with `bus` prefilled to that bus's name.
27. Dropping a Generator on a bus in Satellite view does the same.
28. Dropping an Electrolyzer on a hydrogen bus leaves `bus0` empty and shows the
    existing carrier-mismatch line.
29. Dropping a Bus on the schematic canvas creates it with `x == 0 and y == 0`,
    and it appears in `UnplacedBusesPanel`.
30. `AssetPalette.tsx`, `SimpleTable`, `CarriersTable`, the bulk-edit toolbar and
    the three orphan `FIELD_MAP` entries are absent from the tree, and
    `npm run build` passes.
31. Reverting the whole-batch 404 in `bulk_update` fails a test; reverting the
    blank-to-`inf` rule fails a different test.
32. `pypsa-gui/frontend/src/utils/coerce.ts` is unchanged, and its ten existing
    tests pass unmodified.
33. Full suites green against the `c2cc4510` baseline: frontend 660 tests plus
    the new ones, 0 failures; backend 2183 passed / 23 skipped plus the new ones,
    0 failures, run in pixi's `test` env (the `default` env omits `pywebview` and
    yields 7 spurious failures).

## Out of scope

- Virtualising `AssetTable`, adopting `DataGrid.tsx`, and fixing `DataGrid`'s
  latent index bug (`GenerationStack.tsx:92` resolves a post-sort index against
  the raw array) or its unvalidated virtualisation. None is touched.
- Consolidating the four existing `status.str.startswith("Input")` call sites
  onto the new catalog service (D3).
- Unifying the edit view's `{cond && …}` idiom with the read view's null-out
  idiom (D22).
- De-duplicating the carrier grouping tables between `cardKit.tsx:598-604` and
  `components/CarrierSelect.tsx:47-50`. The grid consumes `CarrierSelect` rather
  than adding a third copy, which is the cheapest way to avoid making the
  documented debt worse without paying it off here.
- Clearing a time series from the grid (ruling 18 states this explicitly).
- Any change to `layout.json` persistence or the diagnosed-not-fixed node-drag
  revert (`docs/superpowers/findings/2026-07-31-blank-canvas-node-drags-revert.md`).
  Adjacent to D28's `x`/`y` write, but a different store.
- Number formatting for display. Cells render the raw value; the lossy unit-stepping
  helpers at `pages/results/shared.tsx:598-617` are deliberately not used, because
  round-tripping `€1.2 M` through a paste loses precision.

## Known limitations

**A blank numeric cell cannot be distinguished from `inf` or `NaN` in the
payload.** `clean_scalar` collapses all three to `null` before the grid sees
them. The grid therefore renders empty and states the per-column rule in the
header tooltip (D12). A consequence: copying a blank `p_nom` cell and pasting it
into a `p_nom_max` cell writes `inf`, not `NaN`. The no-op skip (D8) means this
never happens on a copy-and-paste-back of unchanged cells. This is a property of
the existing serializer and bulk endpoint, not something introduced here.

**A cell value containing a tab or a newline cannot round-trip through the
clipboard** (D6, no quote grammar). No PyPSA attribute in any of the nine tabs
holds such a value — names, carriers, booleans and numbers are the whole domain —
and a quote grammar would add an escaping surface for no measured need.

**The one-gesture-one-undo-step guarantee covers gestures that originate in the
grid.** The 500 ms wait (D11) is measured against the grid's own last mutation.
A canvas drag or a properties-panel save landing within 500 ms of a paste can
still coalesce with it, because the window is server-side and per-project. That
is today's behaviour for every surface and is not made worse.

**Ctrl/Cmd+S with a cell editor open saves the cell, not the project** (D5). The
user presses it again. The alternative — letting the save proceed with a PATCH
in flight — is the exact window `CLAUDE.md:650-666,812` prohibits.

**The reveal rules exist twice per card**: once in the shared table (edit view
and creation form) and once in each card's read-view `ExpandedItem[]` null-out
(D22). Unifying them means rewriting six cards' read paths, which the ledger cut.

**`Generator.committable` becomes uneditable from the bottom panel** (D13). It is
nominally editable there today through the bulk toolbar, but the endpoint's own
comment says that path does not support flipping it. This removes a capability
that did not work.

## Risks

**The only backend contract change lands in a declared hotspot.** D9 extends
`PATCH /_bulk` inside `routers/network.py` (4000+ lines,
`.cursor/rules/pypsa-gui-backend.mdc:27-29`), and multiple agent sessions share
this worktree. Mitigation: the characterization suite is written and green
before the route is touched; the coercion extraction is mechanical; the commit is
path-limited (`CLAUDE.md:702-712`), never `git add -A`; and `git branch --show-current`
plus `git status --porcelain` are checked immediately before it.

**Optimistic updates have no precedent in this codebase** (recon §4) and the
`nk()` parity trap turns a wrong `projectId` into `undefined`, which silently
wipes a payload (`queryKeys.ts:16-22`). D10 states the contract in full for that
reason, and item 10 of the success criteria is the test that proves rollback.

**`extra='allow'` widens every Create model at once** (D21). Without the
whitelist landing in the same change, an arbitrary key would reach `n.add()`.
The two are specified as one change and must ship as one commit.

**Six of the eight edit forms will be edited while uncovered until D30's
characterization tests land**, and `cardKit.tsx`'s 33 exports have no test at all
today. This is the single largest source of silent-regression risk in Scope B,
which is why B runs last and why the save-path pins come first.

**The desktop app is not current until it is rebuilt.** Any change reaching it
needs `npm run build` followed by `bash pypsa-gui/build-macos.sh` before the
`.app` or DMG can be called current (`CLAUDE.md:56-84`).
