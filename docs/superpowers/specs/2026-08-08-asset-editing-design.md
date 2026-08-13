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
| **Typed cell editor** | The input affordance a cell opens, chosen from the column's identity and catalog `dtype`: bus dropdown, carrier dropdown, closed-set dropdown, boolean checkbox, colour picker, `inf`-aware numeric, or plain text. | D4 |
| **Paste target** | The set of rows a paste writes to: the checkbox row selection if non-empty, otherwise the active cell's row. Rows past the render cap are included. | D7 |
| **Fill gesture** | One value applied to every cell of the paste target in the active column. Produced by a 1×1 paste, a Ctrl/Cmd+Enter commit, or a Ctrl/Cmd+click on a boolean cell. | D7 |
| **Block paste** | An N×M clipboard matrix mapped row-by-row and column-by-column onto the paste target. | D7 |
| **Series-shadowed cell** | A cell whose attribute is `varying` in the catalog *and* for which a time series exists on that specific asset, so the static value is dead. Never editable. | D14 |
| **Editability override list** | The short named list of attributes whose editability differs from their catalog `status`, each with its reason. Exactly two entries today. | D13 |
| **Extras section** | The appended block on an edit card holding attributes beyond the card's curated set. | D20 |
| **Terminal prefill** | Seeding `bus` (or `bus0`) on the creation form from the bus a palette item was dropped on. | D27 |
| **`data-bus-name`** | The DOM attribute, emitted by both canvases' bus renderers, that carries the bus name for drop hit-testing. | D25 |
| **Schematic canvas / map canvas** | The React Flow view (`.react-flow`, `TopologyCanvas.tsx`) and the Leaflet view (`.leaflet-container`, `MapCanvas.tsx`). `App.tsx:590` picks between them on `canvasView` (recon §0); the map's Satellite and Hybrid basemap modes (`MapModeSwitcher.tsx:6-10`, per `2026-07-30-unplaced-buses-map-design.md:17-19`) are one canvas, not two drop surfaces. | D25 |
| **Cap-splice** | The existing behaviour that injects the selected row into `displayed` when the selection falls past the 1000-row render cap, so `PropertiesPanel` stays in sync (`BottomPanel.tsx:212-217`, recon §13). | D1 |
| **Slide panel** | The right-hand panel the app opens for creation and properties. Navigation in this app is slide-panel based via `uiStore`, not routes (`.cursor/rules/pypsa-gui-frontend.mdc:24`); `App.tsx:478-481` closes the open one on Escape. | D5 |

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

**D4. One editor per cell, typed by column; one draft, one commit path.**
Decision 3 requires **typed widgets** — bus and carrier dropdowns, boolean
toggles, `inf`-aware numerics — and this decision is where each one is named.
Free-text entry into a bus column is the specific footgun being avoided: the
backend does not validate a bus *reference*, only a row *name*, so
`df.loc[names,'bus'] = 'Nrth'` (`network.py:2043-2044`) returns 200 and the
dangling reference surfaces only at preflight.

One editor is mounted at a time and a single click opens it; non-editing cells
render as text, as they do today. The draft stays a single `{ name, col, raw }`
where `raw` is the string the typed widget produced — not the flat draft map
`CarriersTable` uses (`BottomPanel.tsx:1065,1099`), because at the render cap
that would mount 1000 × 15 inputs, which is the DOM-node budget the cap exists to
protect (`BottomPanel.tsx:202-206`). Because a fresh editor mounts per cell, the
uncontrolled-input staleness rule (`CLAUDE.md:586-587`) is satisfied structurally
and no `key`-remount trick is needed. Commit on blur and on Enter; no round-trip
when the committed text equals the cell's current display text — the no-op skip
`BottomPanel.tsx:1122` already does.

**Editor resolution, evaluated in this order** for a cell that D13 and D14 have
already found editable:

| # | Test on (component class, column) | Editor | Commits |
|---|---|---|---|
| 1 | The pair is in the **widget map**, which holds two kinds of entry: `Carrier.color`, and the closed-option-set columns `Bus.control` and `Generator.control`. An entry may be added only for a column whose valid values are a closed set the app already enumerates elsewhere | `Carrier.color`: swatch + `<input type="color">` (preserves `BottomPanel.tsx:1204-1218`). Both `control` columns: native `<select>` over `PQ` / `PV` / `Slack`, the set `CreationForm.tsx:74`, `PropertiesPanel.tsx:1636` and `:346-360` already offer | the `#rrggbb` string; the option string verbatim |
| 2 | Column name matches `/^bus\d*$/` (`bus`, `bus0`, `bus1`, `bus2` — recon §6's terminal fields) | **`components/BusAutocomplete.tsx`**, with `allowUnknown={false}` | the selected bus name verbatim |
| 3 | Column name is `carrier` | **`components/CarrierSelect.tsx`** with `label={null}` and cell styling via its existing `className` / `wrapperClassName` props | the selected carrier name verbatim |
| 4 | Catalog `dtype` is boolean | checkbox rendered in the cell (see the exception below) | `'true'` / `'false'` |
| 5 | Catalog `dtype` is numeric | `inf`-aware text input (D12) | the typed token |
| 6 | otherwise | plain text input | the typed text |

Every editor commits a **string** into the same `gridEdit` validate-then-coerce
entry point, so there is exactly one commit path and the paste path (D7) and the
editor path share one validator. The widget is an input affordance; it is never
the thing that enforces correctness. `gridEdit`'s validators are pure and take an
explicit context — `{ catalog, busNames, carrierNames }` — supplied by the grid,
so the module stays React-free per D2.

**Per-column validation, applied identically to a typed commit, a fill and a
block paste:**

- **Bus columns:** the value must be an existing bus name, **exact and
  case-sensitive**. This is deliberately stricter than `BusAutocomplete`'s own
  `exactMatch`, which lower-cases both sides (`BusAutocomplete.tsx:26`); PyPSA's
  index lookup is case-sensitive, so a case-mismatched name is a dangling
  reference that no layer below would catch.
- **Carrier column:** an unknown carrier is **accepted**, because `bulk_update`
  calls `ensure_carrier(n, new_carrier)` and creates the row with catalog
  metadata. A paste can therefore introduce a carrier; the dropdown cannot, and
  does not need to — the Carriers tab is where carriers are created.
- **Boolean columns:** `true`/`false`/`1`/`0`/`yes`/`no`, case-**insensitive**,
  matching the backend's `value.strip().lower()` test rather than `coerce.ts`'s
  case-sensitive one; `gridEdit` lower-cases the token before delegating, so
  `coerce.ts` stays unmodified (D2).
- **Closed-set columns:** the value must be one of that column's options.
- **Numeric columns:** D12.

**Three adaptations to `BusAutocomplete.tsx`, each closing a hazard recon §15-E
measured**, all additive and leaving its single existing caller
(`CreationForm.tsx:514`) behaving exactly as it does today:

1. A new `allowUnknown?: boolean` prop, defaulting to `true`. The grid passes
   `false`, which converts the "No bus with this name — it will be created
   automatically" line (`:26,107-111`) into a validation refusal. That message is
   true for the creation form and false in a grid, where an unknown bus is a
   dangling reference rather than a new bus.
2. The fixed-position dropdown recomputes on `scroll` (capture, so it sees the
   table body) and on `resize` while open. Today it recomputes only on
   `[open, value]` (`:66`), so it would not follow the grid's scroll.
3. `ArrowUp`/`ArrowDown` call `stopPropagation()` in both dropdown states.
   Today neither branch does (`:43-53`, recon §7), so with the dropdown closed an
   arrow would reach the grid and move the active cell out from under an open
   editor — the one exception D5's "arrows never navigate while editing" needs
   made real.

Its outside-click `document` mousedown listener (`:29-34`) needs no change:
closing the dropdown is not closing the editor, and the editor still commits on
blur.

`CarrierSelect.tsx` is consumed **as-is**, with styling props only. Recon §15-E
measured it as the one kit-adjacent widget needing no structural change, it is a
native `<select>` whose OS-rendered popup cannot be clipped by the grid's scroll
container, and consuming it makes the grid the third consumer of one grouping
table rather than a fourth copy of it — the duplication `cardKit.tsx:598-604` and
`CarrierSelect.tsx:47-48` both document as debt.

**Two argued exceptions.**

- **`cardKit`'s `ChkInput` is not reused for the boolean cell.** Recon §15-D
  found it adaptable, but the two things that would have to go are its
  `col-span-2` layout (`:517`) and its `onCheck` side-effect hook (`:519-525`,
  meaningless where each cell commits independently) — and what remains is a bare
  `<input type="checkbox">`. Extracting a shared component out of a file with
  zero test coverage to share three lines is not worth it. The grid renders its
  own checkbox and `ChkInput` is untouched.
- **The boolean cell is always-on, with no click-to-edit step**, a stated
  exception to the one-editor-at-a-time rule above: a checkbox holds no draft, so
  there is nothing to open. The DOM cost is bounded by the number of boolean
  columns × 1000, which is at most two columns on any tab in `TAB_COLUMNS`.
  A plain click toggles the active cell only; **Ctrl/Cmd+click toggles it as a
  fill gesture** over the paste target, mirroring Ctrl/Cmd+Enter (D5) and
  preserving the deleted toolbar's set-many-booleans capability.

**D5. The keyboard map, and the one capture-phase Escape handler that has to be guarded.**

| Key | Editor closed | Editor open |
|---|---|---|
| Arrow keys | move the active cell | never navigate: move the caret inside a text input, or move the highlight in an open bus dropdown (D4 adaptation 3) |
| Enter | open the editor on the active cell | commit, close, move down one row |
| Ctrl/Cmd+Enter | — | commit as a **fill gesture** over the whole paste target, close |
| Tab / Shift+Tab | move the active cell right / left | commit, move right / left |
| Escape | clear the active cell marker | discard the draft, close |
| A printable character | open the editor seeded with that character | inserts normally |
| Ctrl/Cmd+C, Ctrl/Cmd+V | copy / paste (D6) | native input copy / paste |
| Ctrl/Cmd+S | not handled by the grid | commit the draft, `stopPropagation`, toast "Cell saved — press again to save the project" |

Escape-cancels-edit must survive the app's existing Escape handlers, and the two
phases need two different mechanisms. Stating which fixes which, because one of
them cannot work for the other:

- **Bubble-phase listeners are fixed by `stopPropagation()` alone, and no file
  changes.** `App.tsx:512` (which closes the compare rail or the open slide
  panel, `:478-481`), plus the unguarded Escape handlers in `TopologyCanvas`,
  `MapCanvas` and `ChatPanel`, all listen on `window`/`document` in the bubble
  phase. The editor's own React `onKeyDown` runs at the root container, which is
  an ancestor of the grid and a descendant of `document`, so it runs first and
  `stopPropagation()` prevents every one of them.
- **The one capture-phase Escape listener without a guard, `AppHeader.tsx:281`,
  must be guarded at the source.** Capture runs `window → document → … → target`,
  so it fires *before* the editor's handler no matter the registration order —
  `recon.md:443` states this outright. `stopPropagation()` is therefore useless
  against it. It gains the editable-element guard already present three lines
  away from its sibling at `App.tsx:485`: one inline condition using the in-file
  idiom, no new module and no arbitration layer.
- **`Dialog.tsx:148` is left alone.** It is capture-phase *and* calls
  `stopPropagation()`, so nothing the grid does can pre-empt it; D18 keeps the
  grid's only confirmation out of a `Dialog` instead.

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
  `=`, `+`, `-` or `@` is prefixed with a single quote (`CLAUDE.md:575-576`).
  On paste, exactly one leading single quote is stripped from a cell targeting a
  string column. Numeric and boolean columns are never prefixed, so negative
  numbers round-trip byte-exactly. The house rule's other two triggers, tab and
  CR, are deliberately **not** in the set: a cell value cannot contain either —
  it is the same fact that lets the parser above do without a quote grammar — so
  including them would add an unreachable branch, not protection.

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

A **fill gesture** is shape 1, a Ctrl/Cmd+Enter commit (D5) or a Ctrl/Cmd+click
on a boolean cell (D4), and is subject to the same rules. A paste never travels
through a typed editor; both paths meet at `gridEdit`'s validators (D4), so a
pasted bus name is checked against the real bus list exactly as a dropdown
selection is.

**D8. Rejection is whole-batch and names the offending cells.** A paste is
rejected, changing nothing, if any of the following holds: the shape does not
match (D7); a target cell is not editable (`name`, an `Output` attribute, an
override-list read-only entry, or a series-shadowed cell); or a value fails
`gridEdit` validation for its column (D4) — most consequentially a bus column
value that is not an existing bus name, which nothing below the frontend would
reject. The message names up to five offending
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
- The numeric cell editor — editor 5 of D4's six — is `type="text"`, not
  `type="number"`: recon §15-D measured that `<input type="number">` cannot hold
  `inf` and reads back `''`. It is the one typed widget decision 3 names that has
  no in-repo precedent; the other three are adapted or consumed under D4.

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
(row 1 of D4's editor-resolution table, keyed `Carrier.color`, matching
`BottomPanel.tsx:1204-1218`); its remaining columns —
`co2_emissions`, `nice_name`, `unit` — fall to rows 5 and 6 of that table.
The tab also keeps its help line
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

**What the two columns mean, stated once so every row is checkable.**
*Reveal* makes a field visible; it asserts nothing about the network and cannot
over-report, so it is never mode-gated. *Require* marks a field as blocking, and
it carries exactly one meaning throughout this table: **its absence produces a
backend `_err`, and `_err`s are what `has_errors` (`validation_service.py:1479-1480`)
blocks the solve on — warnings never block.** A rule that mirrors a `_warn` is
therefore not a require rule, and no row below is one.

| # | When | Reveal | Require | Mirrors |
|---|---|---|---|---|
| 1 | `*_nom_extendable` is true | `*_nom_min`, `*_nom_max` | — | `_check_extendable_bounds` (`validation_service.py:364-402`) |
| 2 | mode is `lopf` **and** `*_nom_extendable` is true | — | `capital_cost > 0` **OR** `overnight_cost > 0`, marked on the pair, reported only when both are unset or ≤ 0 | same, `_err` at `:398` |
| 3 | mode is `lopf` **and** `*_nom_extendable` is true | — | `*_nom_min` and `*_nom_max` finite with min < max | same, `_err` at `:388` |
| 4 | mode is `lopf` **and** `*_nom_extendable` is false | — | `*_nom > 0` | same, `_err` at `:381` |
| 5 | `committable` is true | the seven unit-commitment fields | — | already ships at `PropertiesPanel.tsx:411-419` |
| 6 | mode is `pf` **and** no Bus in the network has `control` equal to Slack | — | `control` marked required, network-wide (see below) | `_check_pf` `_err` at `:350`, gated at `:1448-1450` |

Rule 2 is a disjunction because the backend's is; a frontend that demanded
`capital_cost` alone would over-report against a network the solver accepts.

**Rules 2, 3 and 4 carry a mode condition, because their backend counterparts
do.** `_check_extendable_bounds` is reachable from exactly one caller —
`_check_lopf` (`validation_service.py:1231`, six call sites at `:1238, 1239,
1252, 1299, 1319, 1326`) — which the dispatcher runs only for `mode == "lopf"`
(`:1451`). In `pf` mode the backend never inspects `capital_cost` or the
`*_nom` bounds at all, so marking them required there would block the user on a
field the run does not need. The mode itself comes from one place for all four
mode-gated rules — the solver configuration the Solver Settings page already
loads — so the table has exactly one new data dependency, not four.
Rules 1 and 5 are reveals and are deliberately left
unconditional: they are existing shipped behaviour
(`PropertiesPanel.tsx:367-377, 411-419`), they make no claim, and gating them
would hide fields the user is editing.

**Rule 6's scope.** The backend's test is network-wide and satisfied by a single
bus: `n.buses["control"].astype(str).str.lower() == "slack"` with a non-empty
result (`validation_service.py:347-349`) — one match anywhere clears it, and the
comparison is case-insensitive. The marker therefore behaves network-wide, not
per bus: while the condition holds, `control` is marked required on **every** Bus
edit form and on the Buses tab's `control` column header, and it is never
attributed to one particular bus, because no particular bus is at fault. Setting
any one bus to `Slack` clears the marker on all of them in the same render. Two
conditions keep it from over-reporting: the configured solve mode must be `pf` —
read from the solver configuration the Solver Settings page already loads — and
it disappears the instant any bus is Slack.

**Rule 6 deliberately does not fire on the LOPF → AC-PF chain, and this was the
harder call.** When `mode == "lopf"` with `run_ac_pf_after_lopf` set, the
dispatcher runs `_check_stage2_ac_pf` (`:1120`, called at `:1456-1457`), *not*
`_check_pf`, and that check differs in both directions: it emits
`_warn("stage2_no_explicit_slack")` (`:1171`) rather than an `_err`, and it is
satisfied by **any** of a Slack generator, a Slack bus, or a non-blank
`ac_pf_slack_bus` override (`:1162-1170`). Ledger decision 10 is about the
variables a simulation needs in
order to launch; a warning never stops a launch, and the backend's own message
says Stage 2 auto-picks the largest generating bus. Firing a *required* marker
there would tell the user a run is blocked when it is not — round 1's I2
over-reporting failure one level down — and avoiding that would mean the
frontend also reading Slack generators and the override, two new data
dependencies bought for a marker that cannot block anything. Dropping the branch
keeps "required" meaning exactly one thing across all six rows. The advisory
itself is not lost: preflight already surfaces it in `IssuesPanel`, which is
where warnings belong.
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
that the form does not already show, one row each: `description` as the help
text, `unit` as the suffix, and `type` plus the attribute's default so the user
can see what they are adding before they add it. The default is displayed as
`default_text` whenever `default` is `null`, so an unbounded attribute reads
`inf` rather than blank. Adding a parameter never seeds a value — the field opens
empty and PyPSA's default continues to apply until the user types one.

**D24. The catalog query key is `['catalog', component]`, deliberately unscoped.**
`.cursor/rules/pypsa-gui-frontend.mdc:15-16` requires `nk(projectId, …)`; this is
a named exception on the same grounds as `['changelog']` (`BottomPanel.tsx:288`) —
PyPSA's attribute catalog is class-level metadata that is identical across
projects and cannot change at runtime, so project-scoping it would refetch nine
identical payloads on every project switch. `staleTime: Infinity` for the same
reason. The exception and its reason are recorded in a comment at the key.
The endpoint returns nine fields per attribute — seven of the catalog's nine
native columns, the attribute name from the index, and one derived text field —
each with a named consumer. The two native columns left out are `static` and
`typ`: nothing here reads them, and `dtype` already carries the type information
in a JSON-safe form.

| Field | Consumed by |
|---|---|
| `status` | D13's editability default |
| `varying` | D14's series-shadow check |
| `dtype`, as its string name | D4's editor resolution and D6's injection-guard scoping |
| `unit`, with `NaN` → `null` | D15's column headers and D23's picker suffix |
| `description` | D15's header `InfoTip` and D23's picker help text |
| `type` | D23's picker, so the user sees what kind of value an attribute takes |
| `default`, scrubbed by `clean_scalar` so non-finite → `null` | D23's picker |
| `default_text`, the exact text PyPSA holds | D23's picker, standing in whenever `default` is `null`, so an `inf` default is not erased into a blank |
| `name` | the key everything above is looked up by |

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
| A | `components/BusAutocomplete.test.tsx` | before D4's three adaptations: the type-ahead filter and its 60-result cap (`:23-24`), the case-insensitive `exactMatch` and its warning line (`:26,107-111`), the fixed-position dropdown geometry (`:61-66`), and the Up/Down handling (`:43-53`). Zero coverage today, and this is the widget the grid leans on hardest |
| A | `components/CarrierSelect.test.tsx` | before it gains a third consumer: the `<optgroup>` categories, `label={null}` omitting the label (`:26`), and the synthetic-current-option behaviour. Zero coverage today; consumed unchanged, so this pins that "unchanged" is true |
| B | `layout/PropertiesPanel.save.test.tsx` | the eight edit forms' save payloads — the enumerated keys are sent, and a field present in the cached object but absent from the enumeration survives at its old value (the `...current` behaviour at `:144`) |
| B | `layout/properties/cardKit.test.tsx` | `EditShell` renders arbitrary children into its 2-column grid and keeps the Save/Cancel footer — the seam D20 depends on |

New-module tests ship with their modules: `clipboardTsv.test.ts` (round-trip of
each row separator, the trailing-row rule, the three shapes and the four
rejection cases), `gridEdit.test.ts` (the blank rule, the infinity grammar, the
non-numeric rejection, the case-sensitive bus-membership check, the
case-insensitive boolean grammar, the accepted unknown carrier, and that
`coerceForColumn` still owns the blank path),
`attributeCatalog.test.ts` (status default, both override entries, the six
editor-resolution rows of D4, series-shadow
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
the typed cell editors of D4's six resolution rows — three additive adaptations
to `BusAutocomplete.tsx`, `CarrierSelect.tsx` consumed as-is, the colour cell
carried over from `CarriersTable`, and the checkbox, closed-set, numeric and text
cells written fresh, of which only the `inf`-aware numeric is substantive — the
optimistic mutation, and the only backend contract change (D9) — all on files
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
17. Clicking a `bus` cell on the Generators tab opens the `BusAutocomplete`
    dropdown listing existing bus names; scrolling the table body while it is
    open keeps the dropdown aligned to its cell.
18. Typing a bus name that does not exist into a `bus` cell is refused, the cell
    keeps its previous value, and no "it will be created automatically" line
    appears.
19. Pasting a bus name that differs from a real bus only in letter case changes
    nothing and names the cell.
20. Clicking a `carrier` cell opens the grouped carrier dropdown; pasting a
    carrier name that does not yet exist succeeds and that carrier appears in the
    Carriers tab.
21. A `p_nom_extendable` cell renders a checkbox; Ctrl/Cmd+clicking it applies its
    new value to every selected row in exactly one request.
22. A `control` cell offers exactly `PQ`, `PV` and `Slack`, and a paste of any
    other value into it changes nothing and names the cell.
23. Pressing ArrowDown inside an open `bus` cell moves the dropdown highlight and
    does not move the active cell.
24. A Generator with a `marginal_cost` time series renders that cell dimmed with
    a series badge, cannot be edited, and cannot be a paste target.
25. `Bus.control` is editable in the grid; `Generator.committable` is not; both
    appear in the override list with a written reason.
26. The Lines tab's `r` header reads `r (Ω)` and its tooltip states that the
    properties panel shows the value per km.
27. Pasting into more than 200 rows shows a `confirmToast`, and dismissing it
    changes nothing.
28. The Carriers tab renders in the shared grid, its colour cell still opens a
    colour picker, and the tab is still named `Carriers`.
29. Adding a catalog attribute through "+ Add parameter" on a Generator, saving,
    and reloading the project shows the saved value — proving the form seed, the
    payload builder and the Pydantic model were all opened.
30. The "+ Add parameter" picker shows each attribute's type, unit, description
    and default, and shows `inf` rather than a blank for `p_nom_max`.
31. The chosen extras persist across a reload under
    `creationform:extras:<paletteId>`, and a value whose `v` field is not `1` is
    discarded rather than read.
32. In `lopf` mode, ticking `p_nom_extendable` on a Generator with
    `capital_cost = 0` and `overnight_cost = 5` produces **no** required-field
    error; setting both to 0 produces one naming the pair; switching the mode to
    `pf` clears it, because the backend's extendable checks run only under
    `lopf`.
33. On a network with no Slack bus and a solve mode of `pf`, `control` is marked
    required on every Bus form; setting one bus to Slack clears it on all of
    them; switching the mode to `lopf` clears it, including when
    `run_ac_pf_after_lopf` is enabled.
34. `p_nom_min` and `p_nom_max` are hidden in the **creation** form until
    `p_nom_extendable` is ticked, matching the edit form.
35. Dropping a Generator on a bus in the schematic canvas opens the creation form
    with `bus` prefilled to that bus's name.
36. Dropping a Generator on a bus in the map canvas does the same.
37. Dropping an Electrolyzer on a hydrogen bus leaves `bus0` empty and shows the
    existing carrier-mismatch line.
38. Dropping a Bus on the schematic canvas creates it with `x == 0 and y == 0`,
    and it appears in `UnplacedBusesPanel`.
39. `AssetPalette.tsx`, `SimpleTable`, `CarriersTable`, the bulk-edit toolbar and
    the three orphan `FIELD_MAP` entries are absent from the tree, and
    `npm run build` passes.
40. Reverting the whole-batch 404 in `bulk_update` fails a test; reverting the
    blank-to-`inf` rule fails a different test.
41. `pypsa-gui/frontend/src/utils/coerce.ts` is unchanged, and its ten existing
    tests pass unmodified.
42. Full suites green against the `c2cc4510` baseline: frontend 660 tests plus
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
- Surfacing the Stage 2 AC-PF slack advisory (`stage2_no_explicit_slack`,
  `validation_service.py:1171`) as a form marker. It is a `_warn`, it is
  satisfied by three different signals, and preflight already reports it in
  `IssuesPanel`; D22 records why rule 6 stops at `pf` mode.
- De-duplicating the carrier grouping tables between `cardKit.tsx:598-604` and
  `components/CarrierSelect.tsx:47-50`. D4 consumes `CarrierSelect` for the
  grid's carrier cell rather than adding a third copy, which is the cheapest way
  to avoid making the documented debt worse without paying it off here.
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
and a quote grammar would add an escaping surface for no measured need. The same
fact is why D6's injection guard omits the house rule's tab and CR triggers:
those two arms would be unreachable, and an unreachable branch reads as
protection that is not there.

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
