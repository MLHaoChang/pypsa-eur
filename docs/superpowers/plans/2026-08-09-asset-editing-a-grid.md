# Asset editing — Scope A: the editable bottom grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every cell of every bottom-panel asset tab becomes editable in place, one value can be copied onto many assets through the system clipboard round-tripping with Excel, and the whole surface is driven by PyPSA's own attribute catalog rather than by sampled values.

**Architecture:** Three React-free modules carry the logic — `clipboardTsv.ts` (TSV wire format and paste-shape detection), `gridEdit.ts` (per-cell validate-then-coerce, wrapping the unmodified `coerce.ts`), and `attributeCatalog.ts` (editability, series shadow, headers, editor resolution). A new `GET /api/network/catalog/{component}` endpoint backed by `services/attribute_catalog.py` serves PyPSA's `n.components.<attr>.defaults` as JSON; `hooks/useCatalog.ts` caches it forever under a deliberately unscoped key. `AssetTable` in `BottomPanel.tsx` is extended in place — no virtualisation — gaining an active cell, a roving tabindex, one mounted editor at a time, and an optimistic `PATCH /_bulk` mutation. The endpoint gains an additive row-wise body form so a row-by-row paste is expressible in one request.

**Tech Stack:** React 19 + TypeScript 5.8 (strict), `@tanstack/react-query` 5 for the cache and the optimistic mutation, zustand 5 for `uiStore`, `react-hot-toast` for `confirmToast`, vitest 4.1.10 + jsdom 29 + Testing Library 16 on the frontend; FastAPI + PyPSA 1.1.2 + pandas + pytest on the backend.

---

## Plan set

This feature ships as **three plans**. Scope C has already landed.

| # | File | Scope | State |
|---|---|---|---|
| 1 | `docs/superpowers/plans/2026-08-09-asset-editing-c-drag-drop.md` | C — drop-on-a-bus | **Done**, commits `d93248ce`…`e8614a35` |
| 2 | `docs/superpowers/plans/2026-08-09-asset-editing-a-grid.md` (this file) | A — the editable grid | this plan |
| 3 | `docs/superpowers/plans/2026-08-09-asset-editing-b-parameter-table.md` | B — the parameter surface | not yet written; consumes this plan's `attributeCatalog.ts`, `useCatalog.ts` and catalog endpoint |

**This plan's base is `e8614a35`, not `c2cc4510`.** Scope C is an ancestor and
its five test files are part of the baseline this plan is measured against.

### Spec decision coverage — the decisions this plan owns

Source: `docs/superpowers/specs/2026-08-08-asset-editing-design.md`.

| Decision | Owned here | Task |
|---|---|---|
| D1 `AssetTable` extended in place, no virtualisation | yes | 11 (the constraint every grid task obeys) |
| D2 three pure modules + two hooks, `coerce.ts` unmodified | yes | 7, 8, 9 (`useAssetDrag.ts` was built in Scope C) |
| D3 one catalog endpoint + one service module | yes | 5 |
| D4 typed cell editors, one draft, one commit path | yes | 12 |
| D5 keyboard map + the guarded capture-phase Escape | yes | 11 |
| D6 clipboard I/O via `ClipboardEvent`, TSV wire format | yes | 9, 14 |
| D7 three paste shapes against the paste target | yes | 9, 14 |
| D8 whole-batch rejection naming offending cells | yes | 14 |
| D9 `PATCH /_bulk` gains an additive `rows` body form | yes | 6 |
| D10 optimistic write with rollback, exact cache contract | yes | 13 |
| D11 one paste/fill = one undo step | yes | 13 |
| D12 blank-and-infinity contract | yes | 8 (grammar), 12 (numeric editor) |
| D13 editability = catalog `status` + two overrides | yes | 7 |
| D14 `varying` attribute checked for a real series | yes | 5 (backend list), 7 (resolution) |
| D15 absolute `r`/`x`/`b` with unit headers | yes | 7 (labels), 11 (header render) |
| D16 `CarriersTable` absorbed and deleted | yes | 15 |
| D17 `availableCols` stays derived from the data | yes | 11 (a no-change decision; guarded by the two tests in `availableCols stays derived from the data`) |
| D18 large-paste confirmation is a `confirmToast` | yes | 14 |
| D19 native table semantics + roving tabindex | yes | 11 |
| D24 catalog query key `['catalog', component]`, nine payload fields | yes | 5, 7 |
| D29 five deletions | rows 2–4 here (`SimpleTable`, `CarriersTable`, the bulk toolbar) | 15, 16 |
| D30 characterization tests are task zero | yes, four of them | 1, 2, 3, 4 |

**Owned by Plan B, not here:** D20, D21, D22, D23. `attributeCatalog.ts` is
created in Task 7 with editability, series-shadow, header and editor
resolution; **D22's six reveal rules are appended to that same file by Plan B**
and are deliberately absent here. Task 7 leaves no stub for them — an empty
placeholder would be a plan failure; Plan B adds a new exported table.

### Success-criteria coverage

| Criteria | Task | Note |
|---|---|---|
| 1 open/commit/Escape, slide panel stays open | 11, 12 | Escape leaving the panel open is D5's guard |
| 2 unchanged text issues no request | 12 | the no-op skip |
| 3 copy-then-paste-back issues no request | 14 | every cell is a no-op |
| 4 3000 rows, one value, one request past the cap | 13, 14 | |
| 5 Ctrl/Cmd+Enter same single request | 12, 14 | |
| 6 N×1 distinct values, one request, one changelog entry | 6, 14 | the `rows` form |
| 7 row-count mismatch changes nothing, reports both counts | 9, 14 | |
| 8 one invalid value changes nothing, names `row / column`, selection intact | 14 | |
| 9 `Output` column in target changes nothing, names the cell | 14 | |
| 10 failure restores values **and** selection, readable `detail` | 13 | |
| 11 two pastes → depth 2, two History rows, undo reverts only the second | 13 | the 500 ms wait |
| 12 negative number round-trips byte-exactly | 9 | injection guard skips numeric columns |
| 13 `\r\n` and `\n` identical, no phantom trailing row | 9 | |
| 14 blank `p_nom_max`→`inf`, `e_sum_min`→`-inf`, `p_nom`→`NaN` | 1, 8 | pinned in Task 1 first |
| 15 `inf` typed → `inf`, body carries `"inf"` | 8, 12 | |
| 16 `12o0` rejected, stored value unchanged | 8 | |
| 17 bus cell opens the dropdown; scrolling keeps it aligned | 10, 12 | |
| 18 unknown bus refused, no "created automatically" line | 10, 12 | |
| 19 case-differing bus name changes nothing, names the cell | 8, 14 | |
| 20 carrier cell dropdown; pasting a new carrier succeeds | 8, 12 | |
| 21 `p_nom_extendable` checkbox; Ctrl/Cmd+click fills selection in one request | 12, 14 | |
| 22 `control` offers exactly PQ/PV/Slack; other value rejected | 8, 12 | |
| 23 ArrowDown in an open bus cell moves the highlight, not the active cell | 10, 11 | |
| 24 series-shadowed cell dimmed, badged, uneditable, not a paste target | 5, 7, 12 | |
| 25 `Bus.control` editable, `Generator.committable` not, both with reasons | 7 | |
| 26 `r (Ω)` header + per-km tooltip | 7, 11 | |
| 27 >200 rows shows a `confirmToast`; dismissing changes nothing | 14 | |
| 28 Carriers renders in the shared grid, colour picker survives, tab still named `Carriers` | 15 | |
| 39 the five deletions absent + `npm run build` passes | 16 | rows 2–4 here; rows 1 and 5 landed in Scope C |
| 40 reverting the whole-batch 404 / the blank-to-`inf` rule each fails a test | 1 | |
| 41 `coerce.ts` unchanged, its ten tests pass unmodified | 8, 16 | asserted by `git diff` in Task 16 |
| 42 full suites green | 16 | **against the corrected baseline below** |

**Covered by no task in this plan:** criteria 29–38 (Scope B and Scope C).

---

## Global Constraints

Every task's requirements implicitly include this section.

**Paths contain a space.** The worktree is
`/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing`.
Quote every path in every shell command.

**Branch and commits.** Work only on `feature/asset-editing` in that worktree.
Multiple agent sessions share this repo (`CLAUDE.md:702-712`). Before every
commit run `git branch --show-current` and `git status --porcelain`. Commit with
a **path-limited** `git add <paths>` naming only the files the task lists —
**never `git add -A`**. Never `git stash`: the stash ref is shared between
worktrees. Never touch `/Users/orange/Desktop/Code Test/pypsa-eur-assistant`.

**Toolchain — Node is NOT on the default PATH.** This worktree has no
`.pixi/envs/`. Borrow the main checkout's **`test`** environment — the only one
carrying node, npm, npx, python, pytest, ruff *and* `pywebview`:

```bash
PIXI_BIN="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/test/bin"
```

Frontend, from `<worktree>/pypsa-gui/frontend`:

```bash
PATH="$PIXI_BIN:$PATH" npm test                                   # == "vitest run"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/gridEdit.test.ts   # one file
PATH="$PIXI_BIN:$PATH" npm run build                              # == "tsc -b && vite build"
```

`npm run build` is the **only** type-check in the project — there is no
`typecheck` script, no ESLint and no Prettier anywhere in the repo.

Backend, from `<worktree>/pypsa-gui/backend`:

```bash
"$PIXI_BIN/python" -m pytest tests/test_bulk_update.py -v     # one file
"$PIXI_BIN/python" -m pytest                                  # whole suite
"$PIXI_BIN/python" -m ruff check <file>                       # backend lint
```

- **The backend suite MUST run in pixi's `test` environment, not `default`.**
  `default` omits `pywebview` **by design** (`pixi.toml:318-325`).
- **Never pass an extra `-q`.** `pypsa-gui/backend/pytest.ini:15` already sets
  `addopts = -q`. A second `-q` stacks to `-qq` and **suppresses the
  `N passed in Xs` summary line**. Use `-v` for one file and no flag for the
  whole suite.

**Baseline at `e8614a35`, measured on this worktree — use these numbers, not the spec's.**

| Suite | Measured at `e8614a35` | The spec / ledger say | Why they differ |
|---|---|---|---|
| Frontend | **87 files, 691 tests, 0 failures** | 82 / 660 at `c2cc4510` | Scope C added 5 files and 31 tests |
| Backend | **2286 passed, 18 skipped, 0 failures** | 2183 passed / 23 skipped | **The recorded baseline is stale.** `git diff c2cc4510 e8614a35 -- pypsa-gui/backend` is EMPTY and Scope C changed zero backend files, so the same source produces both numbers. Measured, not inherited. |

Success criterion 42 names `2183 / 23`; it is wrong for the same reason and
should be read as "0 failures against the measured baseline". Do not treat a
count of 2286 as a regression.

**Test-writing house rules.**
- `vite.config.ts:34-35` sets `globals: false`. **Every test file imports
  `describe` / `it` / `expect` / `vi` from `'vitest'`.**
- **`@testing-library/jest-dom` is NOT installed.** Use plain vitest matchers
  (`expect(el.textContent).toBe(…)`), never `toBeInTheDocument()`.
- Mock network access with `vi.mock('<rel>/api/<module>', async importOriginal
  => …)`. msw is not installed; there are zero `vi.stubGlobal('fetch', …)` calls.
- A test must never build its expectation by calling the function under test
  (`2026-08-01-trustworthy-numbers-design.md:138-144`).
- Frontend tests are co-located: `Foo.test.tsx` beside `Foo.tsx`. There are no
  `__tests__` directories.
- Backend tests are flat in `pypsa-gui/backend/tests/`, use the `client` and
  `install_network` fixtures and the module-level `build_network` helper from
  `tests/conftest.py`.

**jsdom capability facts, measured in this worktree** — assume otherwise and
each costs an hour:
- `ClipboardEvent` and `DataTransfer` are **undefined**. A copy/paste event has
  to be built by hand — Task 9 supplies the exact helper.
- `document.elementFromPoint` is **undefined** (Scope C's finding; not needed here).
- `PointerEvent` **is** defined.
- Every measured box reports 0. A component that measures itself needs the
  `beforeAll` stub copied from `pages/results/asset/AssetTable.test.tsx:8-14`,
  declared **above** the component import.
- `localStorage` is a bare `{}`, not a `Storage` — `getItem`/`setItem`/`clear`
  all throw (`CLAUDE.md`). `BottomPanel`'s `loadVisible`/`saveVisible` already
  wrap every access in try/catch, so the component survives; **a test must never
  assert on localStorage**, only on rendered output.
- A React state update fired from a **non-React** listener (`window`,
  `document`, or a `ClipboardEvent`) is not flushed before the next assertion.
  Wrap the dispatch in `act()`. Assertions against zustand or React Query state
  need no wrapper — those stores update synchronously.

**TypeScript.** `strict: true`; `noUncheckedIndexedAccess` is **off**;
`types: []`. The `@/*` path alias is TypeScript-only and **non-functional at
runtime** — use relative imports, as every existing file does. `as never` cannot
be spread into JSX props (TS2698); use `as unknown as <Props>`.

**House idioms that are not optional.**
- Modifier keys: `const modifier = e.ctrlKey || e.metaKey` (`App.tsx:491`).
- Query keys: `nk(projectId, root)` returns `[root, projectId, ...rest]`
  (`utils/queryKeys.ts:23-25`). In a non-React callback read the id via
  `useUIStore.getState().currentProject` — a mismatched id makes `getQueryData`
  return `undefined` and silently wipes a payload (`queryKeys.ts:16-22`).
- FastAPI error arrays are formatted into readable strings before display,
  never `String([{…}])` (`.cursor/rules/pypsa-gui-frontend.mdc:19`).
- `routers/network.py` is a declared change hotspot
  (`.cursor/rules/pypsa-gui-backend.mdc:27-29`) — surgical edits only.
- Backend service logic lives in `services/`, routes stay thin
  (`.cursor/rules/pypsa-gui-backend.mdc:10-12`).
- The desktop app is not current until `npm run build` then
  `bash pypsa-gui/build-macos.sh` (`CLAUDE.md:56-84`). No task in this plan
  claims the `.app` is current.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pypsa-gui/backend/tests/test_bulk_update.py` (new) | Characterization: pins every guarantee of `PATCH /_bulk` before it is touched | 1 |
| `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (new) | Characterization: selection, shift-range, select-all past the cap, sort, search, cap-splice, truncation notice | 2 |
| `pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx` (new) | Characterization: filter + 60-cap, case-insensitive `exactMatch` and its warning, dropdown geometry, Up/Down | 3 |
| `pypsa-gui/frontend/src/components/CarrierSelect.test.tsx` (new) | Characterization: `<optgroup>` categories, `label={null}`, synthetic current option | 4 |
| `pypsa-gui/backend/services/attribute_catalog.py` (new) | The only reader of `n.components.<attr>.defaults` for this feature; builds the nine-field payload | 5 |
| `pypsa-gui/backend/tests/test_attribute_catalog.py` (new) | Payload shape, `default_text` for an `inf` default, `null` unit, unknown component 400 | 5 |
| `pypsa-gui/backend/routers/network.py` — new `GET /catalog/{component}` route; `list_timeseries` gains two components | Thin route + D14's coverage | 5 |
| `pypsa-gui/backend/routers/network.py:1931-2053` — `bulk_update` | Additive `rows` body form; coercion extracted to a module-level helper | 6 |
| `pypsa-gui/frontend/src/hooks/useCatalog.ts` (new) | `useQuery` over the catalog endpoint, unscoped key, `staleTime: Infinity` | 7 |
| `pypsa-gui/frontend/src/utils/attributeCatalog.ts` (new) | Editability + override list, series-shadow resolution, header labels, editor resolution | 7 |
| `pypsa-gui/frontend/src/utils/attributeCatalog.test.ts` (new) | The override list, the six editor rows, series shadow, header labels | 7 |
| `pypsa-gui/frontend/src/utils/gridEdit.ts` (new) | Per-column validate-then-coerce; wraps `coerceForColumn`; the infinity grammar | 8 |
| `pypsa-gui/frontend/src/utils/gridEdit.test.ts` (new) | Blank rule, infinity grammar, non-numeric rejection, case-sensitive bus check, case-insensitive booleans, unknown carrier accepted | 8 |
| `pypsa-gui/frontend/src/utils/clipboardTsv.ts` (new) | TSV parse/serialise, injection guard, paste-shape resolution | 9 |
| `pypsa-gui/frontend/src/utils/clipboardTsv.test.ts` (new) | Row-separator round-trip, trailing-row rule, three shapes, four rejections | 9 |
| `pypsa-gui/frontend/src/components/BusAutocomplete.tsx` | Three additive adaptations: `allowUnknown`, scroll/resize reposition, arrow `stopPropagation` | 10 |
| `pypsa-gui/frontend/src/layout/AppHeader.tsx:273-279` | The capture-phase Escape gains the editable-element guard | 11 |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — `AssetTable` | Active cell, roving tabindex, keyboard map, header labels | 11 |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — `CellEditor` | The six typed editors, one mounted at a time | 12 |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — `useGridMutation` | Optimistic write, rollback, the 500 ms undo spacing | 13 |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — copy/paste handlers | `ClipboardEvent` wiring, whole-batch rejection, `confirmToast` | 14 |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — Carriers tab | Renders through the shared grid; `CarriersTable` deleted | 15 |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx:555-656` `SimpleTable`, `:433-470` toolbar | **deleted** | 16 |

---

## Task 1: Characterize `PATCH /_bulk` before the contract changes

**Files:**
- Test: `pypsa-gui/backend/tests/test_bulk_update.py` (create)

**Interfaces:**
- Consumes: `client` and `install_network` fixtures and the module-level
  `build_network()` helper from `tests/conftest.py`.
- Produces: nothing importable. This task's product is the safety net Task 6
  edits under. Task 6 is judged partly on these staying green.

**Context the implementer needs.** `bulk_update` (`routers/network.py:1931-2053`)
has **zero test coverage** and Task 6 edits it. Nine guarantees are load-bearing
and each is a separate failure mode: bulk rename refusal (`:1944-1945`),
whole-batch 404 on any unknown name (`:1954-1957`), 409 on transient rows
(`:1967-1977`), 400 on an unknown column (`:1982-1985`), the three-way blank
rule (`:2015-2020`), boolean string coercion (`:1996-2002`), the non-numeric 400
(`:2024-2027`), the string-column cast (`:2031-2032`), and exactly one changelog
entry per call (`:2049-2052`).

**The three-way blank rule is testable on Generator alone**, which is not
obvious and is worth stating because guessing wrong costs a run:

| Column | Branch | Result |
|---|---|---|
| `p_nom_max` | `col.endswith("_max")` | `inf` |
| `lifetime` | `col == "lifetime"` | `inf` |
| `e_sum_min` | `col == "e_sum_min"` | `-inf` |
| `p_nom` | neither | `NaN` |

`e_sum_min` is a **Generator** attribute in PyPSA 1.1.2 with a `-inf` default —
measured, not assumed. It is **not** on `Store`, so a test that reaches for a
Store here gets a 400 from the unknown-column check at `:1982` and proves
nothing.

The route is mounted under `/api/network` (`main.py:753`), so the URL is
`PATCH /api/network/_bulk`. The changelog is read at `GET /api/changelog/`
(trailing slash — `routers/changelog.py:20`).

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/backend/tests/test_bulk_update.py`:

```python
"""
Characterization of PATCH /api/network/_bulk, written BEFORE the row-wise body
form is added (spec D9, D30).

The endpoint has zero coverage today and every guarantee below is load-bearing:
a partial application is unrecoverable, and the blank-to-sentinel rule is the
only thing that turns "the user cleared a bound" into PyPSA's ±inf rather than
a NaN the solver reads as missing.

Measured facts these tests depend on (PyPSA 1.1.2):
  * `e_sum_min` is a GENERATOR attribute (default -inf), not a Store one.
  * `lifetime` and `p_nom_max` both resolve to +inf when blanked, by two
    different branches of the same if/elif.
"""
from __future__ import annotations

import math

import pypsa
import pytest

from tests.conftest import build_network

BULK = "/api/network/_bulk"


@pytest.fixture
def net(install_network):
    """A two-generator network, installed as the live singleton."""
    n = build_network()
    install_network(n)
    return n


def test_rename_is_refused(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"name": "gas2"},
    })
    assert r.status_code == 400
    assert "rename" in r.json()["detail"].lower()
    assert "gas" in net.generators.index


def test_unknown_name_rejects_the_whole_batch(client, net):
    before = float(net.generators.at["gas", "p_nom"])
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "ghost"],
        "updates": {"p_nom": 999.0},
    })
    assert r.status_code == 404
    assert "ghost" in r.json()["detail"]
    # The whole batch is refused — "gas" must be untouched.
    assert float(net.generators.at["gas", "p_nom"]) == before


def test_unknown_column_is_400(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"p_min_pu ": 0.5},          # trailing space, a real typo
    })
    assert r.status_code == 400
    assert "no column" in r.json()["detail"].lower()


def test_unknown_component_class_is_400(client, net):
    r = client.patch(BULK, json={
        "component_class": "Widget", "names": ["gas"], "updates": {"p_nom": 1.0},
    })
    assert r.status_code == 400
    assert "Widget" in r.json()["detail"]


def test_transient_rows_are_409(client, net, monkeypatch):
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(
        PyPSAService, "get_transient_rows",
        staticmethod(lambda cls: {"gas"} if cls == "Generator" else set()),
    )
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"p_nom": 1.0},
    })
    assert r.status_code == 409
    assert "scaffolding" in r.json()["detail"].lower()


@pytest.mark.parametrize("col,expected", [
    ("p_nom_max", math.inf),     # endswith("_max")
    ("lifetime", math.inf),      # == "lifetime"
    ("e_sum_min", -math.inf),    # == "e_sum_min"
])
def test_blanking_a_bound_writes_its_sentinel(client, net, col, expected):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {col: None},
    })
    assert r.status_code == 200
    assert float(net.generators.at["gas", col]) == expected


def test_blanking_a_plain_numeric_writes_nan(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom": None},
    })
    assert r.status_code == 200
    assert math.isnan(float(net.generators.at["gas", "p_nom"]))


def test_empty_string_takes_the_same_blank_path_as_null(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom_max": ""},
    })
    assert r.status_code == 200
    assert math.isinf(float(net.generators.at["gas", "p_nom_max"]))


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True),
    ("false", False), ("FALSE", False), ("0", False), ("no", False),
])
def test_boolean_strings_coerce_case_insensitively(client, net, raw, expected):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"p_nom_extendable": raw},
    })
    assert r.status_code == 200
    assert bool(net.generators.at["gas", "p_nom_extendable"]) is expected


def test_non_numeric_into_a_numeric_column_is_400(client, net):
    before = float(net.generators.at["gas", "p_nom"])
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom": "12o0"},
    })
    assert r.status_code == 400
    assert "non-numeric" in r.json()["detail"].lower()
    assert float(net.generators.at["gas", "p_nom"]) == before


def test_inf_string_is_accepted_by_float(client, net):
    # The grid sends the STRING "inf" for an infinity token (spec D12); this
    # pins that the endpoint's float(value) already parses it, so D12 needs no
    # backend change.
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom_max": "inf"},
    })
    assert r.status_code == 200
    assert math.isinf(float(net.generators.at["gas", "p_nom_max"]))


def test_number_into_a_string_column_is_cast_to_str(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"carrier": 42},
    })
    assert r.status_code == 200
    assert net.generators.at["gas", "carrier"] == "42"


def test_setting_carrier_creates_the_carrier_row(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"carrier": "brand_new_carrier"},
    })
    assert r.status_code == 200
    assert "brand_new_carrier" in net.carriers.index


def test_one_call_writes_exactly_one_changelog_entry(client, net):
    before = len(client.get("/api/changelog/").json())
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "solar"],
        "updates": {"p_nom": 123.0, "marginal_cost": 7.0},
    })
    assert r.status_code == 200
    entries = client.get("/api/changelog/").json()
    # Two rows and two fields, still exactly ONE audit entry.
    assert len(entries) == before + 1


def test_response_reports_row_count_and_field_names(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "solar"],
        "updates": {"p_nom": 5.0},
    })
    assert r.status_code == 200
    assert r.json() == {"updated": 2, "fields": ["p_nom"]}


def test_every_named_row_receives_the_value(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "solar"],
        "updates": {"p_nom": 77.0},
    })
    assert r.status_code == 200
    assert float(net.generators.at["gas", "p_nom"]) == 77.0
    assert float(net.generators.at["solar", "p_nom"]) == 77.0


def test_empty_names_and_empty_updates_are_400(client, net):
    assert client.patch(BULK, json={
        "component_class": "Generator", "names": [], "updates": {"p_nom": 1.0},
    }).status_code == 400
    assert client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {},
    }).status_code == 400


def test_carrier_class_is_bulk_editable(client, install_network):
    # D16 absorbs the Carriers tab into the shared grid, which requires that
    # Carrier is a valid component_class here. It already is.
    n = pypsa.Network()
    n.add("Carrier", "gas", co2_emissions=0.2)
    install_network(n)
    r = client.patch(BULK, json={
        "component_class": "Carrier", "names": ["gas"],
        "updates": {"co2_emissions": 0.5},
    })
    assert r.status_code == 200
    assert float(n.carriers.at["gas", "co2_emissions"]) == 0.5
```

- [ ] **Step 2: Run it and confirm every test passes against unmodified source**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_bulk_update.py -v
```

Expected: all tests pass, no failures, no errors. These are characterization
tests: they pin behaviour that already exists, so they must pass on the **first**
run. A failure here means the test is wrong, not the endpoint. Do **not** "fix"
`routers/network.py` to make them pass.

If `test_transient_rows_are_409` errors on the monkeypatch, check whether
`get_transient_rows` is a `staticmethod` or a `classmethod` on `PyPSAService`
and match it; the assertion itself is correct either way.

- [ ] **Step 3: Lint and commit**

```bash
"$PIXI_BIN/python" -m ruff check tests/test_bulk_update.py
```

Expected: `All checks passed!`

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current    # must print: feature/asset-editing
git status --porcelain
git add pypsa-gui/backend/tests/test_bulk_update.py
git diff --cached --name-only
git commit -m "test(gui): characterize PATCH /_bulk before it gains a row-wise form"
```

---

## Task 2: Characterize the bottom panel's selection, sort and cap behaviour

**Files:**
- Test: `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing importable. Tasks 11–16 all edit `AssetTable`; this is what
  notices if one of them breaks selection, sort, search or the render cap.

**Context the implementer needs.** `BottomPanel.tsx` has **zero coverage** and
D1 keeps every one of these behaviours while Tasks 11–16 rebuild the cell layer
around them. The seven behaviours to pin (`BottomPanel.tsx` line numbers):

| Behaviour | Where |
|---|---|
| Checkbox selection toggles one row | `:237-256` |
| Shift-click selects the inclusive range from the last anchor | `:242-249` |
| Select-all covers the **uncapped** `sorted` array, not `displayed` | `:258-262` |
| Sort toggles asc/desc and re-sorts with `localeCompare({numeric:true})` | `:190-200, 222-225` |
| Search filters on `name` substring, case-insensitive | `:181-188` |
| Cap-splice injects the selected row when it falls past the cap | `:208-219` |
| The `truncated` notice appears above the cap | `:220, 423-430` |

`AssetTable` is **not exported** — the file's only export is the default
`BottomPanel`. Rather than change the export surface for a test (which Task 11
would then have to keep), this test renders `BottomPanel` and drives it through
the real UI, mocking the nine `networkApi` getters it calls at `:902-910`.

`BottomPanel` reads `localStorage` through `loadVisible`/`saveVisible`
(`:81-94`), both already try/catch-wrapped, so jsdom's bare-`{}` `localStorage`
is survivable — but **do not assert on it**.

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx`:

```tsx
// Characterization of AssetTable's selection / sort / search / render-cap
// behaviour, written BEFORE the editable cell layer is built on top of it
// (spec D1, D30). BottomPanel.tsx has zero coverage today and Tasks 11-16 all
// edit this component.
//
// AssetTable is not exported, so these drive it through the real BottomPanel
// with the nine network getters mocked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getLines: vi.fn(), getLinks: vi.fn(),
      getTransformers: vi.fn(), getGenerators: vi.fn(), getLoads: vi.fn(),
      getStorageUnits: vi.fn(), getStores: vi.fn(), getCarriers: vi.fn(),
      bulkUpdate: vi.fn(),
    },
  }
})

import { networkApi } from '../api/network'
import BottomPanel from './BottomPanel'

/** n buses named "B0".."B(n-1)" with a descending v_nom so sort is observable. */
function buses(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    name: `B${i}`, v_nom: 380 - i, carrier: 'AC', x: 0, y: 0,
    country: '', unit: '', control: 'PQ', sub_network: '',
  }))
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <BottomPanel />
    </QueryClientProvider>,
  )
}

/** Every data row's checkbox, in render order. Index 0 is the header's. */
function rowCheckboxes(): HTMLInputElement[] {
  return Array.from(
    document.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]'),
  )
}

beforeEach(() => {
  const api = vi.mocked(networkApi)
  api.getBuses.mockReset().mockResolvedValue(buses(5) as never)
  for (const fn of [api.getLines, api.getLinks, api.getTransformers,
    api.getGenerators, api.getLoads, api.getStorageUnits, api.getStores,
    api.getCarriers]) {
    fn.mockReset().mockResolvedValue([] as never)
  }
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

describe('AssetTable selection — behaviour as of e8614a35', () => {
  it('a checkbox click selects exactly that row', async () => {
    renderPanel()
    const boxes = await screen.findAllByRole('checkbox')
    // The first checkbox is the header select-all; row boxes follow.
    await userEvent.click(rowCheckboxes()[1])
    expect(screen.getByText(/1 selected/)).toBeTruthy()
    expect(boxes.length).toBeGreaterThan(1)
  })

  it('shift-click selects the inclusive range from the previous anchor', async () => {
    renderPanel()
    await screen.findAllByRole('checkbox')
    const boxes = rowCheckboxes()
    await userEvent.click(boxes[0])
    fireEvent.click(boxes[3], { shiftKey: true })
    // Rows 0,1,2,3 inclusive.
    expect(screen.getByText(/4 selected/)).toBeTruthy()
  })

  it('select-all covers every row', async () => {
    renderPanel()
    const header = (await screen.findAllByRole('checkbox'))[0]
    await userEvent.click(header)
    expect(screen.getByText(/5 selected/)).toBeTruthy()
  })

  it('a second select-all click clears the selection', async () => {
    renderPanel()
    const header = (await screen.findAllByRole('checkbox'))[0]
    await userEvent.click(header)
    await userEvent.click(header)
    expect(screen.queryByText(/selected/)).toBeNull()
  })
})

describe('AssetTable search and sort — behaviour as of e8614a35', () => {
  it('search filters rows by a case-insensitive name substring', async () => {
    renderPanel()
    await screen.findByText('B0')
    const search = document.querySelector(
      'input[placeholder*="earch" i]',
    ) as HTMLInputElement
    await userEvent.type(search, 'b3')
    expect(screen.getByText('B3')).toBeTruthy()
    expect(screen.queryByText('B0')).toBeNull()
  })

  it('clicking a column header sorts, and clicking again reverses', async () => {
    renderPanel()
    await screen.findByText('B0')
    const nameOf = () => Array.from(
      document.querySelectorAll('tbody tr'),
    ).map(tr => within(tr as HTMLElement).getByText(/^B\d$/).textContent)

    await userEvent.click(screen.getByText('v_nom'))
    const asc = nameOf()
    await userEvent.click(screen.getByText('v_nom'))
    const desc = nameOf()
    expect(desc).toEqual([...asc].reverse())
  })
})

describe('AssetTable render cap — behaviour as of e8614a35', () => {
  it('renders every row when the count is at or below the 1000 cap', async () => {
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(50) as never)
    renderPanel()
    await screen.findByText('B0')
    expect(document.querySelectorAll('tbody tr').length).toBe(50)
    expect(screen.queryByText(/showing first/i)).toBeNull()
  })

  it('caps the DOM at 1000 rows and shows the truncation notice', async () => {
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(1200) as never)
    renderPanel()
    await screen.findByText('B0')
    expect(document.querySelectorAll('tbody tr').length).toBe(1000)
    expect(screen.getByText(/1000/)).toBeTruthy()
  })

  it('select-all past the cap selects the UNCAPPED row count', async () => {
    // This is the behaviour decision 5 leans on: paste must reach rows the DOM
    // never rendered. If this ever reports 1000, the cap has leaked into
    // selection and the paste path is silently truncated.
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(1200) as never)
    renderPanel()
    await screen.findByText('B0')
    const header = (await screen.findAllByRole('checkbox'))[0]
    await userEvent.click(header)
    expect(screen.getByText(/1200 selected/)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run it and confirm it passes against unmodified source**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
```

Expected: `Test Files  1 passed (1)` with 9 tests passing.

These pin existing behaviour and must pass first time. If a query fails to find
an element, fix the **query**, not the component — read the render at
`BottomPanel.tsx:340-470` and match its real markup (for example, confirm the
search input's actual `placeholder` text and the truncation notice's wording at
`:423-430`, and adjust the selectors above to match rather than changing the
component).

- [ ] **Step 3: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/BottomPanel.test.tsx
git diff --cached --name-only
git commit -m "test(gui): characterize the bottom panel's selection, sort and render cap"
```

---

## Task 3: Characterize `BusAutocomplete` before its three adaptations

**Files:**
- Test: `pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable. Task 10 changes this component; these tests are
  what prove its single existing caller (`CreationForm.tsx:514`) still behaves
  identically afterwards.

**Context the implementer needs.** This is the widget the grid leans on hardest
(D4 editor row 2) and it has **zero coverage**. Four behaviours matter, all in
`components/BusAutocomplete.tsx`:

1. The type-ahead filter is a case-insensitive `includes`, capped at 60 results
   (`:22-24`).
2. `exactMatch` **lower-cases both sides** (`:25`), and a non-empty value with
   no match renders "No bus with this name — it will be created automatically"
   (`:26, 108-112`). Task 10 makes this conditional on a new `allowUnknown` prop.
3. The dropdown is `position: fixed`, positioned from
   `inputRef.getBoundingClientRect()`, recomputed only on `[open, value]`
   (`:61-66`). Task 10 adds `scroll` and `resize`.
4. `ArrowDown` / `ArrowUp` move a cursor and `preventDefault()`, but **neither
   branch calls `stopPropagation()`** (`:43-53`). Task 10 adds it.

jsdom reports 0 for `getBoundingClientRect`, so the geometry test asserts that
the dropdown is `position: fixed` and that a rect **is** read, not specific
pixel values.

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx`:

```tsx
// Characterization of BusAutocomplete, written BEFORE spec D4's three additive
// adaptations (allowUnknown, scroll/resize reposition, arrow stopPropagation).
// Zero coverage today; CreationForm.tsx:514 is its only caller and must behave
// identically after Task 10.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import BusAutocomplete from './BusAutocomplete'

const BUSES = ['Bus A', 'Bus B', 'North', 'north_2', 'South']

/** Controlled wrapper — the component takes value/onChange from its parent. */
function Harness({ buses = BUSES, initial = '' }: { buses?: string[]; initial?: string }) {
  const [v, setV] = (globalThis as unknown as {
    __react: typeof import('react')
  }).__react.useState(initial)
  return <BusAutocomplete value={v} onChange={setV} buses={buses} />
}

afterEach(() => vi.restoreAllMocks())

describe('BusAutocomplete filtering — behaviour as of e8614a35', () => {
  it('filters case-insensitively on a substring', async () => {
    render(<BusAutocomplete value="nor" onChange={() => {}} buses={BUSES} />)
    await userEvent.click(screen.getByRole('textbox'))
    expect(screen.getByText('North')).toBeTruthy()
    expect(screen.getByText('north_2')).toBeTruthy()
    expect(screen.queryByText('South')).toBeNull()
  })

  it('caps the visible suggestions at 60', async () => {
    const many = Array.from({ length: 200 }, (_, i) => `B${i}`)
    render(<BusAutocomplete value="B" onChange={() => {}} buses={many} />)
    await userEvent.click(screen.getByRole('textbox'))
    expect(document.querySelectorAll('li').length).toBe(60)
  })

  it('shows the auto-create warning for a value matching no bus', () => {
    render(<BusAutocomplete value="Nrth" onChange={() => {}} buses={BUSES} />)
    expect(screen.getByText(/created automatically/)).toBeTruthy()
  })

  it('treats a case-differing name as an exact match, so no warning appears', () => {
    // This is the behaviour D4 calls out as too lax for a grid: PyPSA's index
    // lookup is case-sensitive, so "NORTH" is a dangling reference. The widget
    // accepts it today; Task 10 does NOT change this — gridEdit rejects it.
    render(<BusAutocomplete value="NORTH" onChange={() => {}} buses={BUSES} />)
    expect(screen.queryByText(/created automatically/)).toBeNull()
  })

  it('shows no warning for an empty value', () => {
    render(<BusAutocomplete value="" onChange={() => {}} buses={BUSES} />)
    expect(screen.queryByText(/created automatically/)).toBeNull()
  })
})

describe('BusAutocomplete keyboard — behaviour as of e8614a35', () => {
  it('ArrowDown on a closed dropdown opens it', async () => {
    render(<BusAutocomplete value="" onChange={() => {}} buses={BUSES} />)
    const input = screen.getByRole('textbox')
    input.focus()
    expect(document.querySelectorAll('li').length).toBeGreaterThan(0)
  })

  it('ArrowDown does NOT stop propagation today', () => {
    // Task 10 adds stopPropagation so an arrow key cannot escape an open editor
    // and move the grid's active cell. Pinning the current behaviour makes that
    // change visible rather than silent.
    const outer = vi.fn()
    render(
      <div onKeyDown={outer}>
        <BusAutocomplete value="" onChange={() => {}} buses={BUSES} />
      </div>,
    )
    const input = screen.getByRole('textbox')
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(outer).toHaveBeenCalled()
  })

  it('Enter selects the highlighted suggestion', async () => {
    const onChange = vi.fn()
    render(<BusAutocomplete value="Bus" onChange={onChange} buses={BUSES} />)
    const input = screen.getByRole('textbox')
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('Bus A')
  })

  it('clicking a suggestion commits it', async () => {
    const onChange = vi.fn()
    render(<BusAutocomplete value="Sou" onChange={onChange} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    fireEvent.mouseDown(screen.getByText('South'))
    expect(onChange).toHaveBeenCalledWith('South')
  })
})

describe('BusAutocomplete dropdown geometry — behaviour as of e8614a35', () => {
  it('renders the list fixed-positioned so it escapes a scroll container', () => {
    render(<BusAutocomplete value="Bus" onChange={() => {}} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    const list = document.querySelector('ul') as HTMLElement
    expect(list.style.position).toBe('fixed')
  })

  it('does not reposition on scroll today', () => {
    // Task 10 adds a capture-phase scroll listener. Today nothing recomputes,
    // which is exactly why the dropdown would drift inside the grid's scrolling
    // table body.
    render(<BusAutocomplete value="Bus" onChange={() => {}} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    const list = document.querySelector('ul') as HTMLElement
    const before = list.style.top
    fireEvent.scroll(document, {})
    expect((document.querySelector('ul') as HTMLElement).style.top).toBe(before)
  })
})
```

**Note on the `Harness` helper above:** it is unused by the tests as written —
every case passes `value` directly because the component is controlled and none
of these assertions needs the value to change. **Delete the `Harness` function
before committing**; it is shown here only so the next reader knows a controlled
wrapper was considered and is not needed. Leaving it in would fail `tsc -b` with
an unused-variable error under this project's settings.

- [ ] **Step 2: Delete the unused `Harness`, then run**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/components/BusAutocomplete.test.tsx
```

Expected: `Test Files  1 passed (1)`, 11 tests passing.

If `ArrowDown on a closed dropdown opens it` fails, read `:45-47`: the dropdown
also opens on `onFocus` (`:82`), so focusing alone may already have opened it —
adjust the assertion to match the real trigger rather than changing the
component.

- [ ] **Step 3: Type-check and commit**

```bash
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0. A surviving unused `Harness` fails here.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx
git diff --cached --name-only
git commit -m "test(gui): characterize BusAutocomplete before the grid adapts it"
```

---

## Task 4: Characterize `CarrierSelect` before it gains a third consumer

**Files:**
- Test: `pypsa-gui/frontend/src/components/CarrierSelect.test.tsx` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable. D4 consumes `CarrierSelect` **unchanged**; this
  test is what makes "unchanged" a checkable claim rather than an intention.

**Context the implementer needs.** `CarrierSelect.tsx` is a native `<select>`
whose option list is the union of three sources (`:12-18`): the project's own
carriers, the curated `CARRIER_CATALOG_NAMES`, and the current value — the last
so a legacy carrier stays selected instead of vanishing. It renders one
`<optgroup>` per category from the `GROUPS` table at `:48-71`, and `label={null}`
omits the label element.

It fetches carriers itself with `useQuery` under `nk(currentProject, 'carriers')`
(`:1-7`), so a test must supply a `QueryClientProvider` and seed that key
directly — the component reads through `useQuery`, so `setQueryData` before
render is the cheapest way to make the data present without a mocked fetch
resolving asynchronously.

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/frontend/src/components/CarrierSelect.test.tsx`:

```tsx
// Characterization of CarrierSelect, written BEFORE the grid becomes its third
// consumer (spec D4). It is consumed UNCHANGED — these tests are what make that
// claim checkable. Zero coverage today.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CarrierSelect from './CarrierSelect'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getCarriers: vi.fn(async () => []) },
  }
})

const PROJECT_CARRIERS = [
  { name: 'AC', co2_emissions: 0, color: '#111111', nice_name: 'AC', unit: '' },
  { name: 'my_odd_carrier', co2_emissions: 0, color: '#222222', nice_name: '', unit: '' },
]

function renderSelect(props: Partial<React.ComponentProps<typeof CarrierSelect>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'carriers'), PROJECT_CARRIERS)
  return render(
    <QueryClientProvider client={client}>
      <CarrierSelect value="" onChange={() => {}} {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

describe('CarrierSelect option list — behaviour as of e8614a35', () => {
  it('groups options into optgroups', () => {
    renderSelect()
    const groups = document.querySelectorAll('optgroup')
    expect(groups.length).toBeGreaterThan(1)
  })

  it('includes a carrier that exists only on the project', () => {
    renderSelect()
    expect(screen.getByRole('option', { name: 'my_odd_carrier' })).toBeTruthy()
  })

  it('includes curated catalog carriers the project does not have', () => {
    renderSelect()
    // "onwind" ships in CARRIER_CATALOG_NAMES and is absent from the project.
    expect(screen.getByRole('option', { name: 'onwind' })).toBeTruthy()
  })

  it('includes the current value even when it is in neither source', () => {
    renderSelect({ value: 'legacy_one_off' })
    expect(screen.getByRole('option', { name: 'legacy_one_off' })).toBeTruthy()
  })

  it('lists each carrier exactly once', () => {
    renderSelect({ value: 'AC' })
    const acs = screen.getAllByRole('option').filter(o => o.textContent === 'AC')
    expect(acs.length).toBe(1)
  })
})

describe('CarrierSelect rendering props — behaviour as of e8614a35', () => {
  it('renders a label by default', () => {
    renderSelect({ label: 'Carrier' })
    expect(screen.getByText('Carrier')).toBeTruthy()
  })

  it('omits the label when label={null} — the prop the grid passes', () => {
    const { container } = renderSelect({ label: null })
    expect(container.querySelector('label span')).toBeNull()
  })

  it('appends className to the select', () => {
    renderSelect({ className: 'grid-cell-select' })
    const sel = screen.getByRole('combobox')
    expect(sel.className).toContain('grid-cell-select')
  })

  it('calls onChange with the chosen carrier name', async () => {
    const onChange = vi.fn()
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    client.setQueryData(nk('Demo', 'carriers'), PROJECT_CARRIERS)
    const { getByRole } = render(
      <QueryClientProvider client={client}>
        <CarrierSelect value="" onChange={onChange} />
      </QueryClientProvider>,
    )
    const sel = getByRole('combobox') as HTMLSelectElement
    const userEvent = (await import('@testing-library/user-event')).default
    await userEvent.selectOptions(sel, 'AC')
    expect(onChange).toHaveBeenCalledWith('AC')
  })
})
```

- [ ] **Step 2: Run it**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/components/CarrierSelect.test.tsx
```

Expected: `Test Files  1 passed (1)`, 9 tests passing.

If `includes curated catalog carriers` fails, open
`src/utils/carrierCatalog.ts` and pick a name that is actually in
`CARRIER_CATALOG_NAMES` — the assertion is about the union behaviour, not about
`onwind` specifically. If `omits the label` fails, read `:26` and match the real
DOM shape the `label={null}` branch produces.

- [ ] **Step 3: Type-check, run all four characterization files, commit**

```bash
PATH="$PIXI_BIN:$PATH" npm run build
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx \
  src/components/BusAutocomplete.test.tsx src/components/CarrierSelect.test.tsx
```

Expected: build exit 0; `Test Files  3 passed (3)`.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/components/CarrierSelect.test.tsx
git diff --cached --name-only
git commit -m "test(gui): characterize CarrierSelect before the grid consumes it"
```

---

## Task 5: The attribute-catalog service, its endpoint, and D14's series coverage

**Files:**
- Create: `pypsa-gui/backend/services/attribute_catalog.py`
- Create: `pypsa-gui/backend/tests/test_attribute_catalog.py`
- Modify: `pypsa-gui/backend/routers/network.py` — add `GET /catalog/{component}`; extend `list_timeseries` at `:3022`

**Interfaces:**
- Consumes: `clean_scalar` from `services/serialization.py`; `PyPSAService.get_network()`.
- Produces, for Tasks 7–15 and for Plan B:

```python
# services/attribute_catalog.py
def known_components() -> list[str]: ...
def catalog_for(n: "pypsa.Network", component_class: str) -> list[dict[str, Any]]: ...
    # raises KeyError for an unknown class
```

  and the wire payload, which `hooks/useCatalog.ts` (Task 7) types:

```jsonc
GET /api/network/catalog/Generator
{
  "component": "Generator",
  "attributes": [
    { "name": "p_nom_max", "status": "Input (optional)", "varying": false,
      "dtype": "float64", "unit": "MW", "description": "…", "type": "float",
      "default": null, "default_text": "inf" }
  ]
}
```

**Context the implementer needs — measured, because guessing here is expensive.**

PyPSA 1.1.2's `n.components.<attr>.defaults` is a DataFrame indexed by
attribute name with **exactly nine columns**:

```
['type', 'unit', 'default', 'description', 'status', 'static', 'varying', 'typ', 'dtype']
```

**There is no `default_text` column.** D24 calls `default_text` "one derived
text field", and this is where it is derived: the `default` column is `object`
dtype holding real Python values (`inf` as a float, `False` as a bool, `''` as
a str), so `default_text = str(raw_default)` yields `"inf"`, `"False"`, `""`.
That is what makes success criterion 30 — "shows `inf` rather than a blank for
`p_nom_max`" — reachable, because `clean_scalar(inf)` is `None` and `default`
alone would be blank.

`unit` and `description` hold **float `NaN`** when absent (not the string
`"nan"`), which is exactly what `clean_scalar` maps to `None`. `dtype` holds a
numpy dtype whose `str()` is `'float64'` / `'bool'` / `'object'`. `varying` may
be a numpy bool, so cast it.

The two native columns deliberately **left out** are `static` and `typ` (D24).

`n.components` exposes all ten classes' defaults on a bare `pypsa.Network()` —
they are class-level metadata and need no components added.

There is **no catch-all `GET /{…}` route** on this router (checked), so
`/catalog/{component}` cannot be shadowed.

`list_timeseries` (`:3019-3042`) iterates six components at `:3022`; D14 adds
`buses` and `transformers` so the series-shadow check covers every tab the grid
renders. `carriers` has no `_t` store and is skipped by the existing
`getattr(..., None)` guard at `:3023`.

- [ ] **Step 1: Write the failing backend test**

Create `pypsa-gui/backend/tests/test_attribute_catalog.py`:

```python
"""
GET /api/network/catalog/{component} — the payload spec D24 fixes, and the two
components D14 adds to the time-series listing.

Measured against PyPSA 1.1.2: defaults carries nine columns and NO default_text,
so default_text is derived here as str(raw_default). That derivation is the only
reason an inf default survives clean_scalar's non-finite → null scrub.
"""
from __future__ import annotations

import pypsa

from tests.conftest import build_network

CATALOG = "/api/network/catalog"


def test_unknown_component_is_400(client, install_network):
    install_network(build_network())
    r = client.get(f"{CATALOG}/Widget")
    assert r.status_code == 400
    assert "Generator" in r.json()["detail"]        # lists the valid set


def test_payload_carries_exactly_the_nine_specified_fields(client, install_network):
    install_network(build_network())
    r = client.get(f"{CATALOG}/Generator")
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "Generator"
    attrs = body["attributes"]
    assert len(attrs) > 40                           # Generator has 53
    assert set(attrs[0]) == {
        "name", "status", "varying", "dtype", "unit",
        "description", "type", "default", "default_text",
    }
    # `static` and `typ` are deliberately NOT served (D24).
    assert "static" not in attrs[0]
    assert "typ" not in attrs[0]


def _attr(client, component: str, name: str) -> dict:
    body = client.get(f"{CATALOG}/{component}").json()
    return next(a for a in body["attributes"] if a["name"] == name)


def test_an_inf_default_is_null_but_default_text_says_inf(client, install_network):
    install_network(build_network())
    a = _attr(client, "Generator", "p_nom_max")
    assert a["default"] is None                      # clean_scalar scrubbed it
    assert a["default_text"] == "inf"                # …and this is why D23 can show it
    assert a["unit"] == "MW"
    assert a["dtype"] == "float64"
    assert a["status"].startswith("Input")


def test_a_missing_unit_is_null_not_the_string_nan(client, install_network):
    install_network(build_network())
    a = _attr(client, "Generator", "bus")
    assert a["unit"] is None
    assert a["dtype"] == "object"
    assert a["status"] == "Input (required)"


def test_varying_is_a_real_bool(client, install_network):
    install_network(build_network())
    assert _attr(client, "Generator", "marginal_cost")["varying"] is True
    assert _attr(client, "Generator", "p_nom")["varying"] is False


def test_output_attributes_are_reported_as_output(client, install_network):
    install_network(build_network())
    assert _attr(client, "Generator", "p_nom_opt")["status"] == "Output"


def test_bus_control_is_output_which_is_why_d13_overrides_it(client, install_network):
    # D13's override list exists because the catalog calls this Output while the
    # app has always exposed it. Pinning the upstream fact the override answers.
    install_network(build_network())
    assert _attr(client, "Bus", "control")["status"] == "Output"


def test_boolean_dtype_is_reported_as_bool(client, install_network):
    install_network(build_network())
    assert _attr(client, "Generator", "p_nom_extendable")["dtype"] == "bool"


def test_every_grid_component_class_is_served(client, install_network):
    install_network(build_network())
    for cls in ["Bus", "Carrier", "Line", "Link", "Transformer",
                "Generator", "StorageUnit", "Store", "Load"]:
        r = client.get(f"{CATALOG}/{cls}")
        assert r.status_code == 200, cls
        assert len(r.json()["attributes"]) > 0, cls


def test_timeseries_listing_now_covers_buses_and_transformers(client, install_network):
    # D14: the series-shadow check must cover every tab the grid renders.
    n = pypsa.Network()
    n.set_snapshots(["2025-01-01 00:00", "2025-01-01 01:00"])
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01)
    n.buses_t.v_mag_pu_set["B1"] = [1.0, 1.01]
    install_network(n)
    listed = client.get("/api/network/timeseries").json()
    assert any(e["component"] == "buses" for e in listed)


def test_timeseries_listing_still_covers_the_original_six(client, install_network):
    n = build_network()
    n.generators_t.p_max_pu["solar"] = [0.5, 0.6, 0.7, 0.8]
    install_network(n)
    listed = client.get("/api/network/timeseries").json()
    entry = next(e for e in listed
                 if e["component"] == "generators" and e["attribute"] == "p_max_pu")
    assert "solar" in entry["columns"]
```

- [ ] **Step 2: Run it and watch it fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_attribute_catalog.py -v
```

Expected: every catalog test fails with 404 (the route does not exist), and
`test_timeseries_listing_now_covers_buses_and_transformers` fails on the
`any(...)` assertion. The last test passes already.

- [ ] **Step 3: Write the service module**

Create `pypsa-gui/backend/services/attribute_catalog.py`:

```python
"""
PyPSA attribute catalog — the one reader of `n.components.<attr>.defaults` for
the asset-editing feature (spec D3).

Deliberately NOT consolidated with the four pre-existing
`status.str.startswith("Input")` call sites (`routers/network.py:189, 2459`,
`services/vintage_service.py:235`): `routers/network.py` is a declared hotspot,
each of those has its own fall-back-to-all-columns behaviour, and none is
covered by a test. Recorded in the spec's Out of scope, not an oversight.

Measured against PyPSA 1.1.2 — `defaults` has exactly these nine columns:
    type, unit, default, description, status, static, varying, typ, dtype
There is NO `default_text` column; it is derived here (see `_default_text`).
"""
from __future__ import annotations

from typing import Any

from services.serialization import clean_scalar

# Class name → the pypsa.Network attribute holding that component's frame.
# Mirrors routers/network.py's _COMPONENT_ATTRS. Duplicated on purpose: the
# router imports this service, so the service must not import the router.
_CATALOG_ATTRS: dict[str, str] = {
    "Bus": "buses",
    "Carrier": "carriers",
    "Line": "lines",
    "Link": "links",
    "Transformer": "transformers",
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Load": "loads",
    "ShuntImpedance": "shunt_impedances",
}

# The two native columns deliberately not served (D24): `static` and `typ`.
# Nothing in the frontend reads them and `dtype` already carries the type in a
# JSON-safe form.
_SERVED = ("status", "varying", "dtype", "unit", "description", "type", "default")


def known_components() -> list[str]:
    """Every component class the catalog can describe, sorted."""
    return sorted(_CATALOG_ATTRS)


def _py(v: Any) -> Any:
    """numpy scalar → Python scalar. Leaves str/None/native types alone.

    Without this a numpy.float64 or numpy.bool_ reaches json.dumps, which
    raises `Object of type float64 is not JSON serializable` — the same class
    of failure CLAUDE.md records for Pydantic models in SSE frames.
    """
    if isinstance(v, str) or v is None:
        return v
    item = getattr(v, "item", None)
    return item() if callable(item) else v


def _default_text(raw: Any) -> str:
    """
    The text PyPSA's default reads as.

    `clean_scalar` maps every non-finite float to None, so `p_nom_max`'s inf
    default would otherwise reach the UI as a blank — which reads as "no
    default" rather than "unbounded". str() of the raw value keeps `inf`
    legible (D24, success criterion 30).
    """
    v = _py(raw)
    return "" if v is None else str(v)


def catalog_for(n: Any, component_class: str) -> list[dict[str, Any]]:
    """
    Every attribute PyPSA defines for `component_class`, as the nine-field
    payload D24 fixes.

    Raises KeyError when the class is unknown; the route turns that into a 400
    naming the valid set.
    """
    attr = _CATALOG_ATTRS[component_class]          # KeyError → 400 at the route
    defaults = getattr(n.components, attr).defaults

    out: list[dict[str, Any]] = []
    for name, row in defaults.iterrows():
        entry: dict[str, Any] = {"name": str(name)}
        for col in _SERVED:
            if col not in defaults.columns:
                entry[col] = None
                continue
            entry[col] = clean_scalar(_py(row[col]))
        # Normalise the three fields whose exact JSON type the frontend relies on.
        entry["status"] = "" if entry["status"] is None else str(entry["status"])
        entry["dtype"] = "" if entry["dtype"] is None else str(entry["dtype"])
        entry["varying"] = bool(entry["varying"])
        entry["type"] = "" if entry["type"] is None else str(entry["type"])
        entry["default_text"] = _default_text(row["default"])
        out.append(entry)
    return out
```

- [ ] **Step 4: Add the route and extend the time-series listing**

In `pypsa-gui/backend/routers/network.py`, add the import beside the other
service imports at the top of the file:

```python
from services import attribute_catalog
```

Add the route immediately **above** the `# ── Time Series ───` banner comment at
`:3016`, so it sits with the other read-only metadata routes and away from the
`_bulk` hotspot:

```python
# ── Attribute catalog ─────────────────────────────────────────────────────────

@router.get("/catalog/{component}")
def get_attribute_catalog(component: str) -> dict:
    """
    PyPSA's own attribute metadata for one component class (spec D3, D24).

    Class-level and immutable at runtime, which is why the client caches it
    under the unscoped key ['catalog', component] with staleTime: Infinity.
    All catalog logic lives in services/attribute_catalog.py; this stays thin
    per .cursor/rules/pypsa-gui-backend.mdc:10-12.
    """
    n = PyPSAService.get_network()
    try:
        attributes = attribute_catalog.catalog_for(n, component)
    except KeyError:
        raise HTTPException(
            400,
            f"Unknown component '{component}'. Expected one of: "
            f"{', '.join(attribute_catalog.known_components())}.",
        )
    return {"component": component, "attributes": attributes}
```

Then extend `list_timeseries`'s component list at `:3022`:

```python
    for component in ["generators", "loads", "storage_units", "stores", "lines",
                      "links", "buses", "transformers"]:
```

Leave the rest of that function alone. The deliberate side effect is that the
Time-Series tab will also list bus and transformer series that genuinely exist,
which is correct and not a regression (D14).

- [ ] **Step 5: Run the test**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_attribute_catalog.py -v
```

Expected: all tests pass.

If `test_payload_carries_exactly_the_nine_specified_fields` fails on the set
comparison, print `sorted(attrs[0])` and reconcile — the payload must be exactly
those nine keys, no more.

- [ ] **Step 6: Run the bulk characterization plus the whole backend suite**

```bash
"$PIXI_BIN/python" -m pytest tests/test_bulk_update.py tests/test_attribute_catalog.py -v
"$PIXI_BIN/python" -m ruff check services/attribute_catalog.py tests/test_attribute_catalog.py
"$PIXI_BIN/python" -m pytest
```

Expected: the two files green; ruff `All checks passed!`; the whole suite
**0 failures** with a passed count no lower than the measured 2286 baseline.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/backend/services/attribute_catalog.py \
        pypsa-gui/backend/tests/test_attribute_catalog.py \
        pypsa-gui/backend/routers/network.py
git diff --cached --name-only
git commit -m "feat(gui): serve PyPSA's attribute catalog, and cover buses/transformers in the series list"
```

---

## Task 6: `PATCH /_bulk` gains an additive row-wise body form

**Files:**
- Modify: `pypsa-gui/backend/routers/network.py:1931-2053` (`bulk_update`)
- Modify: `pypsa-gui/backend/tests/test_bulk_update.py` (append the row-form cases)

**Interfaces:**
- Consumes: Task 1's characterization suite, which must stay green unmodified.
- Produces, for Task 13's client:

```jsonc
// today's form, unchanged
{ "component_class": "Generator", "names": ["a","b"], "updates": { "p_nom": 5 } }
// new
{ "component_class": "Generator",
  "rows": [ { "name": "a", "updates": { "p_nom": 5 } },
            { "name": "b", "updates": { "p_nom": 7 } } ] }
```

  Both return `{"updated": <int>, "fields": [<str>, …]}`. The `updates` form
  keeps `list(updates.keys())` verbatim (Task 1 pins the exact dict); the `rows`
  form returns the sorted union of every row's columns.

**Context the implementer needs.** `df.loc[names, col] = value` applies **one
scalar to every named row**, so a row-by-row paste is inexpressible in today's
body — that is the whole reason for D9. Every existing guarantee must survive
both branches: rename refusal, whole-batch 404, transient 409, unknown-column
400, dtype coercion, one lock acquisition, and **exactly one changelog entry**.

The coercion loop at `:1993-2034` is extracted **unchanged** into a module-level
function that both branches call. This is a mechanical extraction pinned by Task
1, not a refactor of the hotspot — do not "improve" it while moving it.

This is a declared change hotspot in a shared worktree. Re-check
`git branch --show-current` and `git status --porcelain` immediately before the
commit, and use a path-limited `git add`.

- [ ] **Step 1: Write the failing row-form tests**

Append to `pypsa-gui/backend/tests/test_bulk_update.py`:

```python
# ── The additive row-wise form (spec D9) ─────────────────────────────────────


def test_row_form_writes_a_different_value_per_row(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom": 11.0}},
            {"name": "solar", "updates": {"p_nom": 22.0}},
        ],
    })
    assert r.status_code == 200
    assert float(net.generators.at["gas", "p_nom"]) == 11.0
    assert float(net.generators.at["solar", "p_nom"]) == 22.0


def test_row_form_reports_the_union_of_columns(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom": 1.0}},
            {"name": "solar", "updates": {"marginal_cost": 2.0}},
        ],
    })
    assert r.status_code == 200
    assert r.json() == {"updated": 2, "fields": ["marginal_cost", "p_nom"]}


def test_row_form_writes_exactly_one_changelog_entry(client, net):
    before = len(client.get("/api/changelog/").json())
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom": 1.0}},
            {"name": "solar", "updates": {"p_nom": 2.0}},
        ],
    })
    assert r.status_code == 200
    entries = client.get("/api/changelog/").json()
    assert len(entries) == before + 1
    assert "2 row(s)" in entries[0]["description"]


def test_row_form_refuses_the_whole_batch_on_an_unknown_name(client, net):
    before = float(net.generators.at["gas", "p_nom"])
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom": 999.0}},
            {"name": "ghost", "updates": {"p_nom": 999.0}},
        ],
    })
    assert r.status_code == 404
    assert float(net.generators.at["gas", "p_nom"]) == before


def test_row_form_refuses_an_unknown_column(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [{"name": "gas", "updates": {"nope": 1.0}}],
    })
    assert r.status_code == 400
    assert "no column" in r.json()["detail"].lower()


def test_row_form_refuses_a_rename(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [{"name": "gas", "updates": {"name": "gas2"}}],
    })
    assert r.status_code == 400
    assert "rename" in r.json()["detail"].lower()


def test_row_form_applies_the_same_blank_sentinels(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom_max": None}},
            {"name": "solar", "updates": {"e_sum_min": None}},
        ],
    })
    assert r.status_code == 200
    assert math.isinf(float(net.generators.at["gas", "p_nom_max"]))
    assert float(net.generators.at["solar", "e_sum_min"]) == -math.inf


def test_row_form_rejects_a_non_numeric_value_whole_batch(client, net):
    before = float(net.generators.at["gas", "p_nom"])
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom": 5.0}},
            {"name": "solar", "updates": {"p_nom": "12o0"}},
        ],
    })
    assert r.status_code == 400
    # Nothing applied — coercion runs before the lock, so "gas" is untouched.
    assert float(net.generators.at["gas", "p_nom"]) == before


def test_row_form_rejects_a_duplicate_name(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [
            {"name": "gas", "updates": {"p_nom": 1.0}},
            {"name": "gas", "updates": {"p_nom": 2.0}},
        ],
    })
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()


def test_sending_both_forms_is_refused(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "names": ["gas"], "updates": {"p_nom": 1.0},
        "rows": [{"name": "solar", "updates": {"p_nom": 2.0}}],
    })
    assert r.status_code == 400


def test_row_form_rejects_an_empty_rows_list(client, net):
    assert client.patch(BULK, json={
        "component_class": "Generator", "rows": [],
    }).status_code == 400


def test_row_form_creates_a_carrier_it_introduces(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator",
        "rows": [{"name": "gas", "updates": {"carrier": "row_form_carrier"}}],
    })
    assert r.status_code == 200
    assert "row_form_carrier" in net.carriers.index
```

- [ ] **Step 2: Run and watch the new cases fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_bulk_update.py -v
```

Expected: every test from Task 1 still passes; the twelve new row-form tests
fail — most with 400 `"updates must be a non-empty object"`, because today's
route reads only `names`/`updates`.

- [ ] **Step 3: Extract the coercion loop, unchanged, to module level**

In `pypsa-gui/backend/routers/network.py`, immediately **above**
`@router.patch("/_bulk")` at `:1931`, add:

```python
def _coerce_bulk_value(df: pd.DataFrame, col: str, value: Any) -> Any:
    """
    Coerce one bulk value to `col`'s existing dtype.

    Extracted verbatim from bulk_update's inline loop so the row-wise form
    (spec D9) applies byte-identical semantics. Mechanical move — the blank
    sentinels, the boolean vocabulary and the 400 message are unchanged, and
    tests/test_bulk_update.py pins all three.

    Without this, writing a string into a numeric column upcasts the whole
    column to `object`, which then breaks `n.export_to_netcdf()` at save time
    with a cryptic "object array contains mixed native types" ValueError.
    """
    col_dtype = df[col].dtype
    if pd.api.types.is_bool_dtype(col_dtype):
        if isinstance(value, str):
            if value.strip().lower() in ("true", "1", "yes"):
                value = True
            elif value.strip().lower() in ("false", "0", "no"):
                value = False
        return bool(value) if value is not None else value
    if pd.api.types.is_numeric_dtype(col_dtype):
        if value is None or value == "":
            # Blank-to-clear a bound should produce PyPSA's "no bound"
            # sentinel (±inf), matching how the per-row PUT path clears the
            # capacity/economic bounds via the schema aliases (_NoneToPosInf
            # on *_max / lifetime, _NoneToNegInf on e_sum_min). The
            # endswith("_max") predicate is intentionally a superset: it also
            # covers PyPSA's inf-default voltage bounds (v_mag_pu_max,
            # v_ang_max) — clearing those to inf is likewise their PyPSA
            # default, so the resulting network is valid. Everything else
            # keeps NaN ("missing"), as before.
            if col.endswith("_max") or col == "lifetime":
                return float("inf")
            if col == "e_sum_min":
                return float("-inf")
            return float("nan")            # pandas treats this as missing
        try:
            return float(value)
        except (TypeError, ValueError):
            raise HTTPException(400,
                f"Column '{col}' is numeric ({col_dtype}); got non-numeric "
                f"value {value!r}.")
    # Strings / objects pass through. We still cast to str if the user sent a
    # number into a string column so dtype stays clean.
    if pd.api.types.is_string_dtype(col_dtype) or pd.api.types.is_object_dtype(col_dtype):
        return "" if value is None else str(value)
    return value
```

- [ ] **Step 4: Rewrite `bulk_update` to accept both forms**

Replace the whole body of `bulk_update` (`:1932-2053`) with:

```python
def bulk_update(body: dict) -> dict:
    component_class = body.get("component_class", "")
    names = body.get("names", [])
    updates = body.get("updates", {})
    rows = body.get("rows")

    if component_class not in _COMPONENT_ATTRS:
        raise HTTPException(400, f"Unknown component_class '{component_class}'. "
            f"Expected one of: {', '.join(sorted(_COMPONENT_ATTRS))}.")

    # Two body forms (spec D9). The scalar form applies one value per column to
    # every named row; the row form carries a per-row patch, which is what a
    # row-by-row paste needs and what `df.loc[names, col] = value` cannot say.
    row_form = rows is not None
    if row_form and (names or updates):
        raise HTTPException(400,
            "Send either names+updates or rows, not both.")

    if row_form:
        if not isinstance(rows, list) or len(rows) == 0:
            raise HTTPException(400, "rows must be a non-empty list")
        pairs: list[tuple[str, dict]] = []
        for i, entry in enumerate(rows):
            if not isinstance(entry, dict):
                raise HTTPException(400, f"rows[{i}] must be an object")
            nm = entry.get("name")
            up = entry.get("updates")
            if not isinstance(nm, str) or not nm:
                raise HTTPException(400, f"rows[{i}] needs a non-empty 'name'")
            if not isinstance(up, dict) or len(up) == 0:
                raise HTTPException(400, f"rows[{i}] needs a non-empty 'updates' object")
            if "name" in up:
                raise HTTPException(400,
                    "Bulk rename not supported. Use PUT /<component>/{name}.")
            pairs.append((nm, up))
        name_strs = [nm for nm, _ in pairs]
        # A duplicate name would make the result order-dependent and the undo
        # step ambiguous. One gesture is one request; a client that targets the
        # same row twice has a bug worth surfacing.
        if len(set(name_strs)) != len(name_strs):
            dupes = sorted({x for x in name_strs if name_strs.count(x) > 1})
            raise HTTPException(400,
                f"Duplicate row name(s) in rows: {', '.join(dupes[:5])}.")
        touched_cols = {c for _, up in pairs for c in up}
    else:
        if not isinstance(names, list) or len(names) == 0:
            raise HTTPException(400, "names must be a non-empty list")
        if not isinstance(updates, dict) or len(updates) == 0:
            raise HTTPException(400, "updates must be a non-empty object")
        if "name" in updates:
            raise HTTPException(400, "Bulk rename not supported. Use PUT /<component>/{name}.")
        name_strs = [str(x) for x in names]
        touched_cols = set(updates)

    attr = _COMPONENT_ATTRS[component_class]
    n = PyPSAService.get_network()
    df = getattr(n, attr)

    # Resolve names. Bulk semantics: refuse the whole batch if any target is
    # missing — partial application would be hard to undo predictably.
    missing = [n_ for n_ in name_strs if n_ not in df.index]
    if missing:
        sample = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        raise HTTPException(404, f"{len(missing)} {component_class}(s) not found: {sample}")

    # Reject any target that's currently a solver-internal transient row
    # (vintage clone, VOLL slack). The /api/network/{component} filter
    # hides these from the UI, so a frontend can't normally surface their
    # names — but a stale localStorage payload, a replay attack, or a
    # power-user CLI hitting the bulk endpoint directly could. Mutating
    # LP scaffolding mid-solve corrupts the optimisation in subtle ways
    # (e.g. flipping a vintage's p_nom_extendable defeats the whole
    # per-period bound mechanism). Refuse with a clear 409.
    transient_targets = [n_ for n_ in name_strs
                         if n_ in PyPSAService.get_transient_rows(component_class)]
    if transient_targets:
        sample = ", ".join(transient_targets[:3]) + ("…" if len(transient_targets) > 3 else "")
        raise HTTPException(
            409,
            f"Cannot bulk-edit {len(transient_targets)} {component_class}(s) "
            f"({sample}) — these rows are LP scaffolding generated by the "
            f"current solve (vintage clones or VOLL slacks). Wait for the "
            f"solver to finish and try again on the parent row(s).",
        )

    # Validate every column exists. PyPSA defines its full schema lazily — the
    # column may exist on the DataFrame even if no row has set it explicitly,
    # so this catches typos like "p_min_pu " (trailing space).
    unknown_cols = [c for c in sorted(touched_cols) if c not in df.columns]
    if unknown_cols:
        raise HTTPException(400,
            f"{component_class} has no column(s): {', '.join(unknown_cols)}.")

    # Coerce EVERYTHING before taking the lock, so a bad value in row 9 leaves
    # rows 1-8 untouched. Same all-or-nothing contract the 404 above keeps.
    if row_form:
        coerced_rows: list[tuple[str, dict[str, Any]]] = [
            (nm, {c: _coerce_bulk_value(df, c, v) for c, v in up.items()})
            for nm, up in pairs
        ]
        new_carriers = [up["carrier"] for _, up in coerced_rows
                        if isinstance(up.get("carrier"), str)]
    else:
        coerced: dict[str, Any] = {
            col: _coerce_bulk_value(df, col, value) for col, value in updates.items()
        }
        new_carriers = ([coerced["carrier"]]
                        if isinstance(coerced.get("carrier"), str) else [])

    with PyPSAService.get_lock():
        # If the bulk update sets `carrier`, ensure the carrier row exists with
        # catalog metadata first — same auto-add behavior as PUT.
        if component_class != "Carrier":
            for new_carrier in new_carriers:
                ensure_carrier(n, new_carrier)
        if row_form:
            for nm, up in coerced_rows:
                for col, value in up.items():
                    df.loc[nm, col] = value
        else:
            for col, value in coerced.items():
                df.loc[name_strs, col] = value

    # One audit entry per bulk op (not per component). Pretty-print the values
    # so the History tab shows what changed at a glance. The row form cannot
    # print every value, so it prints the shape instead.
    if row_form:
        description = f"Bulk: {len(touched_cols)} field(s) across {len(name_strs)} row(s)"
        fields = sorted(touched_cols)
    else:
        description = "Bulk: " + ", ".join(f"{k}={v}" for k, v in updates.items())
        fields = list(updates.keys())
    change_log_service.log(
        "update", component_class, f"({len(name_strs)} items)", description,
    )
    return {"updated": len(name_strs), "fields": fields}
```

- [ ] **Step 5: Run the whole bulk file**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_bulk_update.py -v
```

Expected: every test passes — Task 1's characterization **unmodified** plus the
twelve row-form cases. Task 1's tests passing untouched is the proof that the
extraction changed no behaviour; if one of them now fails, revert the extraction
and redo it verbatim rather than editing the test.

- [ ] **Step 6: Lint, whole suite, commit**

```bash
"$PIXI_BIN/python" -m ruff check routers/network.py tests/test_bulk_update.py
"$PIXI_BIN/python" -m pytest
```

Expected: ruff clean; whole suite 0 failures.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current            # re-check: shared worktree, hotspot file
git status --porcelain
git add pypsa-gui/backend/routers/network.py pypsa-gui/backend/tests/test_bulk_update.py
git diff --cached --name-only
git commit -m "feat(gui): PATCH /_bulk accepts a row-wise body, one request per paste"
```

---

## Task 7: `useCatalog` and `attributeCatalog.ts` — editability, series shadow, headers, editor resolution

**Files:**
- Modify: `pypsa-gui/frontend/src/api/types.ts` (add `CatalogAttribute`, `CatalogPayload`)
- Modify: `pypsa-gui/frontend/src/api/network.ts` (add `getCatalog`)
- Create: `pypsa-gui/frontend/src/hooks/useCatalog.ts`
- Create: `pypsa-gui/frontend/src/utils/attributeCatalog.ts`
- Create: `pypsa-gui/frontend/src/utils/attributeCatalog.test.ts`

**Interfaces:**
- Consumes: Task 5's endpoint payload; `TimeseriesInfo` from `api/types.ts:547-549`
  (`{ component, attribute, column_count, columns }`).
- Produces, for Tasks 8 and 11–15 and for Plan B:

```ts
// api/types.ts
export interface CatalogAttribute {
  name: string
  status: string          // 'Input (required)' | 'Input (optional)' | 'Output'
  varying: boolean
  dtype: string           // 'float64' | 'bool' | 'object' | …
  unit: string | null
  description: string | null
  type: string
  default: unknown        // non-finite scrubbed to null by the backend
  default_text: string    // 'inf' where `default` is null — Plan B's picker
}
export interface CatalogPayload { component: string; attributes: CatalogAttribute[] }

// hooks/useCatalog.ts
export function useCatalog(componentClass: string | null):
  UseQueryResult<CatalogPayload>

// utils/attributeCatalog.ts
export type CatalogMap = Map<string, CatalogAttribute>
export function toCatalogMap(attributes: CatalogAttribute[]): CatalogMap

export type SeriesIndex = Map<string, Set<string>>   // attribute → asset names
export function buildSeriesIndex(
  entries: { component: string; attribute: string; columns: string[] }[],
  componentAttr: string,
): SeriesIndex

export const EDITABILITY_OVERRIDES: Record<string, 'editable' | 'readonly'>
export const CLOSED_SETS: Record<string, string[]>

export type EditabilityReason = 'name' | 'unknown' | 'series' | 'override' | 'output'
export type Editability = { editable: true } | { editable: false; reason: EditabilityReason }
export function resolveEditability(args: {
  componentClass: string
  column: string
  rowName: string
  catalog: CatalogMap
  series: SeriesIndex
}): Editability

export type EditorKind = 'color' | 'closedSet' | 'bus' | 'carrier' | 'boolean' | 'numeric' | 'text'
export function resolveEditor(
  componentClass: string, column: string, catalog: CatalogMap,
): EditorKind

export function isNumericDtype(dtype: string): boolean
export function isBooleanDtype(dtype: string): boolean

export function columnHeaderLabel(
  column: string, catalog: CatalogMap, colLabels: Record<string, string>,
): string
export function columnHeaderTooltip(
  componentClass: string, column: string, catalog: CatalogMap,
): string | null
```

**Context the implementer needs.**

**Plan B, not this plan, adds D22's six reveal rules to this same file.** They
are absent here on purpose — an empty stub would be a placeholder. Plan B adds a
new exported table; nothing in this task needs changing for it.

`resolveEditability` evaluation order is fixed and each step exists for a
different reason:

| # | Test | Reason |
|---|---|---|
| 1 | `column === 'name'` | never editable — `network.py:1944-1945` refuses it (D13) |
| 2 | attribute absent from the catalog | a custom column PyPSA does not define; with no `dtype` there is nothing to validate against, so read-only is the honest default. **This is a plan decision the spec does not state**; `vintage_service.py:237-240` confirms such columns exist |
| 3 | series-shadowed | D14 says "Never editable", so it outranks an `editable` override. No override attribute is `varying` today, so the order is currently unobservable — it is fixed anyway so it stays true if one ever is |
| 4 | override list | D13's exactly two entries |
| 5 | `status` starts with `Output` | D13's default |
| 6 | otherwise | editable |

`dtype` classification, from the strings the backend actually emits:
`'bool'` is boolean; anything matching `/^(float|int|uint)/` is numeric
(`'float64'`, `'int64'`); `'object'` is neither.

D15's headers: use `COL_LABELS` where an entry exists (it is curated and already
carries units, e.g. `v_nom: 'V nom (kV)'`), otherwise `col (unit)` from the
catalog when a unit exists, otherwise the bare column name. `COL_LABELS` lives
in `BottomPanel.tsx:46-60`; it is **passed in** rather than imported so this
module stays free of layout imports (D2).

The `r`/`x`/`b` tooltip is Line-only and states the per-km split (D15,
criterion 26).

- [ ] **Step 1: Add the payload types and the client method**

In `pypsa-gui/frontend/src/api/types.ts`, append beside the other network types:

```ts
/**
 * One row of PyPSA's attribute catalog (spec D24). Class-level metadata,
 * identical across projects — see hooks/useCatalog.ts for why the query key is
 * deliberately unscoped.
 */
export interface CatalogAttribute {
  name: string
  /** 'Input (required)' | 'Input (optional)' | 'Output' — D13's default. */
  status: string
  /** True when a time series may shadow the static value — D14. */
  varying: boolean
  /** numpy dtype name: 'float64', 'bool', 'object', … — D4's editor pick. */
  dtype: string
  unit: string | null
  description: string | null
  type: string
  /** Non-finite defaults are scrubbed to null by clean_scalar. */
  default: unknown
  /** The text PyPSA's default reads as — 'inf' where `default` is null. */
  default_text: string
}

export interface CatalogPayload {
  component: string
  attributes: CatalogAttribute[]
}
```

In `pypsa-gui/frontend/src/api/network.ts`, add beside `listTimeseries` (`:241`):

```ts
  // Attribute catalog (spec D3/D24). Class-level metadata; cached forever.
  getCatalog: (component: string) =>
    client.get<CatalogPayload>(`/network/catalog/${component}`).then(r => r.data),
```

and add `CatalogPayload` to that file's existing `import type { … } from './types'`.

- [ ] **Step 2: Write the failing module test**

Create `pypsa-gui/frontend/src/utils/attributeCatalog.test.ts`:

```ts
// attributeCatalog — editability (D13), series shadow (D14), header labels
// (D15) and editor resolution (D4). Pure module: no React, no DOM.
import { describe, expect, it } from 'vitest'
import {
  CLOSED_SETS, EDITABILITY_OVERRIDES, buildSeriesIndex, columnHeaderLabel,
  columnHeaderTooltip, isBooleanDtype, isNumericDtype, resolveEditability,
  resolveEditor, toCatalogMap,
} from './attributeCatalog'
import type { CatalogAttribute } from '../api/types'

function attr(over: Partial<CatalogAttribute> & { name: string }): CatalogAttribute {
  return {
    status: 'Input (optional)', varying: false, dtype: 'float64', unit: null,
    description: null, type: 'float', default: 0, default_text: '0.0', ...over,
  }
}

const GEN = toCatalogMap([
  attr({ name: 'name', dtype: 'object', status: 'Input (required)' }),
  attr({ name: 'bus', dtype: 'object', status: 'Input (required)', type: 'string' }),
  attr({ name: 'bus0', dtype: 'object' }),
  attr({ name: 'carrier', dtype: 'object' }),
  attr({ name: 'p_nom', unit: 'MW' }),
  attr({ name: 'p_nom_max', unit: 'MW', default: null, default_text: 'inf' }),
  attr({ name: 'p_nom_extendable', dtype: 'bool', type: 'boolean' }),
  attr({ name: 'committable', dtype: 'bool', type: 'boolean' }),
  attr({ name: 'marginal_cost', varying: true, unit: 'currency/MWh' }),
  attr({ name: 'p_nom_opt', status: 'Output', unit: 'MW' }),
  attr({ name: 'control', dtype: 'object', status: 'Output' }),
])

const NO_SERIES: Map<string, Set<string>> = new Map()

describe('dtype classification', () => {
  it('classifies the dtype names the backend actually emits', () => {
    expect(isBooleanDtype('bool')).toBe(true)
    expect(isNumericDtype('float64')).toBe(true)
    expect(isNumericDtype('int64')).toBe(true)
    expect(isNumericDtype('object')).toBe(false)
    expect(isBooleanDtype('object')).toBe(false)
    expect(isNumericDtype('bool')).toBe(false)
  })
})

describe('resolveEditability', () => {
  const base = { componentClass: 'Generator', rowName: 'gas', catalog: GEN, series: NO_SERIES }

  it('never allows editing `name`', () => {
    expect(resolveEditability({ ...base, column: 'name' }))
      .toEqual({ editable: false, reason: 'name' })
  })

  it('allows an Input attribute', () => {
    expect(resolveEditability({ ...base, column: 'p_nom' })).toEqual({ editable: true })
  })

  it('refuses an Output attribute', () => {
    expect(resolveEditability({ ...base, column: 'p_nom_opt' }))
      .toEqual({ editable: false, reason: 'output' })
  })

  it('refuses a column PyPSA does not define', () => {
    expect(resolveEditability({ ...base, column: 'my_custom_col' }))
      .toEqual({ editable: false, reason: 'unknown' })
  })

  it('refuses Generator.committable despite its Input status (override)', () => {
    expect(resolveEditability({ ...base, column: 'committable' }))
      .toEqual({ editable: false, reason: 'override' })
  })

  it('allows Bus.control despite its Output status (override)', () => {
    const buses = toCatalogMap([attr({ name: 'control', dtype: 'object', status: 'Output' })])
    expect(resolveEditability({
      componentClass: 'Bus', column: 'control', rowName: 'B1',
      catalog: buses, series: NO_SERIES,
    })).toEqual({ editable: true })
  })

  it('still refuses Generator.control, which has no override', () => {
    expect(resolveEditability({ ...base, column: 'control' }))
      .toEqual({ editable: false, reason: 'output' })
  })

  it('refuses a series-shadowed cell on the asset that has the series', () => {
    const series = new Map([['marginal_cost', new Set(['gas'])]])
    expect(resolveEditability({ ...base, column: 'marginal_cost', series }))
      .toEqual({ editable: false, reason: 'series' })
  })

  it('still allows the same column on an asset with no series', () => {
    const series = new Map([['marginal_cost', new Set(['gas'])]])
    expect(resolveEditability({
      ...base, column: 'marginal_cost', rowName: 'solar', series,
    })).toEqual({ editable: true })
  })

  it('does not shadow a non-varying attribute even if the map names it', () => {
    // Defence against a stale series entry: `varying` is the catalog's claim,
    // and only a varying attribute can be shadowed.
    const series = new Map([['p_nom', new Set(['gas'])]])
    expect(resolveEditability({ ...base, column: 'p_nom', series }))
      .toEqual({ editable: true })
  })

  it('documents both override entries with a reason', () => {
    expect(EDITABILITY_OVERRIDES['Bus.control']).toBe('editable')
    expect(EDITABILITY_OVERRIDES['Generator.committable']).toBe('readonly')
    expect(Object.keys(EDITABILITY_OVERRIDES).length).toBe(2)
  })
})

describe('buildSeriesIndex', () => {
  const LISTING = [
    { component: 'generators', attribute: 'p_max_pu', columns: ['solar', 'wind'] },
    { component: 'generators', attribute: 'marginal_cost', columns: ['gas'] },
    { component: 'loads', attribute: 'p_set', columns: ['L1'] },
  ]

  it('keeps only the requested component and keys by attribute', () => {
    const idx = buildSeriesIndex(LISTING, 'generators')
    expect(idx.get('p_max_pu')).toEqual(new Set(['solar', 'wind']))
    expect(idx.get('marginal_cost')).toEqual(new Set(['gas']))
    expect(idx.has('p_set')).toBe(false)
  })

  it('is empty for a component with no series', () => {
    expect(buildSeriesIndex(LISTING, 'stores').size).toBe(0)
  })
})

describe('resolveEditor — D4 rows 1 to 6, in order', () => {
  it('row 1a: Carrier.color is the colour picker', () => {
    const carriers = toCatalogMap([attr({ name: 'color', dtype: 'object' })])
    expect(resolveEditor('Carrier', 'color', carriers)).toBe('color')
  })

  it('row 1b: Bus.control and Generator.control are closed sets', () => {
    const buses = toCatalogMap([attr({ name: 'control', dtype: 'object', status: 'Output' })])
    expect(resolveEditor('Bus', 'control', buses)).toBe('closedSet')
    expect(resolveEditor('Generator', 'control', GEN)).toBe('closedSet')
    expect(CLOSED_SETS['Bus.control']).toEqual(['PQ', 'PV', 'Slack'])
    expect(CLOSED_SETS['Generator.control']).toEqual(['PQ', 'PV', 'Slack'])
  })

  it('row 2: every bus terminal column gets the bus picker', () => {
    expect(resolveEditor('Generator', 'bus', GEN)).toBe('bus')
    expect(resolveEditor('Generator', 'bus0', GEN)).toBe('bus')
  })

  it('row 2 does not capture a column that merely starts with "bus"', () => {
    const m = toCatalogMap([attr({ name: 'bus_carrier', dtype: 'object' })])
    expect(resolveEditor('Generator', 'bus_carrier', m)).toBe('text')
  })

  it('row 3: carrier gets the carrier dropdown', () => {
    expect(resolveEditor('Generator', 'carrier', GEN)).toBe('carrier')
  })

  it('row 4: a boolean dtype gets the checkbox', () => {
    expect(resolveEditor('Generator', 'p_nom_extendable', GEN)).toBe('boolean')
  })

  it('row 5: a numeric dtype gets the inf-aware numeric input', () => {
    expect(resolveEditor('Generator', 'p_nom', GEN)).toBe('numeric')
  })

  it('row 6: anything else is plain text', () => {
    const m = toCatalogMap([attr({ name: 'nice_name', dtype: 'object' })])
    expect(resolveEditor('Carrier', 'nice_name', m)).toBe('text')
  })

  it('falls back to text for a column with no catalog entry', () => {
    expect(resolveEditor('Generator', 'my_custom_col', GEN)).toBe('text')
  })
})

describe('columnHeaderLabel — D15', () => {
  const COL_LABELS = { v_nom: 'V nom (kV)', p_nom: 'P nom (MW)' }

  it('prefers a curated COL_LABELS entry', () => {
    expect(columnHeaderLabel('p_nom', GEN, COL_LABELS)).toBe('P nom (MW)')
  })

  it('falls back to the catalog unit', () => {
    const lines = toCatalogMap([attr({ name: 'r', unit: 'Ohm' })])
    expect(columnHeaderLabel('r', lines, {})).toBe('r (Ohm)')
  })

  it('uses the bare column name when there is no unit', () => {
    expect(columnHeaderLabel('carrier', GEN, {})).toBe('carrier')
  })

  it('uses the bare column name for a column with no catalog entry', () => {
    expect(columnHeaderLabel('my_custom_col', GEN, {})).toBe('my_custom_col')
  })
})

describe('columnHeaderTooltip — D15', () => {
  const lines = toCatalogMap([
    attr({ name: 'r', unit: 'Ohm', description: 'Series resistance' }),
    attr({ name: 'x', unit: 'Ohm', description: 'Series reactance' }),
    attr({ name: 'b', unit: 'S', description: 'Shunt susceptance' }),
    attr({ name: 's_nom', unit: 'MVA', description: 'Rating' }),
  ])

  it('tells the user the properties panel shows r/x/b per km', () => {
    for (const col of ['r', 'x', 'b']) {
      const tip = columnHeaderTooltip('Line', col, lines)
      expect(tip).toContain('per km')
    }
  })

  it('leaves other Line columns with just their description', () => {
    expect(columnHeaderTooltip('Line', 's_nom', lines)).toBe('Rating')
  })

  it('does not add the per-km note on a non-Line component', () => {
    const gens = toCatalogMap([attr({ name: 'r', unit: 'Ohm', description: 'Resistance' })])
    expect(columnHeaderTooltip('Generator', 'r', gens)).toBe('Resistance')
  })

  it('returns null when there is nothing to say', () => {
    expect(columnHeaderTooltip('Generator', 'my_custom_col', GEN)).toBe(null)
  })
})
```

- [ ] **Step 3: Run it and watch it fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/attributeCatalog.test.ts
```

Expected: the file fails to collect with
`Failed to resolve import "./attributeCatalog"`.

- [ ] **Step 4: Write the module**

Create `pypsa-gui/frontend/src/utils/attributeCatalog.ts`:

```ts
import type { CatalogAttribute } from '../api/types'

// ── Attribute catalog helpers ────────────────────────────────────────────────
// Pure: no React, no DOM (spec D2). Answers four questions about a grid cell —
// may it be edited (D13/D14), which editor does it open (D4), what does its
// column header read (D15), and what does that header's tooltip say.
//
// Plan B appends D22's six reveal rules to this file. They are deliberately
// absent here: Scope A has no consumer for them.

export type CatalogMap = Map<string, CatalogAttribute>

export function toCatalogMap(attributes: CatalogAttribute[]): CatalogMap {
  return new Map(attributes.map(a => [a.name, a]))
}

/** attribute name → the asset names that actually have a series for it. */
export type SeriesIndex = Map<string, Set<string>>

/**
 * Narrow `GET /network/timeseries` to one component and key it by attribute.
 * `componentAttr` is PyPSA's frame name ('generators', 'buses', …), which is
 * what that endpoint reports — not the class name.
 */
export function buildSeriesIndex(
  entries: { component: string; attribute: string; columns: string[] }[],
  componentAttr: string,
): SeriesIndex {
  const idx: SeriesIndex = new Map()
  for (const e of entries) {
    if (e.component !== componentAttr) continue
    const existing = idx.get(e.attribute)
    if (existing) for (const c of e.columns) existing.add(c)
    else idx.set(e.attribute, new Set(e.columns))
  }
  return idx
}

/**
 * Editability that differs from the catalog's `status` (spec D13, ruling 17).
 * Exactly two entries, each with its reason. Keyed `<ComponentClass>.<column>`.
 */
export const EDITABILITY_OVERRIDES: Record<string, 'editable' | 'readonly'> = {
  // Editable against status='Output': it selects the AC-PF slack, the app has
  // always exposed it (CreationForm.tsx:74, PropertiesPanel.tsx:1636), and
  // D22's rule 6 requires it settable.
  'Bus.control': 'editable',
  // Read-only against status='Input': PATCH /_bulk writes df.loc directly and
  // its own header comment names flipping `committable` as unsupported through
  // that path. The right panel's per-row PUT stays the way to change it.
  'Generator.committable': 'readonly',
}

/**
 * Columns whose valid values are a closed set the app already enumerates
 * elsewhere (D4 editor row 1). An entry may be added only when that is true —
 * the set here must match CreationForm.tsx:74 and PropertiesPanel.tsx:346-360.
 */
export const CLOSED_SETS: Record<string, string[]> = {
  'Bus.control': ['PQ', 'PV', 'Slack'],
  'Generator.control': ['PQ', 'PV', 'Slack'],
}

/** PyPSA's terminal columns: bus, bus0, bus1, bus2 … (recon §6). */
const BUS_COLUMN = /^bus\d*$/

export function isBooleanDtype(dtype: string): boolean {
  return dtype === 'bool'
}

export function isNumericDtype(dtype: string): boolean {
  return /^(float|int|uint)/.test(dtype)
}

export type EditabilityReason = 'name' | 'unknown' | 'series' | 'override' | 'output'
export type Editability =
  | { editable: true }
  | { editable: false; reason: EditabilityReason }

/**
 * May this cell be edited?
 *
 * Order is fixed and each step answers a different question:
 *   1. `name` — the backend refuses a bulk rename outright.
 *   2. no catalog entry — a custom column PyPSA does not define. With no dtype
 *      there is nothing to validate against, so read-only is the honest
 *      default rather than a guess.
 *   3. series-shadowed — D14 says "never editable", so it outranks an
 *      `editable` override. No override attribute is varying today, so this
 *      ordering is currently unobservable; it is fixed so it stays true.
 *   4. the override list (D13).
 *   5. the catalog default: Output is read-only.
 */
export function resolveEditability(args: {
  componentClass: string
  column: string
  rowName: string
  catalog: CatalogMap
  series: SeriesIndex
}): Editability {
  const { componentClass, column, rowName, catalog, series } = args

  if (column === 'name') return { editable: false, reason: 'name' }

  const attr = catalog.get(column)
  if (!attr) return { editable: false, reason: 'unknown' }

  // Only a `varying` attribute can be shadowed — the catalog's claim wins over
  // a stale entry in the series listing.
  if (attr.varying && series.get(column)?.has(rowName)) {
    return { editable: false, reason: 'series' }
  }

  const override = EDITABILITY_OVERRIDES[`${componentClass}.${column}`]
  if (override === 'readonly') return { editable: false, reason: 'override' }
  if (override === 'editable') return { editable: true }

  if (attr.status.startsWith('Output')) return { editable: false, reason: 'output' }
  return { editable: true }
}

export type EditorKind =
  | 'color' | 'closedSet' | 'bus' | 'carrier' | 'boolean' | 'numeric' | 'text'

/** D4's editor-resolution table, evaluated top to bottom. */
export function resolveEditor(
  componentClass: string, column: string, catalog: CatalogMap,
): EditorKind {
  const key = `${componentClass}.${column}`
  if (key === 'Carrier.color') return 'color'          // row 1a
  if (CLOSED_SETS[key]) return 'closedSet'             // row 1b
  if (BUS_COLUMN.test(column)) return 'bus'            // row 2
  if (column === 'carrier') return 'carrier'           // row 3

  const attr = catalog.get(column)
  if (!attr) return 'text'
  if (isBooleanDtype(attr.dtype)) return 'boolean'     // row 4
  if (isNumericDtype(attr.dtype)) return 'numeric'     // row 5
  return 'text'                                        // row 6
}

/**
 * Column header text (D15). A curated COL_LABELS entry wins — it is
 * hand-written and already carries its unit ('V nom (kV)'). Otherwise the
 * catalog's unit is appended, so Lines read `r (Ohm)`, `x (Ohm)`, `b (S)`.
 *
 * `colLabels` is passed in rather than imported: this module must not depend
 * on layout/BottomPanel.tsx (D2).
 */
export function columnHeaderLabel(
  column: string, catalog: CatalogMap, colLabels: Record<string, string>,
): string {
  const curated = colLabels[column]
  if (curated) return curated
  const unit = catalog.get(column)?.unit
  return unit ? `${column} (${unit})` : column
}

/**
 * Header tooltip (D15). Lines' r/x/b additionally state the convention split:
 * the grid is the raw-attribute surface and shows PyPSA's absolute values,
 * while the properties panel curates them per km (ruling 19). Without this the
 * same attribute reads as two different numbers in two places with no
 * explanation.
 */
const PER_KM_LINE_COLUMNS = new Set(['r', 'x', 'b'])

export function columnHeaderTooltip(
  componentClass: string, column: string, catalog: CatalogMap,
): string | null {
  const attr = catalog.get(column)
  const description = attr?.description ?? null
  if (componentClass === 'Line' && PER_KM_LINE_COLUMNS.has(column)) {
    const note = 'The properties panel shows this attribute per km; the grid '
      + 'shows the absolute value PyPSA stores.'
    return description ? `${description} — ${note}` : note
  }
  return description
}
```

- [ ] **Step 5: Write the hook**

Create `pypsa-gui/frontend/src/hooks/useCatalog.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import type { CatalogPayload } from '../api/types'

/**
 * PyPSA's attribute catalog for one component class.
 *
 * The query key is deliberately NOT nk(projectId, …), against
 * .cursor/rules/pypsa-gui-frontend.mdc:15-16. This is a named exception on the
 * same grounds as ['changelog'] (BottomPanel.tsx:288): the catalog is
 * class-level metadata, identical across every project and immutable at
 * runtime, so project-scoping it would refetch nine identical payloads on
 * every project switch. `staleTime: Infinity` follows for the same reason.
 * Recorded here because the exception is invisible at the call site (spec D24).
 */
export function useCatalog(componentClass: string | null) {
  return useQuery<CatalogPayload>({
    queryKey: ['catalog', componentClass],
    queryFn: () => networkApi.getCatalog(componentClass as string),
    enabled: !!componentClass,
    staleTime: Infinity,
    gcTime: Infinity,
  })
}
```

- [ ] **Step 6: Run the test, type-check, commit**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/attributeCatalog.test.ts
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: all tests pass; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/api/types.ts \
        pypsa-gui/frontend/src/api/network.ts \
        pypsa-gui/frontend/src/hooks/useCatalog.ts \
        pypsa-gui/frontend/src/utils/attributeCatalog.ts \
        pypsa-gui/frontend/src/utils/attributeCatalog.test.ts
git diff --cached --name-only
git commit -m "feat(gui): catalog-driven editability, series shadow, headers and editor resolution"
```

---

## Task 8: `gridEdit.ts` — one validate-then-coerce entry point for every commit

**Files:**
- Create: `pypsa-gui/frontend/src/utils/gridEdit.ts`
- Create: `pypsa-gui/frontend/src/utils/gridEdit.test.ts`
- **Not modified:** `pypsa-gui/frontend/src/utils/coerce.ts` (D2, criterion 41)

**Interfaces:**
- Consumes: `coerceForColumn` from `./coerce`; `CatalogMap`, `resolveEditor`,
  `isNumericDtype`, `CLOSED_SETS` from `./attributeCatalog` (Task 7).
- Produces, for Tasks 12 and 14:

```ts
export interface GridEditContext {
  componentClass: string
  catalog: CatalogMap
  busNames: Set<string>
}
export type CellResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string }

export function validateAndCoerce(
  column: string, raw: string, ctx: GridEditContext,
): CellResult
export function parseInfinityToken(raw: string): 'inf' | '-inf' | null
```

**Context the implementer needs.**

**`validateAndCoerce` is the single commit path.** A typed editor and a paste
both call it, so a pasted bus name is checked against the real bus list exactly
as a dropdown selection is (D4, D7).

**It wraps `coerce.ts`; it does not replace it** (D2). The blank path in
particular delegates, so `coerceForColumn('')` keeps owning the "blank is an
explicit clear" invariant and its ten existing tests stay green unmodified
(criterion 41).

**Infinity is returned as a STRING, not a number.** `JSON.stringify(Infinity)`
is `"null"`, so returning a JS `Infinity` would silently send `null` and the
backend would apply its blank rule instead of the user's `inf`. The endpoint's
`float(value)` parses `"inf"` and `"-inf"` directly — Task 1 pins that. This is
what makes criterion 15 ("the request body carries the string `\"inf\"`") true.

**Per-column rules (D4, D12):**

| Editor kind | Rule |
|---|---|
| `bus` | must be an existing bus name, **exact and case-sensitive** — stricter than `BusAutocomplete`'s own lower-cased `exactMatch` (`:26`), because PyPSA's index lookup is case-sensitive and a case-mismatched name is a dangling reference nothing below would catch |
| `carrier` | an unknown carrier is **accepted** — `bulk_update` calls `ensure_carrier` and creates the row |
| `closedSet` | must be one of that column's options |
| `boolean` | `true`/`false`/`1`/`0`/`yes`/`no`, case-**insensitive**, matching the backend's `value.strip().lower()`; lower-case the token here and delegate, so `coerce.ts` stays unmodified |
| `numeric` | infinity grammar, else a finite number; a non-numeric string is **rejected** — a named behaviour change from `coerce.ts:19`, which silently returns `null` and clears the field |
| `color`, `text` | pass through |

Blank (`raw.trim() === ''`) short-circuits every rule and returns
`coerceForColumn('', undefined)`, i.e. `null`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/gridEdit.test.ts`:

```ts
// gridEdit — the one validate-then-coerce entry point every commit goes
// through, whether it came from a typed editor or a paste (spec D4, D7, D12).
import { describe, expect, it } from 'vitest'
import { validateAndCoerce, parseInfinityToken } from './gridEdit'
import { toCatalogMap } from './attributeCatalog'
import { coerceForColumn } from './coerce'
import type { CatalogAttribute } from '../api/types'

function attr(over: Partial<CatalogAttribute> & { name: string }): CatalogAttribute {
  return {
    status: 'Input (optional)', varying: false, dtype: 'float64', unit: null,
    description: null, type: 'float', default: 0, default_text: '0.0', ...over,
  }
}

const CTX = {
  componentClass: 'Generator',
  catalog: toCatalogMap([
    attr({ name: 'p_nom', unit: 'MW' }),
    attr({ name: 'p_nom_max', unit: 'MW' }),
    attr({ name: 'p_nom_extendable', dtype: 'bool', type: 'boolean' }),
    attr({ name: 'bus', dtype: 'object', type: 'string' }),
    attr({ name: 'carrier', dtype: 'object', type: 'string' }),
    attr({ name: 'control', dtype: 'object', status: 'Output' }),
    attr({ name: 'nice_name', dtype: 'object', type: 'string' }),
  ]),
  busNames: new Set(['North', 'South', 'Bus A']),
}

describe('the blank rule — delegated to coerce.ts, unmodified (D2, D12)', () => {
  it('an empty cell commits null', () => {
    expect(validateAndCoerce('p_nom', '', CTX)).toEqual({ ok: true, value: null })
  })

  it('a whitespace-only cell is also blank', () => {
    expect(validateAndCoerce('p_nom', '   ', CTX)).toEqual({ ok: true, value: null })
  })

  it('blank returns exactly what coerce.ts returns for a blank', () => {
    // Not a tautology: this asserts the DELEGATION, i.e. that gridEdit did not
    // reimplement the blank rule with its own literal.
    const r = validateAndCoerce('p_nom', '', CTX)
    expect(r).toEqual({ ok: true, value: coerceForColumn('', undefined) })
  })

  it('blanks a string column to null too', () => {
    expect(validateAndCoerce('nice_name', '', CTX)).toEqual({ ok: true, value: null })
  })
})

describe('the infinity grammar (D12)', () => {
  it.each([
    ['inf', 'inf'], ['INF', 'inf'], ['+inf', 'inf'], ['infinity', 'inf'],
    ['Infinity', 'inf'], ['∞', 'inf'],
    ['-inf', '-inf'], ['-INFINITY', '-inf'], ['-∞', '-inf'],
  ])('parses %s as %s', (raw, expected) => {
    expect(parseInfinityToken(raw)).toBe(expected)
  })

  it('is not fooled by a number or a word', () => {
    expect(parseInfinityToken('12')).toBe(null)
    expect(parseInfinityToken('information')).toBe(null)
    expect(parseInfinityToken('')).toBe(null)
  })

  it('commits infinity as the STRING "inf", never the JS number', () => {
    // JSON.stringify(Infinity) is "null", which would silently become the
    // backend's blank rule instead of the user's inf.
    const r = validateAndCoerce('p_nom_max', 'inf', CTX)
    expect(r).toEqual({ ok: true, value: 'inf' })
    expect(JSON.stringify({ v: 'inf' })).toBe('{"v":"inf"}')
  })

  it('commits negative infinity as "-inf"', () => {
    expect(validateAndCoerce('p_nom_max', '-inf', CTX)).toEqual({ ok: true, value: '-inf' })
  })
})

describe('numeric columns (D12)', () => {
  it('accepts a plain number', () => {
    expect(validateAndCoerce('p_nom', '120', CTX)).toEqual({ ok: true, value: 120 })
  })

  it('accepts a negative number', () => {
    expect(validateAndCoerce('p_nom', '-3.5', CTX)).toEqual({ ok: true, value: -3.5 })
  })

  it('rejects a non-numeric string instead of silently clearing it', () => {
    // coerce.ts:19 returns null here, which clears the field. That is the
    // deliberate, named behaviour change (D12) — coerce.ts itself is unchanged.
    const r = validateAndCoerce('p_nom', '12o0', CTX)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toContain('12o0')
  })

  it('rejects NaN as a typed token', () => {
    expect(validateAndCoerce('p_nom', 'nan', CTX).ok).toBe(false)
  })
})

describe('bus columns — exact and case-sensitive (D4)', () => {
  it('accepts an existing bus name', () => {
    expect(validateAndCoerce('bus', 'North', CTX)).toEqual({ ok: true, value: 'North' })
  })

  it('rejects a name differing only in case', () => {
    // BusAutocomplete's own exactMatch lower-cases both sides, which is right
    // for the creation form and wrong here: PyPSA's index lookup is
    // case-sensitive, so "NORTH" is a dangling reference (criterion 19).
    const r = validateAndCoerce('bus', 'NORTH', CTX)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toContain('NORTH')
  })

  it('rejects a bus that does not exist', () => {
    expect(validateAndCoerce('bus', 'Nrth', CTX).ok).toBe(false)
  })

  it('accepts a bus name containing spaces', () => {
    expect(validateAndCoerce('bus', 'Bus A', CTX)).toEqual({ ok: true, value: 'Bus A' })
  })
})

describe('the carrier column accepts the unknown (D4)', () => {
  it('accepts a carrier the network does not have yet', () => {
    // bulk_update calls ensure_carrier and creates the row (criterion 20).
    expect(validateAndCoerce('carrier', 'brand_new', CTX))
      .toEqual({ ok: true, value: 'brand_new' })
  })
})

describe('closed-set columns (D4)', () => {
  it('accepts each option', () => {
    for (const v of ['PQ', 'PV', 'Slack']) {
      expect(validateAndCoerce('control', v, CTX)).toEqual({ ok: true, value: v })
    }
  })

  it('rejects anything else, naming the allowed set', () => {
    const r = validateAndCoerce('control', 'Swing', CTX)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toContain('Slack')
  })
})

describe('boolean columns — case-insensitive (D4)', () => {
  it.each([
    ['true', true], ['TRUE', true], ['True', true], ['1', true], ['YES', true],
    ['false', false], ['FALSE', false], ['0', false], ['No', false],
  ])('accepts %s', (raw, expected) => {
    expect(validateAndCoerce('p_nom_extendable', raw, CTX))
      .toEqual({ ok: true, value: expected })
  })

  it('rejects a token that is neither', () => {
    expect(validateAndCoerce('p_nom_extendable', 'maybe', CTX).ok).toBe(false)
  })
})

describe('text columns', () => {
  it('passes the text through unchanged', () => {
    expect(validateAndCoerce('nice_name', 'Combined Cycle', CTX))
      .toEqual({ ok: true, value: 'Combined Cycle' })
  })

  it('does not trim interior spacing', () => {
    expect(validateAndCoerce('nice_name', 'a  b', CTX))
      .toEqual({ ok: true, value: 'a  b' })
  })
})

describe('an unknown column', () => {
  it('is rejected rather than guessed at', () => {
    expect(validateAndCoerce('mystery', '5', CTX).ok).toBe(false)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/gridEdit.test.ts
```

Expected: fails to collect with `Failed to resolve import "./gridEdit"`.

- [ ] **Step 3: Write the module**

Create `pypsa-gui/frontend/src/utils/gridEdit.ts`:

```ts
import { coerceForColumn } from './coerce'
import {
  CLOSED_SETS, isNumericDtype, resolveEditor, type CatalogMap,
} from './attributeCatalog'

// ── Grid cell validation and coercion ────────────────────────────────────────
// Pure: no React, no DOM (spec D2). This is the SINGLE commit path — a typed
// editor and a paste both arrive here, so a pasted bus name is checked against
// the real bus list exactly as a dropdown selection is (D4, D7). The widget is
// an input affordance; it is never the thing that enforces correctness.
//
// This WRAPS utils/coerce.ts rather than replacing it, so today's blank
// semantics are preserved by construction and coerce.ts's ten existing tests
// stay green unmodified (D2, success criterion 41).

export interface GridEditContext {
  componentClass: string
  catalog: CatalogMap
  /** Every bus name in the network. Membership is exact and case-sensitive. */
  busNames: Set<string>
}

export type CellResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string }

const INFINITY_WORDS = new Set(['inf', 'infinity', '∞'])

/**
 * Recognise the infinity grammar (D12): inf, +inf, -inf, infinity, ∞, -∞,
 * case-insensitive.
 *
 * Returns the STRING 'inf' / '-inf', never a JS number: JSON.stringify(Infinity)
 * is "null", which the backend would read as a blank and turn into its own
 * sentinel instead of the user's infinity. The endpoint's float(value) parses
 * these strings directly (pinned by tests/test_bulk_update.py).
 */
export function parseInfinityToken(raw: string): 'inf' | '-inf' | null {
  const t = raw.trim().toLowerCase()
  if (!t) return null
  const negative = t.startsWith('-')
  const body = negative || t.startsWith('+') ? t.slice(1) : t
  if (!INFINITY_WORDS.has(body)) return null
  return negative ? '-inf' : 'inf'
}

const BOOLEAN_TRUE = new Set(['true', '1', 'yes'])
const BOOLEAN_FALSE = new Set(['false', '0', 'no'])

/**
 * Validate a raw string against its column and coerce it to what the request
 * body should carry. Called identically by a typed commit, a fill and a block
 * paste.
 */
export function validateAndCoerce(
  column: string, raw: string, ctx: GridEditContext,
): CellResult {
  // Blank first, before any type dispatch — coerce.ts owns this rule and the
  // backend re-interprets the resulting null per column name (D12).
  if (raw.trim() === '') {
    return { ok: true, value: coerceForColumn('', undefined) }
  }

  const attr = ctx.catalog.get(column)
  if (!attr) {
    return { ok: false, error: `Column '${column}' is not a PyPSA attribute of ${ctx.componentClass}.` }
  }

  const kind = resolveEditor(ctx.componentClass, column, ctx.catalog)

  if (kind === 'bus') {
    // Exact and case-sensitive, deliberately stricter than BusAutocomplete's
    // own lower-cased exactMatch: PyPSA's index lookup is case-sensitive, so a
    // case-mismatched name is a dangling reference no layer below would catch.
    if (!ctx.busNames.has(raw)) {
      return { ok: false, error: `'${raw}' is not an existing bus name (names are case-sensitive).` }
    }
    return { ok: true, value: raw }
  }

  if (kind === 'carrier') {
    // An unknown carrier is accepted: bulk_update calls ensure_carrier and
    // creates the row with catalog metadata. A paste can introduce a carrier;
    // the dropdown cannot, and does not need to.
    return { ok: true, value: raw }
  }

  if (kind === 'closedSet') {
    const options = CLOSED_SETS[`${ctx.componentClass}.${column}`] ?? []
    if (!options.includes(raw)) {
      return { ok: false, error: `'${raw}' is not one of ${options.join(', ')}.` }
    }
    return { ok: true, value: raw }
  }

  if (kind === 'boolean') {
    const t = raw.trim().toLowerCase()
    if (!BOOLEAN_TRUE.has(t) && !BOOLEAN_FALSE.has(t)) {
      return { ok: false, error: `'${raw}' is not a boolean (try true/false, 1/0, yes/no).` }
    }
    // Lower-cased before delegating so coerce.ts's case-sensitive test still
    // matches and that file stays unmodified (D2).
    return { ok: true, value: coerceForColumn(t, true) }
  }

  if (kind === 'numeric' || isNumericDtype(attr.dtype)) {
    const infinite = parseInfinityToken(raw)
    if (infinite) return { ok: true, value: infinite }
    const n = Number(raw)
    if (!Number.isFinite(n)) {
      // A named behaviour change from coerce.ts:19, which returns null here and
      // so silently CLEARS the field on a typo (D12).
      return { ok: false, error: `'${raw}' is not a number.` }
    }
    return { ok: true, value: n }
  }

  // color and text: the literal string.
  return { ok: true, value: raw }
}
```

- [ ] **Step 4: Run the test and prove `coerce.ts` is untouched**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/gridEdit.test.ts src/utils/coerce.test.ts
```

Expected: `Test Files  2 passed (2)`. `coerce.test.ts`'s ten tests pass
**unmodified** — that is criterion 41's frontend half.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git diff --stat e8614a35 -- pypsa-gui/frontend/src/utils/coerce.ts
```

Expected: **no output** — `coerce.ts` is byte-identical to the baseline.

- [ ] **Step 5: Type-check and commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/utils/gridEdit.ts pypsa-gui/frontend/src/utils/gridEdit.test.ts
git diff --cached --name-only
git commit -m "feat(gui): one validate-then-coerce path for every grid commit"
```

---

## Task 9: `clipboardTsv.ts` — the wire format and the three paste shapes

**Files:**
- Create: `pypsa-gui/frontend/src/utils/clipboardTsv.ts`
- Create: `pypsa-gui/frontend/src/utils/clipboardTsv.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces, for Task 14:

```ts
export function serialiseTsv(matrix: string[][]): string
export function parseTsv(text: string): string[][]
export function guardCell(text: string, isStringColumn: boolean): string
export function unguardCell(text: string, isStringColumn: boolean): string

export type PasteShape =
  | { kind: 'fill'; value: string }
  | { kind: 'rowwise'; values: string[] }
  | { kind: 'block'; matrix: string[][] }
  | { kind: 'reject'; message: string }

export function resolvePasteShape(
  matrix: string[][], targetRows: number, columnsAvailable: number,
): PasteShape
```

**Context the implementer needs.**

**Wire format (D6), since recon found no rule to inherit.** Emit `\r\n` between
rows and `\t` between cells, with **no trailing terminator** — CRLF matches the
one adjacent house rule (`CLAUDE.md:575-576`, CSV export) and is accepted by
Excel and Numbers on both platforms. Accept `\r\n`, `\n` **or** `\r` on the way
in, and drop **exactly one** trailing empty row (Excel appends one).

**No quote grammar.** A cell is the literal text between tabs. No PyPSA
attribute in any of the nine tabs holds a tab or a newline — names, carriers,
booleans and numbers are the whole domain — so a quote grammar would add an
escaping surface for no measured need.

**The injection guard is scoped to string columns** and this is load-bearing for
criterion 12. On copy, a cell in a column whose dtype is neither numeric nor
boolean and whose text starts with `=`, `+`, `-` or `@` is prefixed with a
single quote. Numeric and boolean columns are **never** prefixed, so a negative
number round-trips byte-exactly. On paste, exactly one leading single quote is
stripped from a cell targeting a string column.

The house rule's other two triggers, tab and CR, are deliberately **not** in the
set: a cell value cannot contain either — the same fact that lets the parser do
without a quote grammar — so including them would add an unreachable branch,
which reads as protection that is not there.

**Paste shapes (D7),** for a clipboard matrix N×M against T target rows:

| Shape | Condition | Result |
|---|---|---|
| fill | N=1, M=1 | the value goes to every target row in the active column |
| rowwise | N=T, M=1 | value *i* → target row *i* in `sorted` order |
| block | N=T, M>1, M ≤ columns available | column *j* → the *j*-th visible column at or right of the active one |
| reject | anything else | message stating both shapes |

Rule order matters: a 1×1 clipboard onto a 1-row target is a **fill**, because
rule 1 is tested first. The two are indistinguishable in effect, so this is a
tie-break, not a behaviour choice.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/clipboardTsv.test.ts`:

```ts
// clipboardTsv — the TSV wire format (D6) and the three paste shapes (D7).
// Pure: no React, no DOM.
import { describe, expect, it } from 'vitest'
import {
  guardCell, parseTsv, resolvePasteShape, serialiseTsv, unguardCell,
} from './clipboardTsv'

describe('serialiseTsv (D6)', () => {
  it('joins cells with tabs and rows with CRLF', () => {
    expect(serialiseTsv([['a', 'b'], ['c', 'd']])).toBe('a\tb\r\nc\td')
  })

  it('emits no trailing terminator', () => {
    expect(serialiseTsv([['a']]).endsWith('\r\n')).toBe(false)
  })

  it('serialises a single cell as itself', () => {
    expect(serialiseTsv([['42']])).toBe('42')
  })

  it('serialises an empty matrix as an empty string', () => {
    expect(serialiseTsv([])).toBe('')
  })
})

describe('parseTsv (D6)', () => {
  it('accepts CRLF', () => {
    expect(parseTsv('a\tb\r\nc\td')).toEqual([['a', 'b'], ['c', 'd']])
  })

  it('accepts LF', () => {
    expect(parseTsv('a\tb\nc\td')).toEqual([['a', 'b'], ['c', 'd']])
  })

  it('accepts CR', () => {
    expect(parseTsv('a\tb\rc\td')).toEqual([['a', 'b'], ['c', 'd']])
  })

  it('CRLF and LF payloads produce identical results (criterion 13)', () => {
    expect(parseTsv('1\r\n2\r\n3')).toEqual(parseTsv('1\n2\n3'))
  })

  it('drops exactly one trailing empty row', () => {
    expect(parseTsv('a\r\nb\r\n')).toEqual([['a'], ['b']])
  })

  it('keeps a second trailing empty row, which is real data', () => {
    expect(parseTsv('a\r\nb\r\n\r\n')).toEqual([['a'], ['b'], ['']])
  })

  it('preserves an empty cell in the middle of a row', () => {
    expect(parseTsv('a\t\tc')).toEqual([['a', '', 'c']])
  })

  it('round-trips through serialise', () => {
    const m = [['1', 'x'], ['2', 'y'], ['3', 'z']]
    expect(parseTsv(serialiseTsv(m))).toEqual(m)
  })

  it('parses an empty string as no rows', () => {
    expect(parseTsv('')).toEqual([])
  })
})

describe('the CSV-injection guard, scoped to string columns (D6)', () => {
  it.each(['=SUM(A1)', '+1', '-1', '@x'])('prefixes %s in a string column', (s) => {
    expect(guardCell(s, true)).toBe(`'${s}`)
  })

  it('leaves an ordinary string alone', () => {
    expect(guardCell('CCGT', true)).toBe('CCGT')
  })

  it('never prefixes a numeric column, so -3.5 round-trips byte-exactly', () => {
    // Criterion 12. A guard here would turn -3.5 into '-3.5 and the paste back
    // would either fail or write a string into a numeric column.
    expect(guardCell('-3.5', false)).toBe('-3.5')
    expect(unguardCell(guardCell('-3.5', false), false)).toBe('-3.5')
  })

  it('strips exactly one leading quote on paste into a string column', () => {
    expect(unguardCell("'=SUM(A1)", true)).toBe('=SUM(A1)')
    expect(unguardCell("''x", true)).toBe("'x")
  })

  it('does not strip a quote for a numeric column', () => {
    expect(unguardCell("'5", false)).toBe("'5")
  })

  it('round-trips a formula-looking carrier name', () => {
    expect(unguardCell(guardCell('-odd_carrier', true), true)).toBe('-odd_carrier')
  })
})

describe('resolvePasteShape (D7)', () => {
  it('1x1 is a fill, whatever the target size', () => {
    expect(resolvePasteShape([['7']], 300, 5)).toEqual({ kind: 'fill', value: '7' })
    expect(resolvePasteShape([['7']], 1, 5)).toEqual({ kind: 'fill', value: '7' })
  })

  it('Nx1 matching the target row count is row-wise', () => {
    expect(resolvePasteShape([['1'], ['2'], ['3']], 3, 5))
      .toEqual({ kind: 'rowwise', values: ['1', '2', '3'] })
  })

  it('NxM matching the target row count is a block', () => {
    const m = [['1', 'a'], ['2', 'b']]
    expect(resolvePasteShape(m, 2, 4)).toEqual({ kind: 'block', matrix: m })
  })

  it('rejects a block wider than the columns available', () => {
    const r = resolvePasteShape([['1', 'a', 'x'], ['2', 'b', 'y']], 2, 2)
    expect(r.kind).toBe('reject')
    if (r.kind === 'reject') expect(r.message).toContain('column')
  })

  it('rejects a row-count mismatch, naming both counts (criterion 7)', () => {
    const r = resolvePasteShape([['1'], ['2']], 5, 3)
    expect(r.kind).toBe('reject')
    if (r.kind === 'reject') {
      expect(r.message).toContain('2')
      expect(r.message).toContain('5')
    }
  })

  it('rejects an empty clipboard', () => {
    expect(resolvePasteShape([], 3, 3).kind).toBe('reject')
  })

  it('rejects a ragged matrix', () => {
    expect(resolvePasteShape([['1', '2'], ['3']], 2, 3).kind).toBe('reject')
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/clipboardTsv.test.ts
```

Expected: fails to collect with `Failed to resolve import "./clipboardTsv"`.

- [ ] **Step 3: Write the module**

Create `pypsa-gui/frontend/src/utils/clipboardTsv.ts`:

```ts
// ── Clipboard TSV ────────────────────────────────────────────────────────────
// Pure: no React, no DOM (spec D2). Owns the wire format the grid copies and
// pastes, and the shape detection that resolves a clipboard matrix against the
// paste target (D6, D7).
//
// No quote grammar. A cell is the literal text between tabs. No PyPSA
// attribute in any of the nine tabs holds a tab or a newline — names, carriers,
// booleans and numbers are the whole domain — so an escaping surface would add
// a failure mode for no measured need. The same fact is why the injection guard
// below omits the house rule's tab and CR triggers: those arms are unreachable,
// and an unreachable branch reads as protection that is not there.

const CELL_SEP = '\t'
/** CRLF on the way out: matches CLAUDE.md:575-576 and is what Excel expects. */
const ROW_SEP_OUT = '\r\n'
/** CRLF, LF or CR on the way in — Excel, Numbers and hand-typed all differ. */
const ROW_SEP_IN = /\r\n|\n|\r/

export function serialiseTsv(matrix: string[][]): string {
  return matrix.map(row => row.join(CELL_SEP)).join(ROW_SEP_OUT)
}

export function parseTsv(text: string): string[][] {
  if (text === '') return []
  const rows = text.split(ROW_SEP_IN)
  // Excel appends exactly one trailing separator; a second empty row is real
  // data and is kept.
  if (rows.length > 1 && rows[rows.length - 1] === '') rows.pop()
  return rows.map(r => r.split(CELL_SEP))
}

/** Leading characters a spreadsheet would execute as a formula. */
const INJECTION_PREFIXES = ['=', '+', '-', '@']

/**
 * CSV-injection guard, scoped to string columns (D6).
 *
 * Numeric and boolean columns are never prefixed, so a negative number
 * round-trips byte-exactly (success criterion 12) — guarding them would turn
 * -3.5 into '-3.5 and break the paste back.
 */
export function guardCell(text: string, isStringColumn: boolean): string {
  if (!isStringColumn) return text
  return INJECTION_PREFIXES.some(p => text.startsWith(p)) ? `'${text}` : text
}

/** Strip exactly one leading single quote, string columns only. */
export function unguardCell(text: string, isStringColumn: boolean): string {
  if (!isStringColumn) return text
  return text.startsWith("'") ? text.slice(1) : text
}

export type PasteShape =
  | { kind: 'fill'; value: string }
  | { kind: 'rowwise'; values: string[] }
  | { kind: 'block'; matrix: string[][] }
  | { kind: 'reject'; message: string }

/**
 * Resolve a clipboard matrix against the paste target (D7).
 *
 * `targetRows` is the checkbox selection if non-empty, otherwise 1 (the active
 * cell's row). `columnsAvailable` is the count of visible columns at or right
 * of the active column.
 *
 * Rule order is load-bearing only as a tie-break: a 1x1 clipboard onto a
 * 1-row target matches both rule 1 and rule 2 and they do the same thing.
 */
export function resolvePasteShape(
  matrix: string[][], targetRows: number, columnsAvailable: number,
): PasteShape {
  const n = matrix.length
  if (n === 0) {
    return { kind: 'reject', message: 'The clipboard is empty.' }
  }
  const m = matrix[0].length
  if (matrix.some(r => r.length !== m)) {
    return {
      kind: 'reject',
      message: 'The clipboard rows do not all have the same number of columns.',
    }
  }

  if (n === 1 && m === 1) return { kind: 'fill', value: matrix[0][0] }

  const shapes = `Clipboard is ${n} row(s) × ${m} column(s); `
    + `the paste target is ${targetRows} row(s) × ${columnsAvailable} column(s).`

  if (n !== targetRows) return { kind: 'reject', message: shapes }
  if (m === 1) return { kind: 'rowwise', values: matrix.map(r => r[0]) }
  if (m > columnsAvailable) return { kind: 'reject', message: shapes }
  return { kind: 'block', matrix }
}
```

- [ ] **Step 4: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/clipboardTsv.test.ts
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: all tests pass; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/utils/clipboardTsv.ts pypsa-gui/frontend/src/utils/clipboardTsv.test.ts
git diff --cached --name-only
git commit -m "feat(gui): TSV clipboard format and paste-shape resolution"
```

---

## Task 10: Three additive adaptations to `BusAutocomplete`

**Files:**
- Modify: `pypsa-gui/frontend/src/components/BusAutocomplete.tsx`
- Modify: `pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx` (Task 3's file — two pinned behaviours flip)

**Interfaces:**
- Consumes: Task 3's characterization suite.
- Produces:

```ts
interface BusAutocompleteProps {
  value: string
  onChange: (v: string) => void
  buses: string[]
  placeholder?: string
  required?: boolean
  readOnly?: boolean
  /** Grid passes false: an unknown bus is a dangling reference, not a new bus. */
  allowUnknown?: boolean          // default true
}
```

**Context the implementer needs.** All three changes are additive and must leave
the single existing caller (`CreationForm.tsx:514`, which passes no
`allowUnknown`) behaving exactly as it does today. Each closes a hazard:

1. **`allowUnknown`, defaulting to `true`.** The "No bus with this name — it
   will be created automatically" line (`:26, 108-112`) is true for the creation
   form and false in a grid. With `allowUnknown={false}` it becomes a refusal
   message instead. `gridEdit` still does the actual validation (Task 8); this
   only stops the widget *promising* something untrue (criterion 18).
2. **Reposition on scroll and resize.** `dropPos` recomputes only on
   `[open, value]` (`:62-66`), so the fixed-position dropdown would not follow
   the grid's scrolling table body (criterion 17). The `scroll` listener must be
   **capture-phase** to see scrolls on the inner container, which do not bubble.
3. **`stopPropagation()` on ArrowUp/ArrowDown in both dropdown states.** Today
   neither branch does (`:43-53`), so with the dropdown closed an arrow reaches
   the grid and moves the active cell out from under an open editor. This is the
   one exception D5's "arrows never navigate while editing" needs made real
   (criterion 23).

The outside-click `document` mousedown listener (`:29-35`) needs **no** change:
closing the dropdown is not closing the editor, and the editor still commits on
blur.

Two of Task 3's tests pin behaviour this task deliberately changes — they are
updated here, in the same commit, with the reason in a comment.

- [ ] **Step 1: Update the two characterization tests that flip**

In `pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx`, replace the
test named `'ArrowDown does NOT stop propagation today'` with:

```tsx
  it('ArrowDown stops propagation so it cannot reach the grid', () => {
    // Flipped by Task 10 (spec D4 adaptation 3). With the dropdown open OR
    // closed, an arrow key belongs to this widget: letting it bubble would move
    // the grid's active cell out from under an open editor (criterion 23).
    const outer = vi.fn()
    render(
      <div onKeyDown={outer}>
        <BusAutocomplete value="" onChange={() => {}} buses={BUSES} />
      </div>,
    )
    const input = screen.getByRole('textbox')
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(outer).not.toHaveBeenCalled()
  })
```

and replace `'does not reposition on scroll today'` with:

```tsx
  it('repositions on scroll so it follows the grid body', () => {
    // Flipped by Task 10 (adaptation 2). jsdom reports a zero rect, so this
    // asserts that a recompute HAPPENED, not a pixel value: the listener is
    // capture-phase because a scroll on the table body does not bubble.
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    render(<BusAutocomplete value="Bus" onChange={() => {}} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    const before = spy.mock.calls.length
    fireEvent.scroll(document, {})
    expect(spy.mock.calls.length).toBeGreaterThan(before)
  })
```

Append a new describe block for the third adaptation:

```tsx
describe('BusAutocomplete allowUnknown — added by Task 10', () => {
  it('still promises auto-creation by default, for the creation form', () => {
    render(<BusAutocomplete value="Nrth" onChange={() => {}} buses={BUSES} />)
    expect(screen.getByText(/created automatically/)).toBeTruthy()
  })

  it('refuses instead of promising when allowUnknown is false', () => {
    render(
      <BusAutocomplete value="Nrth" onChange={() => {}} buses={BUSES} allowUnknown={false} />,
    )
    expect(screen.queryByText(/created automatically/)).toBeNull()
    expect(screen.getByText(/no bus named/i)).toBeTruthy()
  })

  it('says nothing when the value matches a real bus', () => {
    render(
      <BusAutocomplete value="North" onChange={() => {}} buses={BUSES} allowUnknown={false} />,
    )
    expect(screen.queryByText(/no bus named/i)).toBeNull()
  })
})
```

- [ ] **Step 2: Run and watch the three new expectations fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/components/BusAutocomplete.test.tsx
```

Expected: the two flipped tests and `refuses instead of promising` fail; every
other test still passes.

- [ ] **Step 3: Apply the three adaptations**

In `pypsa-gui/frontend/src/components/BusAutocomplete.tsx`:

Extend the props interface (`:3-10`):

```tsx
interface BusAutocompleteProps {
  value: string
  onChange: (v: string) => void
  buses: string[]
  placeholder?: string
  required?: boolean
  readOnly?: boolean
  /**
   * Whether a name matching no bus is acceptable. The creation form's default
   * (true) is honest — the backend auto-creates the bus. The grid passes false,
   * where an unknown name is a dangling reference instead (spec D4).
   */
  allowUnknown?: boolean
}
```

and the destructure (`:12-14`):

```tsx
export default function BusAutocomplete({
  value, onChange, buses, placeholder = 'Select bus…', required, readOnly,
  allowUnknown = true,
}: BusAutocompleteProps) {
```

Replace the geometry effect (`:62-66`) with one that also follows scroll and
resize:

```tsx
  const [dropPos, setDropPos] = useState({ top: 0, left: 0, width: 0 })
  useEffect(() => {
    if (!open || !inputRef.current) return
    const place = () => {
      const el = inputRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      setDropPos({ top: rect.bottom + 4, left: rect.left, width: rect.width + 28 })
    }
    place()
    // Capture phase: a scroll inside the grid's table body does NOT bubble to
    // window, and a fixed-position dropdown that does not follow its anchor
    // drifts away from the cell it belongs to (spec D4 adaptation 2).
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, value])
```

Add `stopPropagation()` to both arrow branches in `handleKey` (`:43-53`):

```tsx
  const handleKey = (e: React.KeyboardEvent) => {
    if (readOnly) return
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') { setOpen(true); setCursor(0) }
      // An arrow belongs to this widget in BOTH states. Letting it bubble with
      // the dropdown closed would move the grid's active cell out from under an
      // open editor (spec D4 adaptation 3, D5).
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') e.stopPropagation()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault(); e.stopPropagation()
      setCursor(c => Math.min(c + 1, filtered.length - 1))
    }
    else if (e.key === 'ArrowUp') {
      e.preventDefault(); e.stopPropagation()
      setCursor(c => Math.max(c - 1, -1))
    }
    else if (e.key === 'Enter') { if (cursor >= 0 && filtered[cursor]) { e.preventDefault(); select(filtered[cursor]) } }
    else if (e.key === 'Escape') { setOpen(false); setCursor(-1) }
  }
```

Replace the warning block (`:108-112`) so it tells the truth in both modes:

```tsx
      {showWarn && !readOnly && (
        <p className="text-[9px] text-warn mt-0.5 leading-tight">
          {allowUnknown
            ? 'No bus with this name — it will be created automatically'
            : `No bus named "${value}" — pick an existing bus`}
        </p>
      )}
```

- [ ] **Step 4: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/components/BusAutocomplete.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: all tests pass; build exit 0. `CreationForm` passes no `allowUnknown`,
so its behaviour is unchanged — the default keeps the old message.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/components/BusAutocomplete.tsx \
        pypsa-gui/frontend/src/components/BusAutocomplete.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): BusAutocomplete gains allowUnknown, scroll tracking and arrow containment"
```

---

## Task 11: The active cell — roving tabindex, keyboard map, catalog-driven headers

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/AppHeader.tsx:273-279` (guard the capture-phase Escape)
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — `AssetTable`
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (append the navigation cases)

**Interfaces:**
- Consumes: `useCatalog` (Task 7), `columnHeaderLabel`, `columnHeaderTooltip`,
  `resolveEditability`, `buildSeriesIndex`, `toCatalogMap` (Task 7).
- Produces, for Tasks 12–15, inside `AssetTable`:

```ts
type ActiveCell = { name: string; col: string } | null
// state: const [active, setActive] = useState<ActiveCell>(null)
// state: const [editing, setEditing] = useState<ActiveCell>(null)   // Task 12 mounts on this
```

**Context the implementer needs.**

**D1 is the constraint, not a suggestion.** The `<table>` markup, sticky
checkbox column, shift-range selection, cap-splice (`:208-219`), `truncated`
notice, `rowRefs`/`scrollIntoView`, `TAB_COLUMNS` and the 1000-row cap all stay.
Task 2's tests are what notice if one of them moves.

**D19: no ARIA grid roles.** The retained `<table>` already carries the correct
native roles and recon found no in-repo grid-a11y precedent. Exactly one cell
carries `tabIndex={0}` (the active one); every other carries `-1`. That keeps
focus on a real element so blur-commit and Escape work unchanged.
`aria-activedescendant` is rejected because it needs explicit grid roles, which
D1 rules out.

**D5's keyboard map, editor closed** (the open-editor column is Task 12's):

| Key | Editor closed |
|---|---|
| Arrows | move the active cell |
| Enter | open the editor on the active cell |
| Tab / Shift+Tab | move the active cell right / left |
| Escape | clear the active cell marker |
| A printable character | open the editor seeded with that character |
| Ctrl/Cmd+C / +V | copy / paste (Task 14) |

**The one capture-phase Escape handler that must be guarded at the source.**
`AppHeader.tsx:277` calls `closeSearch()` on Escape from a `document` **capture**
listener registered at `:281`. Capture runs `window → document → … → target`, so
it fires *before* the grid's handler no matter the registration order —
`stopPropagation()` is useless against it. It gains the editable-element guard
already used three lines from its sibling at `App.tsx:485`.

Every **bubble**-phase Escape listener (`App.tsx:512`, and the unguarded ones in
`TopologyCanvas`, `MapCanvas`, `ChatPanel`) is fixed by `stopPropagation()`
alone and needs **no file change** — that is what keeps criterion 1's "leaves any
open slide panel open" true. `Dialog.tsx:148` is left alone: it is capture-phase
*and* calls `stopPropagation()`, which is why D18 keeps the grid's only
confirmation out of a `Dialog`.

- [ ] **Step 1: Append the navigation tests**

Append to `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx`:

```tsx
describe('AssetTable active cell — added by Task 11', () => {
  it('clicking a cell makes it the only tabbable one (D19 roving tabindex)', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cells = document.querySelectorAll('tbody td')
    await userEvent.click(cells[2] as HTMLElement)
    const tabbable = document.querySelectorAll('tbody td[tabindex="0"]')
    expect(tabbable.length).toBe(1)
  })

  it('ArrowDown moves the active cell one row down', async () => {
    renderPanel()
    await screen.findByText('B0')
    const first = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(first)
    fireEvent.keyDown(first, { key: 'ArrowDown' })
    const active = document.querySelector('td[tabindex="0"]') as HTMLElement
    expect(active.dataset.row).toBe('B1')
    expect(active.dataset.col).toBe('v_nom')
  })

  it('ArrowRight moves to the next visible column', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="name"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'ArrowRight' })
    const active = document.querySelector('td[tabindex="0"]') as HTMLElement
    expect(active.dataset.col).not.toBe('name')
  })

  it('does not move past the last row', async () => {
    renderPanel()
    await screen.findByText('B4')
    const last = document.querySelector('tbody tr:last-child td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(last)
    fireEvent.keyDown(last, { key: 'ArrowDown' })
    expect((document.querySelector('td[tabindex="0"]') as HTMLElement).dataset.row).toBe('B4')
  })

  it('Escape clears the active cell', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Escape' })
    expect(document.querySelectorAll('td[tabindex="0"]').length).toBe(0)
  })
})

describe('AssetTable headers — D15', () => {
  it('renders the curated COL_LABELS entry where one exists', async () => {
    renderPanel()
    // COL_LABELS maps v_nom → 'V nom (kV)' (BottomPanel.tsx:46-60).
    expect(await screen.findByText('V nom (kV)')).toBeTruthy()
  })
})

describe('availableCols stays derived from the data — D17', () => {
  it('does not offer a catalog attribute that no row carries', async () => {
    // The catalog ANNOTATES columns; it does not add them. A column absent
    // from the DataFrame is 400-rejected by _bulk ("has no column(s)"), so
    // offering it would produce a guaranteed failure. This is the test that
    // fails if someone later drives the column list from the catalog instead
    // of from the data.
    renderPanel()
    await screen.findByText('B0')
    // `v_mag_pu_set` is a real PyPSA Bus attribute; the mocked rows omit it.
    expect(screen.queryByText('v_mag_pu_set')).toBeNull()
    // Open the Columns menu — it must not list it either.
    await userEvent.click(screen.getByText(/columns/i))
    expect(screen.queryByText('v_mag_pu_set')).toBeNull()
  })

  it('keeps `name` pinned visible', async () => {
    renderPanel()
    await screen.findByText('B0')
    expect(document.querySelector('tbody td[data-col="name"]')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
```

Expected: Task 2's nine tests still pass; the six new ones fail — there is no
`data-col`, no `data-row` and no `tabindex` on any cell yet.

- [ ] **Step 3: Guard `AppHeader`'s capture-phase Escape**

In `pypsa-gui/frontend/src/layout/AppHeader.tsx`, replace the `onKey` handler at
`:273-279`:

```tsx
    const onKey = (e: KeyboardEvent) => {
      // Global Escape — works even when focus has moved off the input
      // (e.g. user tabbed away but the dropdown is still on screen because
      // the debounced search hasn't cleared yet, or the focus was stolen
      // by a click on another element that doesn't process Escape).
      //
      // Guarded against editable elements, the same test App.tsx:485 uses.
      // This listener is CAPTURE-phase, so it runs before any descendant and
      // stopPropagation() downstream cannot pre-empt it — the guard has to be
      // here or an Escape meant to discard a grid cell edit also closes the
      // header search (spec D5).
      if (e.key !== 'Escape') return
      const t = e.target as HTMLElement | null
      if (t?.tagName === 'INPUT' || t?.tagName === 'TEXTAREA' || t?.isContentEditable) return
      closeSearch()
    }
```

- [ ] **Step 4: Add the active cell and the keyboard map to `AssetTable`**

In `pypsa-gui/frontend/src/layout/BottomPanel.tsx`, add the imports at the top:

```tsx
import { useCatalog } from '../hooks/useCatalog'
import {
  buildSeriesIndex, columnHeaderLabel, columnHeaderTooltip, resolveEditability,
  toCatalogMap,
} from '../utils/attributeCatalog'
```

Inside `AssetTable`, after the `selectedRows` state (`:168-170`), add:

```tsx
  // ── Active cell (spec D19) ─────────────────────────────────────────────────
  // Exactly one cell is tabbable at a time; everything else carries -1. Focus
  // stays on a real element, so blur-commit and Escape work unchanged and no
  // ARIA grid roles are needed — the <table> already carries the right ones.
  // AssetTable does not receive currentProject as a prop and does not
  // destructure it today — only its parent BottomPanel does (:888). The grid
  // needs it for every nk() key below, so take it reactively here. In the
  // non-React mutation callbacks (Task 13) read it via
  // useUIStore.getState().currentProject instead: the parity rule at
  // queryKeys.ts:16-22 is what makes a mismatched id return undefined.
  const currentProject = useUIStore(s => s.currentProject)

  type CellRef = { name: string; col: string }
  const [active, setActive] = useState<CellRef | null>(null)
  // Which cell has an OPEN editor. Task 12 mounts on this; one at a time.
  const [editing, setEditing] = useState<CellRef | null>(null)
  useEffect(() => { setActive(null); setEditing(null) }, [tab])

  // ── Catalog + series shadow (D13, D14) ────────────────────────────────────
  const { data: catalogPayload } = useCatalog(componentClass)
  const catalog = useMemo(
    () => toCatalogMap(catalogPayload?.attributes ?? []),
    [catalogPayload],
  )
  const { data: tsList = [] } = useQuery({
    queryKey: nk(currentProject, 'timeseries'),
    queryFn: networkApi.listTimeseries,
  })
  const series = useMemo(
    () => buildSeriesIndex(tsList, TAB_TO_API_KEY[componentClass] ?? ''),
    [tsList, componentClass],
  )

  const editabilityOf = useCallback(
    (rowName: string, col: string) =>
      resolveEditability({ componentClass, column: col, rowName, catalog, series }),
    [componentClass, catalog, series],
  )
```

Add the navigation helper and the key handler below them:

```tsx
  /** Move the active cell by (dRow, dCol) within `displayed` × `visibleCols`. */
  const moveActive = useCallback((dRow: number, dCol: number) => {
    setActive(prev => {
      if (!prev) return prev
      const rowIdx = displayed.findIndex(r => r.name === prev.name)
      const colIdx = visibleCols.indexOf(prev.col)
      if (rowIdx < 0 || colIdx < 0) return prev
      const nextRow = Math.min(Math.max(rowIdx + dRow, 0), displayed.length - 1)
      const nextCol = Math.min(Math.max(colIdx + dCol, 0), visibleCols.length - 1)
      return { name: displayed[nextRow].name as string, col: visibleCols[nextCol] }
    })
  }, [displayed, visibleCols])

  /**
   * Keyboard map for a cell with NO open editor (spec D5). The open-editor
   * column of that table lives in the editor component.
   *
   * stopPropagation() on Escape is what keeps an open slide panel open: every
   * other Escape listener in the app is bubble-phase (App.tsx:512 and the
   * unguarded ones in TopologyCanvas / MapCanvas / ChatPanel), so stopping
   * here pre-empts all of them without touching those files. The one
   * capture-phase listener, AppHeader.tsx:277, is guarded at its source.
   */
  const onCellKeyDown = (e: React.KeyboardEvent, rowName: string, col: string) => {
    if (editing) return                       // the editor owns the keyboard
    const modifier = e.ctrlKey || e.metaKey
    if (modifier) return                      // copy/paste — Task 14

    switch (e.key) {
      case 'ArrowDown':  e.preventDefault(); moveActive(1, 0); return
      case 'ArrowUp':    e.preventDefault(); moveActive(-1, 0); return
      case 'ArrowRight': e.preventDefault(); moveActive(0, 1); return
      case 'ArrowLeft':  e.preventDefault(); moveActive(0, -1); return
      case 'Tab':
        e.preventDefault(); moveActive(0, e.shiftKey ? -1 : 1); return
      case 'Escape':
        e.stopPropagation()
        setActive(null)
        return
      case 'Enter':
        e.preventDefault()
        if (editabilityOf(rowName, col).editable) setEditing({ name: rowName, col })
        return
      default:
        // A printable character opens the editor seeded with it (D5). Task 12
        // reads `seed` to prefill; until then it simply opens.
        if (e.key.length === 1 && editabilityOf(rowName, col).editable) {
          setEditing({ name: rowName, col })
        }
    }
  }
```

Move focus to the active cell whenever it changes, so the roving tabindex is
real rather than notional:

```tsx
  /**
   * Composite key for the cell-ref map. A tab cannot occur in a PyPSA name or
   * attribute — the same fact utils/clipboardTsv.ts relies on to do without a
   * quote grammar — so it is a safe separator for `col` + `name`.
   */
  const cellKey = (name: string, col: string) => `${col}\t${name}`
  const cellRefs = useRef<Record<string, HTMLTableCellElement | null>>({})
  useEffect(() => {
    if (!active) return
    cellRefs.current[cellKey(active.name, active.col)]?.focus()
  }, [active])
```

- [ ] **Step 5: Render the cell and header changes**

In the header row, replace the plain column label with the catalog-driven one
(D15):

```tsx
                <th
                  key={col}
                  onClick={() => handleSort(col)}
                  title={columnHeaderTooltip(componentClass, col, catalog) ?? undefined}
                  className="px-1.5 py-1 text-left font-medium cursor-pointer hover:text-text select-none"
                >
                  {columnHeaderLabel(col, catalog, COL_LABELS)}
                  {sortCol === col && (sortDir === 'asc' ? ' ▲' : ' ▼')}
                </th>
```

In the body, give every data cell its identity, its tabindex and its handler.
Keep the existing `fmt(...)` render for a non-editing cell — Task 12 swaps in the
editor:

```tsx
                  <td
                    key={col}
                    ref={el => { cellRefs.current[cellKey(row.name as string, col)] = el }}
                    data-row={row.name as string}
                    data-col={col}
                    tabIndex={active?.name === row.name && active?.col === col ? 0 : -1}
                    onClick={() => setActive({ name: row.name as string, col })}
                    onKeyDown={e => onCellKeyDown(e, row.name as string, col)}
                    className={cellClass(row.name as string, col)}
                  >
                    {fmt(row[col])}
                  </td>
```

with the class helper beside `fmt` (`:227-235`):

```tsx
  /** Visual state for a cell: active ring, and the read-only/series greys. */
  const cellClass = (rowName: string, col: string): string => {
    const base = 'px-1.5 py-0.5 whitespace-nowrap outline-none'
    const ring = active?.name === rowName && active?.col === col
      ? ' ring-1 ring-inset ring-accent' : ''
    const ed = editabilityOf(rowName, col)
    if (ed.editable) return base + ring
    // Output / override / unknown read as "not yours to edit"; a series-shadowed
    // cell reads as "the static value is dead here" (D14) and gets its badge in
    // Task 12.
    return `${base}${ring} text-muted bg-panel/40`
  }
```

- [ ] **Step 6: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: Task 2's nine tests **and** the six new ones pass; build exit 0.

If a navigation test finds no `td[tabindex="0"]`, check that the click handler
sets `active` before the assertion — `userEvent.click` flushes React, so a
failure here means the handler is on the wrong element.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/AppHeader.tsx \
        pypsa-gui/frontend/src/layout/BottomPanel.tsx \
        pypsa-gui/frontend/src/layout/BottomPanel.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): grid active cell, roving tabindex, keyboard map and catalog headers"
```

---

## Task 12: The six typed cell editors

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx` (add `CellEditor`; mount it from the cell)
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (append editor cases)

**Interfaces:**
- Consumes: `resolveEditor`, `CLOSED_SETS` (Task 7); `validateAndCoerce` (Task 8);
  `BusAutocomplete` with `allowUnknown={false}` (Task 10); `CarrierSelect` as-is.
- Produces:

```tsx
function CellEditor(props: {
  componentClass: string
  column: string
  initial: string
  seed?: string
  busNames: string[]
  catalog: CatalogMap
  onCommit: (raw: string, fill: boolean) => void   // fill = Ctrl/Cmd+Enter
  onCancel: () => void
}): JSX.Element
```

**Context the implementer needs.**

**One editor is mounted at a time and the draft is a single
`{ name, col, raw }`** — not `CarriersTable`'s flat draft map
(`BottomPanel.tsx:1065,1099`), because at the render cap that would mount
1000 × 15 inputs, which is the DOM budget the cap exists to protect. A fresh
editor mounting per cell also satisfies the uncontrolled-input staleness rule
(`CLAUDE.md:586-587`) structurally — **no `key`-remount trick is needed.**

**Every editor commits a string** into `validateAndCoerce`, so the editor path
and the paste path share one validator (D4). The widget is an input affordance;
it never enforces correctness.

**The boolean cell is the one stated exception to click-to-edit** (D4): a
checkbox holds no draft, so there is nothing to open. It renders always-on. Its
DOM cost is bounded by boolean columns × 1000, which is at most two columns on
any tab in `TAB_COLUMNS`. A plain click toggles the active cell only;
**Ctrl/Cmd+click toggles it as a fill gesture** over the paste target,
preserving the deleted toolbar's set-many-booleans capability.

**The numeric editor is `type="text"`, not `type="number"`** — `<input
type="number">` cannot hold `inf` and reads back `''` (D12). This is the one
typed widget with no in-repo precedent.

**`cardKit`'s `ChkInput` is deliberately not reused.** Its `col-span-2` layout
and `onCheck` side-effect hook would both have to go, and what remains is a bare
`<input type="checkbox">`. Extracting a shared component out of a file with zero
coverage to share three lines is not worth it.

`CarrierSelect` is consumed **as-is** with styling props only (`label={null}`,
`className`) — Task 4 pins that "as-is" is true.

- [ ] **Step 1: Append the editor tests**

Append to `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx`:

```tsx
describe('AssetTable cell editors — D4', () => {
  it('a single click on an editable numeric cell opens a text input', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    // type="text", not "number": <input type="number"> cannot hold `inf`.
    expect(input.getAttribute('type')).toBe('text')
  })

  it('mounts at most one editor at a time', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cells = document.querySelectorAll('tbody td[data-col="v_nom"]')
    await userEvent.click(cells[0] as HTMLElement)
    fireEvent.keyDown(cells[0] as HTMLElement, { key: 'Enter' })
    await userEvent.click(cells[1] as HTMLElement)
    fireEvent.keyDown(cells[1] as HTMLElement, { key: 'Enter' })
    expect(document.querySelectorAll('tbody input[type="text"]').length).toBe(1)
  })

  it('Escape discards the draft and closes the editor', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    await userEvent.clear(input)
    await userEvent.type(input, '999')
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(cell.querySelector('input')).toBeNull()
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('committing unchanged text issues no request (criterion 2)', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('a read-only Output cell does not open an editor', async () => {
    renderPanel()
    await screen.findByText('B0')
    // sub_network is Output on Bus; the cell must refuse to open.
    const cell = document.querySelector('tbody tr td[data-col="sub_network"]')
    if (!cell) return                       // column not visible on this tab
    await userEvent.click(cell as HTMLElement)
    fireEvent.keyDown(cell as HTMLElement, { key: 'Enter' })
    expect((cell as HTMLElement).querySelector('input')).toBeNull()
  })

  it('a bus cell opens BusAutocomplete rather than a bare input', async () => {
    vi.mocked(networkApi).getGenerators.mockResolvedValue([
      { name: 'gas', bus: 'B0', carrier: 'gas', p_nom: 100 },
    ] as never)
    renderPanel()
    await userEvent.click(await screen.findByText('Generators'))
    const cell = await screen.findByText('B0')
    const td = cell.closest('td') as HTMLElement
    await userEvent.click(td)
    fireEvent.keyDown(td, { key: 'Enter' })
    expect(td.querySelector('input[type="text"]')).toBeTruthy()
    // The auto-create promise must NOT appear — the grid passes allowUnknown={false}.
    expect(screen.queryByText(/created automatically/)).toBeNull()
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
```

Expected: the editor cases fail — no cell renders an input yet.

- [ ] **Step 3: Write `CellEditor`**

In `pypsa-gui/frontend/src/layout/BottomPanel.tsx`, add above `AssetTable`:

```tsx
// ── CellEditor ───────────────────────────────────────────────────────────────
// One editor is mounted at a time, chosen by D4's resolution table. Every
// editor commits a STRING through the same gridEdit validator, so the typed
// path and the paste path cannot disagree.
//
// Because a fresh editor mounts per cell, the uncontrolled-input staleness rule
// (CLAUDE.md:586-587) is satisfied structurally — no key-remount trick needed.
function CellEditor({
  componentClass, column, initial, seed, busNames, catalog, onCommit, onCancel,
}: {
  componentClass: string
  column: string
  initial: string
  seed?: string
  busNames: string[]
  catalog: CatalogMap
  onCommit: (raw: string, fill: boolean) => void
  onCancel: () => void
}) {
  const [raw, setRaw] = useState(seed ?? initial)
  const kind = resolveEditor(componentClass, column, catalog)
  const inputCls = 'w-full bg-bg border border-accent px-1 py-0 text-[11px] font-mono outline-none'

  // Shared key handling for the text-like editors (D5, open-editor column).
  const onKey = (e: React.KeyboardEvent) => {
    const modifier = e.ctrlKey || e.metaKey
    if (e.key === 'Enter') {
      e.preventDefault(); e.stopPropagation()
      onCommit(raw, modifier)              // Ctrl/Cmd+Enter = fill gesture
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation()
      onCancel()
      return
    }
    if (modifier && (e.key === 's' || e.key === 'S')) {
      // A save must never run with a pending edit outstanding
      // (CLAUDE.md:650-666,812) and the grid cannot make its PATCH land
      // synchronously. Swallowing the first keypress is the honest behaviour.
      e.preventDefault(); e.stopPropagation()
      onCommit(raw, false)
      toast.success('Cell saved — press again to save the project')
      return
    }
    // Arrows must not reach the grid while an editor is open (D5). Bus cells
    // are the exception and handle their own arrows (Task 10 adaptation 3).
    if (e.key.startsWith('Arrow')) e.stopPropagation()
  }

  if (kind === 'bus') {
    return (
      <BusAutocomplete
        value={raw}
        onChange={v => { setRaw(v); onCommit(v, false) }}
        buses={busNames}
        allowUnknown={false}
        placeholder="Bus…"
      />
    )
  }

  if (kind === 'carrier') {
    // Consumed as-is, styling props only (D4). Its native <select> popup cannot
    // be clipped by the grid's scroll container.
    return (
      <CarrierSelect
        value={raw}
        onChange={v => { setRaw(v); onCommit(v, false) }}
        label={null}
        className="w-full text-[11px] py-0"
        wrapperClassName="block"
      />
    )
  }

  if (kind === 'closedSet') {
    const options = CLOSED_SETS[`${componentClass}.${column}`] ?? []
    return (
      <select
        autoFocus
        value={raw}
        onChange={e => { setRaw(e.target.value); onCommit(e.target.value, false) }}
        onKeyDown={onKey}
        className={inputCls}
      >
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }

  if (kind === 'color') {
    // Carried over from CarriersTable (:1204-1218): the picker fires on every
    // keystroke, so commit on blur for one request per change.
    return (
      <span className="flex items-center gap-1">
        <input
          type="color"
          value={/^#[0-9a-f]{6}$/i.test(raw) ? raw : '#888888'}
          onChange={e => setRaw(e.target.value)}
          onBlur={() => onCommit(raw, false)}
          className="w-6 h-5 rounded border border-border cursor-pointer"
        />
        <input
          autoFocus
          type="text"
          value={raw}
          onChange={e => setRaw(e.target.value)}
          onBlur={() => onCommit(raw, false)}
          onKeyDown={onKey}
          className={inputCls}
        />
      </span>
    )
  }

  // numeric and text both use a type="text" input: type="number" cannot hold
  // `inf` and reads back '' (D12).
  return (
    <input
      autoFocus
      type="text"
      value={raw}
      onChange={e => setRaw(e.target.value)}
      onBlur={() => onCommit(raw, false)}
      onKeyDown={onKey}
      className={inputCls}
    />
  )
}
```

Add the imports this needs at the top of the file:

```tsx
import BusAutocomplete from '../components/BusAutocomplete'
import CarrierSelect from '../components/CarrierSelect'
import { CLOSED_SETS, resolveEditor, isBooleanDtype, type CatalogMap } from '../utils/attributeCatalog'
import { validateAndCoerce } from '../utils/gridEdit'
```

- [ ] **Step 4: Mount the editor and the always-on boolean from the cell**

Replace the `<td>` body written in Task 11 with:

```tsx
                    {editing?.name === row.name && editing?.col === col ? (
                      <CellEditor
                        componentClass={componentClass}
                        column={col}
                        initial={row[col] == null ? '' : String(row[col])}
                        busNames={busNames}
                        catalog={catalog}
                        onCommit={(raw, fill) => commitCell(row.name as string, col, raw, fill)}
                        onCancel={() => setEditing(null)}
                      />
                    ) : isBooleanCell(col) ? (
                      <input
                        type="checkbox"
                        checked={row[col] === true}
                        disabled={!editabilityOf(row.name as string, col).editable}
                        onClick={e => {
                          // Ctrl/Cmd+click is a FILL gesture over the paste
                          // target, mirroring Ctrl/Cmd+Enter and preserving the
                          // deleted toolbar's set-many-booleans capability (D4).
                          const modifier = e.ctrlKey || e.metaKey
                          commitCell(row.name as string, col,
                            row[col] === true ? 'false' : 'true', modifier)
                        }}
                        readOnly
                        className="cursor-pointer"
                      />
                    ) : seriesShadowed(row.name as string, col) ? (
                      <span className="flex items-center gap-1">
                        <span className="opacity-50">{fmt(row[col])}</span>
                        <span
                          title="A time series exists for this asset — the static value is not what the solver reads."
                          className="px-1 rounded bg-border/40 text-[8px] uppercase tracking-wide"
                        >series</span>
                      </span>
                    ) : (
                      fmt(row[col])
                    )}
```

and add the two small helpers beside `cellClass`:

```tsx
  const isBooleanCell = (col: string): boolean => {
    const attr = catalog.get(col)
    return !!attr && isBooleanDtype(attr.dtype)
  }
  const seriesShadowed = (rowName: string, col: string): boolean =>
    editabilityOf(rowName, col).editable === false
    && (editabilityOf(rowName, col) as { reason: string }).reason === 'series'
```

Add the bus list the bus editor needs, from the same cache the canvas uses:

```tsx
  const { data: allBuses = [] } = useQuery({
    queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses,
  })
  const busNames = useMemo(
    () => allBuses.map(b => b.name as string).sort(), [allBuses],
  )
```

Finally, add the commit entry point. It is a stub in this task and Task 13
replaces its body with the real mutation — **the no-op skip and the validation
are this task's deliverable and stay**:

```tsx
  /**
   * The single commit path for a typed editor (D4). `fill` means the value
   * applies to the whole paste target (Ctrl/Cmd+Enter, or Ctrl/Cmd+click on a
   * boolean), not just this row.
   *
   * Task 13 replaces the request with the optimistic mutation; the no-op skip
   * and the validation below are load-bearing and stay.
   */
  const commitCell = (rowName: string, col: string, raw: string, fill: boolean) => {
    setEditing(null)
    const row = sorted.find(r => r.name === rowName)
    const currentText = row?.[col] == null ? '' : String(row[col])
    // No round-trip when the committed text equals the cell's current display
    // text — the same skip BottomPanel.tsx:1122 already does (criterion 2).
    if (!fill && raw === currentText) return

    const result = validateAndCoerce(col, raw, { componentClass, catalog, busNames: new Set(busNames) })
    if (!result.ok) { toast.error(result.error); return }

    const targets = fill && selectedRows.size > 0
      ? [...selectedRows] : [rowName]
    applyBulk(targets.map(n => ({ name: n, updates: { [col]: result.value } })))
  }
```

`applyBulk` is Task 13's export; until then, stub it as a call to the existing
`bulkMut.mutate` so this task is independently runnable:

```tsx
  // Replaced wholesale by Task 13's useGridMutation.
  const applyBulk = (rows: { name: string; updates: Record<string, unknown> }[]) => {
    const cols = new Set(rows.flatMap(r => Object.keys(r.updates)))
    const sameEverywhere = rows.length > 0 && cols.size === 1
      && rows.every(r => JSON.stringify(r.updates) === JSON.stringify(rows[0].updates))
    bulkMut.mutate(sameEverywhere
      ? { component_class: componentClass, names: rows.map(r => r.name), updates: rows[0].updates }
      : { component_class: componentClass, rows })
  }
```

- [ ] **Step 5: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: every test in the file passes; build exit 0. `networkApi.bulkUpdate`
must have been widened to accept the `rows` body — if `tsc` complains, extend its
parameter type in `api/network.ts:231` to
`{ component_class: string; names?: string[]; updates?: Record<string, unknown>; rows?: { name: string; updates: Record<string, unknown> }[] }`.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/BottomPanel.tsx \
        pypsa-gui/frontend/src/layout/BottomPanel.test.tsx \
        pypsa-gui/frontend/src/api/network.ts
git diff --cached --name-only
git commit -m "feat(gui): six typed cell editors sharing one commit path"
```

---

## Task 13: The optimistic mutation, its rollback, and the undo spacing

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx` (replace the `applyBulk` stub)
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (append rollback cases)

**Interfaces:**
- Consumes: `networkApi.bulkUpdate` widened in Task 12.
- Produces:

```ts
function applyBulk(rows: { name: string; updates: Record<string, unknown> }[]): void
```

**Context the implementer needs.**

**There is no optimistic-update precedent in this repo** (recon §4: no
`cancelQueries`, one `onMutate` touching only local state, one `setQueryData`
inside `onSuccess`), so D10 states the contract in full and this task implements
exactly it:

- `onMutate`: `await qc.cancelQueries({ queryKey: key })`; capture
  `previous = qc.getQueryData(key)` **and the current checkbox selection**;
  write the new rows with `setQueryData`; return both as context.
- `onError`: restore `previous`, restore the selection, surface the backend's
  `detail` as readable text, then **invalidate** `key` so the screen re-reads
  the truth rather than trusting the rollback.
- `onSuccess`: invalidate exactly the four scoped keys the existing bulk
  mutation already uses (`:285-289`) — the tab's own key,
  `nk(projectId,'undoInfo')`, the deliberately unscoped `['changelog']`, and
  `results`. **Not** `ALL_NETWORK_KEYS`.

`key = nk(projectId, TAB_TO_API_KEY[componentClass])` with `projectId` read via
`useUIStore.getState().currentProject` in the non-React callbacks — the parity
rule at `queryKeys.ts:16-22` is what makes a wrong id return `undefined` and
silently wipe a payload.

**D11's undo spacing.** Before issuing a **paste or fill** request, wait out the
remainder of 500 ms since the grid's own last successful mutation, so the
middleware's `claim_push_slot` (`undo_service.py:55,90-102`) always grants a new
snapshot. **Single-cell commits do not wait** and are allowed to coalesce,
matching text-editor behaviour and avoiding thrashing a 20-deep, 500 MB-capped
stack of full-netCDF snapshots (ruling 16).

While a mutation is in flight, cell editors are **disabled** — the pattern
`ModelHorizon.tsx:907-913` already uses to prevent a double-blur race.

- [ ] **Step 1: Append the rollback tests**

Append to `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx`:

```tsx
describe('AssetTable optimistic mutation — D10', () => {
  async function editFirstVNom(to: string) {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    await userEvent.clear(input)
    await userEvent.type(input, to)
    fireEvent.keyDown(input, { key: 'Enter' })
    return cell
  }

  it('sends the scalar form when every row gets the same value', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue({ updated: 1, fields: ['v_nom'] } as never)
    await editFirstVNom('123')
    const body = vi.mocked(networkApi).bulkUpdate.mock.calls[0][0]
    expect(body).toEqual({
      component_class: 'Bus', names: ['B0'], updates: { v_nom: 123 },
    })
  })

  it('shows the new value before the request resolves', async () => {
    let release: (v: unknown) => void = () => {}
    vi.mocked(networkApi).bulkUpdate.mockReturnValue(
      new Promise(res => { release = res }) as never,
    )
    const cell = await editFirstVNom('456')
    expect(cell.textContent).toContain('456')
    release({ updated: 1, fields: ['v_nom'] })
  })

  it('rolls the value back and reports the backend detail on failure', async () => {
    vi.mocked(networkApi).bulkUpdate.mockRejectedValue({
      response: { data: { detail: 'Column is numeric; got non-numeric value' } },
    })
    const cell = await editFirstVNom('456')
    await screen.findByText(/got non-numeric value/)
    expect(cell.textContent).toContain('380')      // the original v_nom
    expect(cell.textContent).not.toContain('456')
  })

  it('restores the checkbox selection after a failure', async () => {
    vi.mocked(networkApi).bulkUpdate.mockRejectedValue({
      response: { data: { detail: 'nope' } },
    })
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(rowCheckboxes()[0])
    await userEvent.click(rowCheckboxes()[1])
    expect(screen.getByText(/2 selected/)).toBeTruthy()
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    await userEvent.clear(input)
    await userEvent.type(input, '9')
    fireEvent.keyDown(input, { key: 'Enter' })
    await screen.findByText(/nope/)
    expect(screen.getByText(/2 selected/)).toBeTruthy()
  })

  it('formats a FastAPI error array instead of printing [object Object]', async () => {
    vi.mocked(networkApi).bulkUpdate.mockRejectedValue({
      response: { data: { detail: [{ loc: ['body', 'p_nom'], msg: 'not a number' }] } },
    })
    await editFirstVNom('7')
    await screen.findByText(/not a number/)
    expect(screen.queryByText(/\[object Object\]/)).toBeNull()
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
```

Expected: the optimistic cases fail — the stub neither writes the cache nor
restores the selection.

- [ ] **Step 3: Add the error formatter**

In `pypsa-gui/frontend/src/layout/BottomPanel.tsx`, above `AssetTable`:

```tsx
/**
 * FastAPI's `detail` is either a string or an array of validation objects.
 * Rendering the array directly gives "[object Object]", which
 * .cursor/rules/pypsa-gui-frontend.mdc:19 forbids and success criterion 10
 * tests for.
 */
function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail.map(d => {
      if (typeof d === 'string') return d
      const o = d as { loc?: unknown[]; msg?: string }
      const where = Array.isArray(o.loc) ? o.loc.filter(x => x !== 'body').join('.') : ''
      return where ? `${where}: ${o.msg ?? 'invalid'}` : (o.msg ?? 'invalid')
    })
    if (parts.length) return parts.join('; ')
  }
  return fallback
}
```

- [ ] **Step 4: Replace the `applyBulk` stub with the optimistic mutation**

Replace the whole stub from Task 12 and the old `bulkMut` (`:272-298`) with:

```tsx
  // ── Optimistic bulk write (spec D10, D11) ─────────────────────────────────
  // No in-repo precedent (recon §4), so the contract is implemented exactly as
  // the spec states it. The key MUST use the same projectId the useQuery that
  // populated the cache used, or getQueryData returns undefined and the
  // rollback wipes the payload (queryKeys.ts:16-22).
  const lastMutationAtRef = useRef(0)

  type BulkRow = { name: string; updates: Record<string, unknown> }
  type BulkBody = {
    component_class: string
    names?: string[]
    updates?: Record<string, unknown>
    rows?: BulkRow[]
  }
  type BulkCtx = { previous: unknown; selection: Set<string>; key: unknown[] }

  const gridMut = useMutation({
    mutationFn: (body: BulkBody) => networkApi.bulkUpdate(body),
    onMutate: async (body: BulkBody): Promise<BulkCtx> => {
      const projectId = useUIStore.getState().currentProject
      const key = nk(projectId, TAB_TO_API_KEY[componentClass] ?? componentClass.toLowerCase() + 's')
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData(key)
      const selection = new Set(selectedRows)

      const patch = new Map<string, Record<string, unknown>>()
      if (body.rows) for (const r of body.rows) patch.set(r.name, r.updates)
      else for (const n of body.names ?? []) patch.set(n, body.updates ?? {})

      qc.setQueryData(key, (old: Record<string, unknown>[] | undefined) =>
        (old ?? []).map(r => {
          const up = patch.get(r.name as string)
          return up ? { ...r, ...up } : r
        }))
      return { previous, selection, key }
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }, _body, ctx) => {
      if (ctx) {
        qc.setQueryData(ctx.key, ctx.previous)
        setSelectedRows(ctx.selection)
        // Re-read the truth rather than trusting the rollback.
        qc.invalidateQueries({ queryKey: ctx.key })
      }
      toast.error(formatDetail(e.response?.data?.detail, 'Bulk update failed'))
    },
    onSuccess: (r: { updated: number }) => {
      lastMutationAtRef.current = Date.now()
      const projectId = useUIStore.getState().currentProject
      const tableKey = TAB_TO_API_KEY[componentClass] ?? componentClass.toLowerCase() + 's'
      // Exactly the four scoped keys the previous bulk mutation used — NOT
      // ALL_NETWORK_KEYS. A bulk attribute write does not touch carriers,
      // snapshots or ac_pf_status.
      qc.invalidateQueries({ queryKey: [tableKey] })
      qc.invalidateQueries({ queryKey: nk(projectId, 'undoInfo') })
      qc.invalidateQueries({ queryKey: ['changelog'] })
      qc.invalidateQueries({ queryKey: nk(projectId, 'results') })
      toast.success(`Updated ${r.updated} ${componentClass.toLowerCase()}(s)`)
    },
  })

  /**
   * Issue one request for one gesture.
   *
   * `spaceUndo` is true for a paste or a fill: D11 requires each of those to
   * get its OWN undo step, and the middleware coalesces pushes inside a 500 ms
   * window (undo_service.py:55,90-102). Waiting out the remainder of that
   * window is a client-side timing concern, deliberately not a server flag.
   * Single-cell commits pass false and are allowed to coalesce (ruling 16).
   */
  const applyBulk = async (rows: BulkRow[], spaceUndo = false) => {
    if (rows.length === 0) return
    const cols = new Set(rows.flatMap(r => Object.keys(r.updates)))
    const first = JSON.stringify(rows[0].updates)
    const sameEverywhere = cols.size >= 1 && rows.every(r => JSON.stringify(r.updates) === first)

    if (spaceUndo) {
      const elapsed = Date.now() - lastMutationAtRef.current
      if (lastMutationAtRef.current > 0 && elapsed < 500) {
        await new Promise(res => setTimeout(res, 500 - elapsed))
      }
    }

    // The scalar form whenever every row gets the same value in every column
    // (every fill gesture); the row form otherwise. One gesture is always
    // exactly one request (D9).
    gridMut.mutate(sameEverywhere
      ? { component_class: componentClass, names: rows.map(r => r.name), updates: rows[0].updates }
      : { component_class: componentClass, rows })
  }
```

Update `commitCell`'s last line to pass the spacing flag:

```tsx
    applyBulk(targets.map(n => ({ name: n, updates: { [col]: result.value } })), fill)
```

Disable editors while a write is in flight, the `ModelHorizon.tsx:907-913`
pattern — in the cell render, guard the editor mount:

```tsx
                    {editing?.name === row.name && editing?.col === col && !gridMut.isPending ? (
```

- [ ] **Step 5: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: every test in the file passes; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/BottomPanel.tsx pypsa-gui/frontend/src/layout/BottomPanel.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): optimistic grid writes with rollback and one undo step per gesture"
```

---

## Task 14: Copy, paste, whole-batch rejection and the large-paste confirmation

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx` (`AssetTable` copy/paste handlers)
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (append clipboard cases)

**Interfaces:**
- Consumes: `parseTsv`, `serialiseTsv`, `guardCell`, `unguardCell`,
  `resolvePasteShape` (Task 9); `validateAndCoerce` (Task 8);
  `resolveEditability` (Task 7); `applyBulk` (Task 13); `confirmToast` from
  `../utils/toasts`.
- Produces: nothing importable.

**Context the implementer needs.**

**`ClipboardEvent` and `DataTransfer` are undefined in jsdom.** The event has to
be built by hand — the helper is given in Step 1 and is the only way these tests
can run.

**Clipboard I/O goes through `ClipboardEvent`, never `navigator.clipboard`**
(D6): no secure context, no user-gesture permission, no Firefox prompt.

**The paste target** (D7) is the checkbox row selection if non-empty, otherwise
the active cell's row, **in the grid's current `sorted` order** — not
`displayed`. That is what makes criterion 4 (3000 rows, all selected, one value,
rows past the 1000-row cap included) work, and Task 2's
`select-all past the cap` test is what proves the selection itself is uncapped.

**Rejection is whole-batch and names the offending cells** (D8). A paste changes
nothing if: the shape does not match; a target cell is not editable (`name`, an
`Output` attribute, an override read-only entry, or a series-shadowed cell); or
a value fails validation — most consequentially a bus name that is not an
existing bus, which nothing below the frontend would reject. The message names
**up to five** cells as `row / column` and states the count of the rest.

**Cells that survive validation but equal the current display text are dropped
as no-ops**; if none remain, no request is issued and the grid reports
"No changes". That is what makes criterion 3 (copy a 3×2 region, paste it back,
no request) true.

**The large-paste confirmation is a `confirmToast`, not a `Dialog`** (D18), at
**more than 200 target rows**. `Dialog.tsx:148` is capture-phase *and* calls
`stopPropagation()`, so while one is open it would swallow the grid's own
Escape.

- [ ] **Step 1: Append the clipboard tests**

Append to `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx`:

```tsx
/**
 * jsdom has neither ClipboardEvent nor DataTransfer, so both events are built
 * by hand. `bubbles: true` matters — the grid listens on its container.
 */
function firePaste(el: Element, text: string) {
  const e = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(e, 'clipboardData', {
    value: { getData: () => text, setData: () => {} },
  })
  el.dispatchEvent(e)
}

function fireCopy(el: Element): string {
  let written = ''
  const e = new Event('copy', { bubbles: true, cancelable: true })
  Object.defineProperty(e, 'clipboardData', {
    value: { getData: () => '', setData: (_t: string, v: string) => { written = v } },
  })
  el.dispatchEvent(e)
  return written
}

describe('AssetTable clipboard — D6, D7, D8', () => {
  async function activate(col = 'v_nom') {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector(`tbody tr td[data-col="${col}"]`) as HTMLElement
    await userEvent.click(cell)
    return cell
  }

  it('copy emits the active cell as TSV', async () => {
    const cell = await activate()
    expect(fireCopy(cell)).toBe('380')
  })

  it('a 1x1 paste fills every selected row in one request', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue({ updated: 5, fields: ['v_nom'] } as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click((await screen.findAllByRole('checkbox'))[0])   // select all
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '111') })
    expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalledTimes(1)
    const body = vi.mocked(networkApi).bulkUpdate.mock.calls[0][0]
    expect(body.names?.length).toBe(5)
    expect(body.updates).toEqual({ v_nom: 111 })
  })

  it('an Nx1 paste of distinct values uses the row form, one request', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue({ updated: 5, fields: ['v_nom'] } as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click((await screen.findAllByRole('checkbox'))[0])
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '1\r\n2\r\n3\r\n4\r\n5') })
    expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalledTimes(1)
    const body = vi.mocked(networkApi).bulkUpdate.mock.calls[0][0]
    expect(body.rows?.length).toBe(5)
    expect(body.names).toBeUndefined()
  })

  it('a row-count mismatch changes nothing and reports both counts', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click((await screen.findAllByRole('checkbox'))[0])   // 5 rows
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '1\r\n2') })
    await screen.findByText(/2 row\(s\).*5 row\(s\)/)
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('one invalid value rejects the whole paste and names the cell', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click((await screen.findAllByRole('checkbox'))[0])
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '1\r\n2\r\n12o0\r\n4\r\n5') })
    await screen.findByText(/B2 \/ v_nom/)
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('a paste into a read-only column changes nothing and names the cell', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="name"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, 'renamed') })
    await screen.findByText(/name/)
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('pasting the same values back issues no request (criterion 3)', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click((await screen.findAllByRole('checkbox'))[0])
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '380\r\n379\r\n378\r\n377\r\n376') })
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
    await screen.findByText(/no changes/i)
  })

  it('a paste over 200 rows asks for confirmation first (D18)', async () => {
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(300) as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click((await screen.findAllByRole('checkbox'))[0])
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '111') })
    // The confirmToast is on screen and nothing has been sent yet.
    await screen.findByText(/300/)
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })
})
```

Add `act` to the Testing Library import at the top of the file.

- [ ] **Step 2: Run and watch them fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
```

Expected: the clipboard cases fail — nothing listens for `copy` or `paste` yet.

- [ ] **Step 3: Wire copy and paste**

In `pypsa-gui/frontend/src/layout/BottomPanel.tsx`, add the imports:

```tsx
import { guardCell, parseTsv, resolvePasteShape, serialiseTsv, unguardCell } from '../utils/clipboardTsv'
import { confirmToast } from '../utils/toasts'
import { isNumericDtype } from '../utils/attributeCatalog'
```

Inside `AssetTable`, add the paste-target helper and the two handlers:

```tsx
  /**
   * The paste target (D7): the checkbox selection if non-empty, otherwise the
   * active cell's row — in `sorted` order, NOT `displayed`, so rows past the
   * 1000-row render cap are included (criterion 4).
   */
  const pasteTargetRows = useCallback((): string[] => {
    if (selectedRows.size > 0) {
      return sorted.map(r => r.name as string).filter(n => selectedRows.has(n))
    }
    return active ? [active.name] : []
  }, [selectedRows, sorted, active])

  /** True when a column's values are text, i.e. injection-guard territory. */
  const isStringColumn = useCallback((col: string): boolean => {
    const attr = catalog.get(col)
    if (!attr) return true
    return !isNumericDtype(attr.dtype) && !isBooleanDtype(attr.dtype)
  }, [catalog])

  const onCopy = (e: React.ClipboardEvent) => {
    if (editing) return                        // native input copy
    const rows = pasteTargetRows()
    if (!active || rows.length === 0) return
    e.preventDefault()
    const cols = selectedRows.size > 0 ? visibleCols : [active.col]
    const matrix = rows.map(name => {
      const row = sorted.find(r => r.name === name)
      return cols.map(c => guardCell(
        row?.[c] == null ? '' : String(row[c]), isStringColumn(c),
      ))
    })
    e.clipboardData.setData('text/plain', serialiseTsv(matrix))
  }

  const onPaste = (e: React.ClipboardEvent) => {
    if (editing) return                        // native input paste
    if (!active) return
    e.preventDefault()

    const matrix = parseTsv(e.clipboardData.getData('text/plain'))
    const targets = pasteTargetRows()
    const startCol = visibleCols.indexOf(active.col)
    const columnsAvailable = Math.max(visibleCols.length - startCol, 0)
    const shape = resolvePasteShape(matrix, targets.length, columnsAvailable)
    if (shape.kind === 'reject') { toast.error(shape.message); return }

    // Expand every shape into the same (row, col, raw) list so validation and
    // the no-op skip have exactly one implementation (D7, D8).
    const cells: { name: string; col: string; raw: string }[] = []
    if (shape.kind === 'fill') {
      for (const name of targets) cells.push({ name, col: active.col, raw: shape.value })
    } else if (shape.kind === 'rowwise') {
      targets.forEach((name, i) => cells.push({ name, col: active.col, raw: shape.values[i] }))
    } else {
      targets.forEach((name, i) => shape.matrix[i].forEach((raw, j) => {
        cells.push({ name, col: visibleCols[startCol + j], raw })
      }))
    }

    // Whole-batch validation (D8): editability first, then the value.
    const offenders: string[] = []
    const accepted: { name: string; col: string; value: unknown }[] = []
    for (const cell of cells) {
      const ed = editabilityOf(cell.name, cell.col)
      if (!ed.editable) { offenders.push(`${cell.name} / ${cell.col}`); continue }
      const raw = unguardCell(cell.raw, isStringColumn(cell.col))
      const result = validateAndCoerce(cell.col, raw, {
        componentClass, catalog, busNames: new Set(busNames),
      })
      if (!result.ok) { offenders.push(`${cell.name} / ${cell.col}`); continue }
      const row = sorted.find(r => r.name === cell.name)
      const currentText = row?.[cell.col] == null ? '' : String(row[cell.col])
      if (raw === currentText) continue                    // no-op, criterion 3
      accepted.push({ name: cell.name, col: cell.col, value: result.value })
    }

    if (offenders.length > 0) {
      const shown = offenders.slice(0, 5).join(', ')
      const rest = offenders.length > 5 ? ` and ${offenders.length - 5} more` : ''
      toast.error(`Paste rejected — cannot write ${shown}${rest}.`)
      return
    }
    if (accepted.length === 0) { toast('No changes'); return }

    // Group by row so one gesture is one request (D9).
    const byRow = new Map<string, Record<string, unknown>>()
    for (const a of accepted) {
      const existing = byRow.get(a.name) ?? {}
      existing[a.col] = a.value
      byRow.set(a.name, existing)
    }
    const rows = [...byRow].map(([name, updates]) => ({ name, updates }))

    // D18: a confirmToast, never a Dialog — Dialog.tsx:148 is capture-phase AND
    // calls stopPropagation, so while one is open it swallows the grid's Escape.
    if (targets.length > 200) {
      confirmToast(
        `Apply this paste to ${targets.length} rows?`,
        () => { applyBulk(rows, true) },
        { confirmLabel: 'Paste', danger: false },
      )
      return
    }
    applyBulk(rows, true)                                  // true → D11 spacing
  }
```

Attach both to the scrolling container that wraps the `<table>`, so a paste
anywhere in the grid is caught:

```tsx
    <div className="flex-1 overflow-auto" onCopy={onCopy} onPaste={onPaste}>
```

- [ ] **Step 4: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: every test in the file passes; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/BottomPanel.tsx pypsa-gui/frontend/src/layout/BottomPanel.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): clipboard copy/paste with whole-batch rejection and a large-paste confirm"
```

---

## Task 15: The Carriers tab renders through the shared grid

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx` (route Carriers to `AssetTable`; delete `CarriersTable` at `:1063-1237`)
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx` (append Carriers cases)

**Interfaces:**
- Consumes: everything Tasks 11–14 built.
- Produces: nothing importable.

**Context the implementer needs.** D16 absorbs `CarriersTable` into the shared
grid. Two behaviours are **carried over, not lost**:

1. The colour column keeps an `<input type="color">` editor — that is D4 editor
   row 1, keyed `Carrier.color`, already built in Task 12 and matching
   `BottomPanel.tsx:1204-1218`.
2. The tab keeps its help line (`:1229-1234`), which explains that CO₂ values
   are per MWh of **primary** energy.

Its remaining columns — `co2_emissions`, `nice_name`, `unit` — fall to editor
rows 5 and 6 and need nothing special.

**Tab labels do not change.** A chat `ui_event` frame requests a bottom tab **by
name** (`2026-07-26-chat-compare-and-navigate-design.md:22,26`), so renaming
`Carriers` would break that contract.

`Carrier` is already in `_COMPONENT_ATTRS` (`network.py:277-288`), so bulk edits
work on it — Task 1's `test_carrier_class_is_bulk_editable` pins that.

`TAB_TYPES` (`:61-70`) currently maps `Carriers` to `null`, which is what routes
it away from `AssetTable`; `TAB_TO_API_KEY` (`:71-78`) needs a `Carrier` entry.

- [ ] **Step 1: Append the Carriers tests**

Append to `pypsa-gui/frontend/src/layout/BottomPanel.test.tsx`:

```tsx
describe('Carriers tab absorbed into the shared grid — D16', () => {
  const CARRIERS = [
    { name: 'AC', co2_emissions: 0, color: '#ff0000', nice_name: 'AC', unit: '' },
    { name: 'gas', co2_emissions: 0.2, color: '#00ff00', nice_name: 'Gas', unit: '' },
  ]

  it('renders through AssetTable, with checkboxes like every other tab', async () => {
    vi.mocked(networkApi).getCarriers.mockResolvedValue(CARRIERS as never)
    renderPanel()
    await userEvent.click(screen.getByText('Carriers'))
    await screen.findByText('gas')
    expect(document.querySelectorAll('tbody input[type="checkbox"]').length).toBe(2)
  })

  it('is still called "Carriers" — a chat ui_event requests it by name', async () => {
    vi.mocked(networkApi).getCarriers.mockResolvedValue(CARRIERS as never)
    renderPanel()
    expect(screen.getByText('Carriers')).toBeTruthy()
  })

  it('keeps the colour picker on the color column', async () => {
    vi.mocked(networkApi).getCarriers.mockResolvedValue(CARRIERS as never)
    renderPanel()
    await userEvent.click(screen.getByText('Carriers'))
    await screen.findByText('gas')
    const cell = document.querySelector('tbody tr td[data-col="color"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    expect(cell.querySelector('input[type="color"]')).toBeTruthy()
  })

  it('keeps the CO2 help line', async () => {
    vi.mocked(networkApi).getCarriers.mockResolvedValue(CARRIERS as never)
    renderPanel()
    await userEvent.click(screen.getByText('Carriers'))
    expect(await screen.findByText(/primary/)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
```

Expected: the Carriers cases fail — the tab still renders `CarriersTable`.

- [ ] **Step 3: Route Carriers through `AssetTable` and delete `CarriersTable`**

In `pypsa-gui/frontend/src/layout/BottomPanel.tsx`:

1. In `TAB_TYPES` (`:61-70`), change the `Carriers` entry from `null` to
   `'Carrier'`.
2. In `TAB_TO_API_KEY` (`:71-78`), add `Carrier: 'carriers'`.
3. Confirm `TAB_COLUMNS` has a `Carriers` entry listing
   `['name', 'co2_emissions', 'color', 'nice_name', 'unit']`; add it if absent.
4. Delete the whole `CarriersTable` function (`:1063-1237`) and its
   `CarriersTableProps` interface.
5. Replace the render branch that mounted it with the shared grid plus the
   retained help line:

```tsx
            {colType && (
              <>
                <AssetTable
                  tab={activeTab}
                  componentClass={colType}
                  data={tableData[activeTab]}
                  defaultColumns={TAB_COLUMNS[activeTab]}
                  selectedName={selectedComponent?.name ?? null}
                  onRowClick={row => {
                    setSelectedComponent({ type: colType, name: row.name as string })
                    openRightPanel()
                  }}
                />
                {activeTab === 'Carriers' && (
                  // Carried over from the deleted CarriersTable (:1229-1234).
                  <p className="text-[10px] text-muted px-2 py-1.5 border-t border-border bg-bg-2/50">
                    CO₂ values are per MWh of <em>primary</em> energy — output-MWh
                    intensity is computed in the Emissions tab as
                    <code> co2_emissions / efficiency</code>.
                  </p>
                )}
              </>
            )}
```

Keep the per-row deep link into the asset-results tab (`:533-542`) exactly as
it is — it is one of four entry points
`2026-07-31-asset-detail-results-design.md:336-341` depends on.

- [ ] **Step 4: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/BottomPanel.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: every test passes; build exit 0. `tsc` catches any surviving reference
to the deleted `CarriersTable`.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/BottomPanel.tsx pypsa-gui/frontend/src/layout/BottomPanel.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): Carriers renders through the shared grid; CarriersTable deleted"
```

---

## Task 16: Delete `SimpleTable` and the bulk toolbar, then verify the whole scope

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx` — delete `SimpleTable` (`:555-656`) and the bulk-edit toolbar (`:433-470`) with its `onApply` (`:300-315`)

**Interfaces:**
- Consumes: everything.
- Produces: nothing. This task only removes and verifies.

**Context the implementer needs.** D29 rows 2–4. `SimpleTable` has **no caller**
and its own comment calls it "legacy, retained for any future read-only
callers". The bulk-edit toolbar is replaced by paste-respects-selection and the
Ctrl/Cmd+Enter fill gesture (ledger decision 5) — the capability is not lost,
its worse interface is.

Deleting the toolbar also removes `editCol`, `editValue`, their reset effect
(`:268-270`) and `onApply` (`:300-315`). The old `bulkMut` was already replaced
in Task 13; confirm no reference survives.

Rows 1 and 5 of D29 (`AssetPalette.tsx` and the three orphan `FIELD_MAP`
entries) landed in Scope C — this task's absence check covers all five so
criterion 39 is verified in one place.

- [ ] **Step 1: Delete the three blocks**

In `pypsa-gui/frontend/src/layout/BottomPanel.tsx`:

1. Delete the entire `SimpleTable` function (`:555-656`) and its props
   interface.
2. Delete the bulk-edit toolbar JSX (`:433-470`) — the whole
   `{selectedRows.size > 0 ? (…) : (…)}` block — and replace it with a hint
   that matches what the grid now does:

```tsx
        {selectedRows.size > 0 ? (
          <>
            <span className="text-muted">·</span>
            <span className="font-medium text-text">{selectedRows.size} selected</span>
            <span className="text-muted">
              ·  Paste to write every selected row, or Ctrl/Cmd+Enter in a cell to fill them.
            </span>
            <button
              onClick={() => setSelectedRows(new Set())}
              className="text-muted hover:text-danger flex items-center gap-0.5"
              title="Clear selection"
            ><X size={11} /></button>
          </>
        ) : (
          <span className="text-muted">
            ·  Click a cell to edit it. Select rows to paste or fill across many.
          </span>
        )}
```

3. Delete `editCol`, `editValue`, their `useEffect` reset (`:266-270`) and
   `onApply` (`:300-315`).

- [ ] **Step 2: Prove all five deletions are absent**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
grep -rn "AssetPalette\b" pypsa-gui/frontend/src --include=*.tsx --include=*.ts \
  | grep -v "AssetPaletteInline" | grep -v "^\s*//" || echo "AssetPalette: ABSENT"
grep -rn "SimpleTable\|CarriersTable" pypsa-gui/frontend/src || echo "SimpleTable/CarriersTable: ABSENT"
grep -n "onApply\|editCol\|editValue" pypsa-gui/frontend/src/layout/BottomPanel.tsx || echo "toolbar: ABSENT"
grep -nE "^  (link|generator|load):" pypsa-gui/frontend/src/layout/CreationForm.tsx || echo "orphan FIELD_MAP keys: ABSENT"
```

Expected: four `ABSENT` lines, or only comment hits that mention the deletion.

- [ ] **Step 3: Prove `coerce.ts` is untouched (criterion 41)**

```bash
git diff --stat e8614a35 -- pypsa-gui/frontend/src/utils/coerce.ts
```

Expected: **no output**.

- [ ] **Step 4: Run both suites in full**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npm test
PATH="$PIXI_BIN:$PATH" npm run build
cd "../backend"
"$PIXI_BIN/python" -m pytest
```

Expected:
- Frontend: **0 failures**, test count no lower than the 691 baseline plus this
  plan's new tests (`attributeCatalog`, `gridEdit`, `clipboardTsv`,
  `BottomPanel`, `BusAutocomplete`, `CarrierSelect`).
- `npm run build`: exit 0.
- Backend: **0 failures**, passed count no lower than the measured **2286**.
  Do not compare against the spec's stale `2183` — see Global Constraints.

- [ ] **Step 5: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/BottomPanel.tsx
git diff --cached --name-only
git commit -m "chore(gui): delete SimpleTable and the bulk-edit toolbar the grid replaces"
```

---

## Scope A is done when

- `npm test` is green with 0 failures and a count no lower than the 691
  baseline plus this plan's new tests.
- `npm run build` exits 0.
- `"$PIXI_BIN/python" -m pytest` reports 0 failures and no fewer than 2286
  passed.
- `git diff e8614a35 -- pypsa-gui/frontend/src/utils/coerce.ts` is empty, and
  `coerce.test.ts`'s ten tests pass unmodified (criterion 41).
- `AssetPalette.tsx`, `SimpleTable`, `CarriersTable`, the bulk-edit toolbar and
  the three orphan `FIELD_MAP` entries are all absent (criterion 39).
- Manually, in the running app (`bash pypsa-gui/start.sh`), success criteria
  1–28: click a cell and it edits; Enter commits and moves down; Escape discards
  **and leaves an open slide panel open**; a 1×1 paste onto a selection of 3000
  rows writes all of them in one request; an `Nx1` paste writes each row its own
  value and produces one History row; a bad value rejects the batch naming
  `row / column`; a failed write restores both the values and the checkboxes;
  blanking `p_nom_max` gives `inf`; typing `inf` gives `inf`; a bus cell opens
  the dropdown and follows the scroll; an unknown bus is refused with no
  "created automatically" line; `p_nom_extendable` renders a checkbox that
  Ctrl/Cmd+click fills; `control` offers exactly PQ/PV/Slack; a series-shadowed
  cell is dimmed, badged and unselectable as a paste target; the Lines tab's `r`
  header reads `r (Ω)` with the per-km tooltip; a >200-row paste asks first; the
  Carriers tab renders in the shared grid with its colour picker.

**Not claimed by this plan:** the desktop `.app` is stale until `npm run build`
is followed by `bash pypsa-gui/build-macos.sh` (`CLAUDE.md:56-84`).

## What Plan B inherits

- `utils/attributeCatalog.ts` — Plan B **appends** D22's six reveal rules here.
- `hooks/useCatalog.ts` and `GET /api/network/catalog/{component}`, already
  characterised by `tests/test_attribute_catalog.py`.
- The nine-field catalog payload, including `default_text`, which D23's
  "+ Add parameter" picker needs so an `inf` default is not erased into a blank.
- The measured baselines in Global Constraints, which supersede the spec's
  `2183 / 23`.

