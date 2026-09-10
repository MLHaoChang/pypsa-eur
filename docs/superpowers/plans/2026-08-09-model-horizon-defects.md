# Model Horizon Defect Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the eleven defects catalogued in `docs/superpowers/specs/2026-08-09-model-horizon-defects-design.md`, so the Model Horizon tab writes to the row the user edited, reports the network's real state, and stops offering two discount paths that disagree.

**Architecture:** Pure display/derivation logic moves out of the 1,489-line `ModelHorizon.tsx` into a new sibling module `modelHorizonModel.ts`, tested directly as functions — the same split `topologyLayoutStore.ts` already uses for `TopologyCanvas`. The page keeps rendering and mutations. Two backend endpoints gain behaviour (`GET /snapshots` reports resolution; `POST /investment_periods` stops flattening per-period ranges); no endpoint changes its contract in a breaking way.

**Tech Stack:** React 18 + TanStack Query + Zustand + Tailwind (frontend, vitest/jsdom); FastAPI + PyPSA + pandas (backend, pytest).

## Global Constraints

- **Repo root:** `~/Desktop/Code Test/pypsa-eur`. All paths below are relative to it.
- **Branch:** `feature/local-app-impl`. **This worktree is shared with other live sessions.** Before EVERY commit, re-run `git branch --show-current` — do not trust an earlier answer. Commit path-limited (`git commit <paths>`), never `git add -A`.
- **Every task is TDD.** Write the failing test, run it, capture the real failure output, then implement. The task report carries a **TDD Evidence** section with the RED command + its output and the GREEN command + its output. There is no "too simple to test" exemption.
- **Frontend test command** (from repo root):
  `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run <file>`
- **Frontend type check** (from repo root):
  `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json`
- **Backend test command** (from repo root):
  `pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v`
- **Never hardcode an interpreter path.** Use `pixi run python` / `npx` via the PATH prefix above.
- **One new backend test file only** — `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`, added in Task 2 and extended in Task 4. After Task 4, run the FULL backend suite once (`pixi run python -m pytest pypsa-gui/backend/tests -q`) to confirm the new file does not cross-contaminate the shared in-memory network singleton.
- **Do not rebuild the macOS app.** These changes are source-only; the DMG stays stale and that is fine to state.
- **Out of scope:** the page's structural redesign, removing `cfg.investment_periods` as a concept.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pypsa-gui/frontend/src/pages/modelHorizonModel.ts` | **New.** Pure derivations: snapshot-weight keys, table rows, resolution label, PV factor, horizon range label. No React, no network. | 1,2,3,6 |
| `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts` | **New.** Unit tests for the above. | 1,2,3,6 |
| `pypsa-gui/frontend/src/pages/ModelHorizon.tsx` | Rendering + mutations only. Loses the manual discount calculator and the dead scaler mutation. | 1,2,3,6 |
| `pypsa-gui/frontend/src/api/types.ts` | `SnapshotInfo` gains `freq`; `weightings` value type widened. | 2 |
| `pypsa-gui/backend/routers/network.py` | `_infer_snapshot_freq` helper; ambiguous-key warning; per-period range preservation; `had_custom_weights` in the sample_weeks response. | 1,2,4,6 |
| `pypsa-gui/backend/tests/test_model_horizon_endpoints.py` | **New.** Backend coverage for resolution inference and per-period range preservation. | 2,4 |
| `pypsa-gui/frontend/src/layout/properties/cardKit.tsx` | `useBuildYearOptions` reads the network endpoint. | 5 |

---

### Task 1: Snapshot-weight keys address the right period (B1, B2)

**Files:**
- Create: `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`
- Create: `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts`
- Modify: `pypsa-gui/frontend/src/pages/ModelHorizon.tsx:357-363` (mutation), `:1404-1460` (table)
- Modify: `pypsa-gui/backend/routers/network.py:1285-1317` (warning log)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type WeightingRow = Record<string, unknown>`
  - `snapshotWeightKey(row: WeightingRow, isMultiPeriod: boolean, fallbackIso: string): string`
  - `interface WeightingTableRow { key: string; period: string | null; iso: string; objective: number; generators: number; stores: number }`
  - `buildWeightingRows(pageRows: WeightingRow[], allSnapshots: string[], isMultiPeriod: boolean, pageStart: number): WeightingTableRow[]`

**Background the implementer needs:** `GET /api/network/snapshots` returns `weightings` as the output of `df_to_json(n.snapshot_weightings)`, which calls `reset_index()`. On a **flat** network the index is named `snapshot`, so each row has a `snapshot` key holding an ISO string. On a **multi-period** network the index is a MultiIndex with levels named `period` / `timestep`, so each row has `period` (a number) and `timestep` (an ISO string) and **no** `snapshot` key. `PATCH /api/network/snapshots/weightings` accepts either `"2030|2024-01-01T00:00:00"` or a bare `"2024-01-01T00:00:00"`; the bare form is registered once per period and resolves to the LAST one.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts`:

```ts
// The Model Horizon weightings table addressed the wrong row on multi-period
// networks. `df_to_json(n.snapshot_weightings)` emits `period` / `timestep`
// columns for a MultiIndex — never `snapshot` or `name` — so the page's
// `wm.snapshot ?? wm.name ?? …` chain fell through to a bare ISO. The backend
// registers bare ISO keys once per period, last-write-wins, so a bare key
// resolves to the LAST period: editing 2030 wrote 2050.
import { describe, it, expect } from 'vitest'
import {
  snapshotWeightKey,
  buildWeightingRows,
  type WeightingRow,
} from './modelHorizonModel'

const FLAT_ROW: WeightingRow = {
  snapshot: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1,
}
const MULTI_ROW: WeightingRow = {
  period: 2030, timestep: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1,
}

describe('snapshotWeightKey', () => {
  it('returns the bare ISO on a flat network', () => {
    expect(snapshotWeightKey(FLAT_ROW, false, 'FALLBACK')).toBe('2024-01-01T00:00:00')
  })

  it('returns the period-qualified key on a multi-period network', () => {
    expect(snapshotWeightKey(MULTI_ROW, true, 'FALLBACK'))
      .toBe('2030|2024-01-01T00:00:00')
  })

  it('falls back to the positional ISO when the row carries neither shape', () => {
    expect(snapshotWeightKey({ objective: 1 }, false, '2024-06-01T12:00:00'))
      .toBe('2024-06-01T12:00:00')
  })

  it('does not emit a bare key on multi-period even if `snapshot` is also present', () => {
    const hybrid: WeightingRow = { ...MULTI_ROW, snapshot: '2024-01-01T00:00:00' }
    expect(snapshotWeightKey(hybrid, true, 'FALLBACK')).toBe('2030|2024-01-01T00:00:00')
  })
})

describe('buildWeightingRows', () => {
  it('produces distinct keys for the same hour under different periods', () => {
    // 3 periods x 24 hours = 72 rows on ONE page (PAGE_SIZE is 100). With the
    // old bare-ISO key this yielded 24 unique keys and 48 React collisions.
    const hours = Array.from({ length: 24 }, (_, h) =>
      `2024-01-01T${String(h).padStart(2, '0')}:00:00`)
    const periods = [2030, 2040, 2050]
    const pageRows: WeightingRow[] = periods.flatMap(p =>
      hours.map(ts => ({ period: p, timestep: ts, objective: 1, generators: 1, stores: 1 })))
    const allSnapshots = periods.flatMap(() => hours)

    const rows = buildWeightingRows(pageRows, allSnapshots, true, 0)

    expect(rows).toHaveLength(72)
    expect(new Set(rows.map(r => r.key)).size).toBe(72)
    expect(rows[0].key).toBe('2030|2024-01-01T00:00:00')
    expect(rows[24].key).toBe('2040|2024-01-01T00:00:00')
    expect(rows[48].key).toBe('2050|2024-01-01T00:00:00')
  })

  it('exposes the period for display on multi-period and null on flat', () => {
    expect(buildWeightingRows([MULTI_ROW], ['2024-01-01T00:00:00'], true, 0)[0].period)
      .toBe('2030')
    expect(buildWeightingRows([FLAT_ROW], ['2024-01-01T00:00:00'], false, 0)[0].period)
      .toBeNull()
  })

  it('reads the displayed timestamp from `timestep` on multi-period', () => {
    expect(buildWeightingRows([MULTI_ROW], ['IGNORED'], true, 0)[0].iso)
      .toBe('2024-01-01T00:00:00')
  })

  it('offsets into allSnapshots by pageStart when the row carries no timestamp', () => {
    const bare: WeightingRow = { objective: 3, generators: 1, stores: 1 }
    const rows = buildWeightingRows([bare], ['a', 'b', 'c'], false, 2)
    expect(rows[0].iso).toBe('c')
    expect(rows[0].key).toBe('c')
  })

  it('coerces weight columns to numbers and defaults missing ones to 1', () => {
    const partial: WeightingRow = { snapshot: 'x', objective: 2.5 }
    const [row] = buildWeightingRows([partial], ['x'], false, 0)
    expect(row.objective).toBe(2.5)
    expect(row.generators).toBe(1)
    expect(row.stores).toBe(1)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: FAIL — `Failed to resolve import "./modelHorizonModel"`. Paste the real output into the TDD Evidence section.

- [ ] **Step 3: Create the model module**

Create `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`:

```ts
// Pure derivations for the Model Horizon page. No React, no network calls —
// everything here is a function of the `GET /api/network/snapshots` payload
// plus the solver config, so it can be tested without rendering the 1,489-line
// page. Same split as `topologyLayoutStore.ts` for TopologyCanvas.

/** One row of `SnapshotInfo.weightings`, i.e. `df_to_json(n.snapshot_weightings)`. */
export type WeightingRow = Record<string, unknown>

/**
 * The key that identifies one snapshot-weighting row to
 * `PATCH /api/network/snapshots/weightings`.
 *
 * Flat networks: the index is named `snapshot`, so the row carries an ISO
 * string under that key and the bare ISO is unambiguous.
 *
 * Multi-period networks: the index is a MultiIndex named `period` / `timestep`,
 * so there is NO `snapshot` key. The bare timestep is ambiguous — the backend
 * registers it once per period and last-write-wins, meaning a bare key always
 * resolves to the LAST period. Emit the period-qualified `period|iso` form,
 * which the backend documents as canonical for multi-period clients.
 */
export function snapshotWeightKey(
  row: WeightingRow,
  isMultiPeriod: boolean,
  fallbackIso: string,
): string {
  if (isMultiPeriod) {
    const period = row.period
    const timestep = row.timestep
    if (period != null && timestep != null) {
      return `${String(period)}|${String(timestep)}`
    }
  }
  const flat = row.snapshot ?? row.name
  if (flat != null) return String(flat)
  return fallbackIso
}

/** A weightings-table row, ready to render. */
export interface WeightingTableRow {
  /** PATCH key and React key. Distinct per (period, timestep). */
  key: string
  /** Investment period as a display string, or null on flat networks. */
  period: string | null
  /** Timestamp shown in the Snapshot column. */
  iso: string
  objective: number
  generators: number
  stores: number
}

function weight(row: WeightingRow, col: string): number {
  const v = row[col]
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : 1
}

/**
 * Turn one page of `SnapshotInfo.weightings` into render-ready rows.
 *
 * `allSnapshots` is the full `SnapshotInfo.snapshots` array and `pageStart` the
 * index of the page's first row within it — together they supply the positional
 * fallback for rows that carry no timestamp of their own.
 */
export function buildWeightingRows(
  pageRows: WeightingRow[],
  allSnapshots: string[],
  isMultiPeriod: boolean,
  pageStart: number,
): WeightingTableRow[] {
  return pageRows.map((row, i) => {
    const fallbackIso = String(allSnapshots[pageStart + i] ?? '')
    const iso = String(row.timestep ?? row.snapshot ?? row.name ?? fallbackIso)
    const period = isMultiPeriod && row.period != null ? String(row.period) : null
    return {
      key: snapshotWeightKey(row, isMultiPeriod, fallbackIso),
      period,
      iso,
      objective: weight(row, 'objective'),
      generators: weight(row, 'generators'),
      stores: weight(row, 'stores'),
    }
  })
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: PASS, 9 tests.

- [ ] **Step 5: Wire the mutation to the new key**

In `pypsa-gui/frontend/src/pages/ModelHorizon.tsx`, add to the existing imports:

```tsx
import { buildWeightingRows, type WeightingRow } from './modelHorizonModel'
```

Replace the `updateOneWeight` mutation (currently at `:357-363`) — the argument is no longer an ISO, so the field is renamed:

```tsx
  const updateOneWeight = useMutation({
    mutationFn: (args: { key: string; objective: number }) =>
      networkApi.updateSnapshotWeightings({ updates: { [args.key]: { objective: args.objective } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'snapshots') }),
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update weight'),
  })
```

- [ ] **Step 6: Wire the table to the new rows**

Still in `ModelHorizon.tsx`, immediately after the existing `pageRows` memo (`:580-583`), add:

```tsx
  const weightingRows = useMemo(
    () => buildWeightingRows(
      pageRows as WeightingRow[],
      snap?.snapshots ?? [],
      snapshotsAreMulti,
      pageStart,
    ),
    [pageRows, snap?.snapshots, snapshotsAreMulti, pageStart],
  )
```

In the weightings table header (`:1407-1414`), add a Period column ahead of Snapshot:

```tsx
                <tr className="border-b border-border">
                  {snapshotsAreMulti && (
                    <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
                  )}
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Snapshot</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Objective</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Generators</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Stores</th>
                </tr>
```

Replace the whole `<tbody>` body (`:1416-1457`) with:

```tsx
                {weightingRows.map((row, i) => (
                  <tr key={row.key} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    {snapshotsAreMulti && (
                      <td className="px-2 py-1 font-mono text-[11px]">{row.period}</td>
                    )}
                    <td className="px-2 py-1 font-mono text-[11px] whitespace-nowrap">{row.iso}</td>
                    <td className="px-2 py-1 text-right">
                      <input
                        key={`sw-${row.key}-${row.objective}`}
                        type="number"
                        step="0.1"
                        min={0}
                        defaultValue={row.objective.toFixed(2)}
                        // Disabled while the shared per-snapshot mutation is in
                        // flight — same double-blur race protection as the
                        // per-period table above. Cosmetic only.
                        disabled={updateOneWeight.isPending}
                        onBlur={e => {
                          const v = parseFloat(e.target.value)
                          if (!Number.isFinite(v) || v < 0) {
                            e.target.value = row.objective.toFixed(2)
                            return
                          }
                          if (v !== row.objective) {
                            updateOneWeight.mutate({ key: row.key, objective: v })
                          }
                        }}
                        className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                      />
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right text-muted">
                      {row.generators.toFixed(2)}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right text-muted">
                      {row.stores.toFixed(2)}
                    </td>
                  </tr>
                ))}
```

Also bump the table's `minWidth` so the extra column doesn't crush the others — change `style={{ minWidth: 480 }}` on that table (`:1406`) to `style={{ minWidth: snapshotsAreMulti ? 560 : 480 }}`.

- [ ] **Step 7: Type-check the frontend**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```
Expected: no errors. If `wm` is now unused, delete the leftover declaration.

- [ ] **Step 8: Make an ambiguous backend write visible**

In `pypsa-gui/backend/routers/network.py`, in `update_snapshot_weightings`, initialise a counter just before the pass-1 loop (immediately after `pending: list[tuple[object, str, float]] = []`, `:1287`):

```python
        # A bare ISO key on a MultiIndex network is ambiguous — it is
        # registered once per period and last-write-wins, so it silently
        # resolves to the LAST period. The GUI now sends `period|iso`; anything
        # still sending bare keys (older clients, chat tools) gets recorded in
        # the audit log rather than writing to a surprising row in silence.
        ambiguous_bare_keys = 0
```

Inside that loop, in the `if key in iso_to_idx:` branch (`:1291-1292`):

```python
            if key in iso_to_idx:
                idx = iso_to_idx[key]
                if is_multi and "|" not in str(key):
                    ambiguous_bare_keys += 1
```

And extend the existing `change_log_service.log(...)` call (`:1314-1317`) so the warning rides along:

```python
        change_log_service.log(
            "update", "Network", "snapshot_weightings",
            f"Updated snapshot weightings: all={all_val}, per-row updates={applied}"
            + (f" — WARNING: {ambiguous_bare_keys} bare-ISO key(s) on a multi-period "
               "network each resolved to the LAST period; send `period|iso` to target "
               "a specific period." if ambiguous_bare_keys else ""),
        )
```

- [ ] **Step 9: Re-run both suites**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: PASS, 9 tests.

Run (from repo root):
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_network_endpoints.py -q 2>/dev/null || pixi run python -m pytest pypsa-gui/backend/tests -q -k "weighting"
```
Expected: PASS — the log-string change must not break any existing assertion on changelog text.

- [ ] **Step 10: Commit**

```bash
git branch --show-current   # confirm feature/local-app-impl before committing
git commit pypsa-gui/frontend/src/pages/modelHorizonModel.ts \
           pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts \
           pypsa-gui/frontend/src/pages/ModelHorizon.tsx \
           pypsa-gui/backend/routers/network.py \
  -m "fix(gui): snapshot-weight edits stop landing on the wrong period

The weightings table keyed its rows off \`wm.snapshot ?? wm.name\`, but a
MultiIndex network serialises as period/timestep columns — neither key
exists, so it fell through to a bare ISO. The backend registers bare ISO
keys once per period, last-write-wins, so editing the 2030 row wrote
2050's weight and the edited row refetched unchanged.

Emit the period-qualified key the backend documents as canonical, show
the period in its own column, and record a warning when a bare key still
arrives on a multi-period network.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The Resolution card reports the network (B3)

**Files:**
- Modify: `pypsa-gui/backend/routers/network.py:469` (new helper nearby), `:1029-1068` (both return branches)
- Create: `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`
- Modify: `pypsa-gui/frontend/src/api/types.ts:113-127`
- Modify: `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`, `modelHorizonModel.test.ts`
- Modify: `pypsa-gui/frontend/src/pages/ModelHorizon.tsx:627,659-663`

**Interfaces:**
- Consumes: `modelHorizonModel.ts` from Task 1.
- Produces:
  - Python: `_infer_snapshot_freq(n) -> str | None` in `routers/network.py`
  - JSON: `SnapshotInfo.freq: string | null`
  - TS: `resolutionLabel(freq: string | null | undefined): string`

**Background the implementer needs:** `freq` in `ModelHorizon.tsx` is `useState('h')` and is only ever set by the single-period `<select>`, which does not render in multi-period mode. The stat card reads it as though it were network state. `pd.infer_freq` returns `None` for the representative-week index (contiguous 168-hour blocks with gaps between them) even though its resolution is genuinely hourly — hence the modal-delta fallback.

- [ ] **Step 1: Write the failing backend test**

Create `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`:

```python
"""
Model Horizon tab endpoints: snapshot resolution reporting and investment-period
range preservation.

The Resolution stat card used to render a frontend form field that was never
seeded from the network — it read "Hourly (h)" for a 3-hourly MultiIndex and for
a Daily flat network after any reload. `GET /snapshots` now reports the real
resolution and the card renders that.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from routers.network import _infer_snapshot_freq


def _multi_index(periods, block):
    import numpy as np
    period_level = np.concatenate([np.full(len(block), p) for p in periods])
    timestep_level = pd.DatetimeIndex(np.concatenate([block.values for _ in periods]))
    mi = pd.MultiIndex.from_arrays(
        [period_level, timestep_level], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    return mi


def test_infer_freq_flat_hourly():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=24, freq="h"))
    assert _infer_snapshot_freq(n) == "h"


def test_infer_freq_flat_daily():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=10, freq="D"))
    assert _infer_snapshot_freq(n) == "D"


def test_infer_freq_multi_period_reads_one_period_not_the_seam():
    # Naively inferring over the flattened timestep level sees the jump from
    # period 2030's last hour back to period 2040's first — an irregular index.
    # Only period 0's slice is meaningful.
    block = pd.date_range("2024-01-01", periods=8, freq="3h")
    n = pypsa.Network()
    n.set_snapshots(_multi_index([2030, 2040, 2050], block))
    assert _infer_snapshot_freq(n) == "3h"


def test_infer_freq_falls_back_to_modal_delta_for_representative_weeks():
    # Two disjoint 168-hour blocks: pd.infer_freq gives None, but the resolution
    # is hourly and the card should say so.
    a = pd.date_range("2024-01-01", periods=168, freq="h")
    b = pd.date_range("2024-03-04", periods=168, freq="h")
    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(a.append(b)))
    assert _infer_snapshot_freq(n) == "h"


def test_infer_freq_returns_none_for_a_single_snapshot():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=1, freq="h"))
    assert _infer_snapshot_freq(n) is None


def test_snapshots_endpoint_reports_freq(client, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=12, freq="6h"))
    n.add("Bus", "B1")
    install_network(n)

    body = client.get("/api/network/snapshots").json()
    assert body["freq"] == "6h"
```

- [ ] **Step 2: Run it to verify it fails**

Run (from repo root):
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v
```
Expected: FAIL at collection — `ImportError: cannot import name '_infer_snapshot_freq' from 'routers.network'`.

- [ ] **Step 3: Implement the helper**

In `pypsa-gui/backend/routers/network.py`, directly after `_build_period_multiindex` (which ends at `:488`):

```python
def _infer_snapshot_freq(n) -> str | None:
    """
    The snapshot index's resolution, as a pandas offset alias ("h", "3h", "D").

    The Model Horizon page used to render its own form state here, which was
    seeded to "h" at mount and never read back from the network — so a
    3-hourly MultiIndex and a Daily flat index both reported "Hourly (h)".

    MultiIndex networks are measured over the FIRST period's slice only: the
    flattened timestep level contains a discontinuity at each period seam
    (period P's last hour → period P+1's first), which would read as irregular.

    `pd.infer_freq` is tried first because it names calendar frequencies ("D",
    "MS", "W") that a raw timedelta cannot. It returns None for a
    representative-week index — contiguous 168-hour blocks separated by gaps —
    whose resolution is nevertheless hourly, so fall back to the modal
    successive delta. Returns None when neither resolves; the UI renders that
    as "irregular" rather than guessing.
    """
    sns = n.snapshots
    if isinstance(sns, pd.MultiIndex):
        level0 = sns.get_level_values(0)
        if len(level0) == 0:
            return None
        first = level0[0]
        idx = pd.DatetimeIndex(sns[level0 == first].get_level_values(1))
    else:
        idx = pd.DatetimeIndex(sns)
    if len(idx) < 2:
        return None
    try:
        inferred = pd.infer_freq(idx)
    except (ValueError, TypeError):
        inferred = None
    if inferred:
        return inferred
    deltas = idx.to_series().diff().dropna()
    if deltas.empty:
        return None
    modal = deltas.mode()
    if modal.empty:
        return None
    hours = modal.iloc[0].total_seconds() / 3600.0
    if hours <= 0:
        return None
    if hours == 1.0:
        return "h"
    if float(hours).is_integer():
        return f"{int(hours)}h"
    return None
```

- [ ] **Step 4: Add `freq` to both return branches**

In `get_snapshots` (`:1029-1068`), compute it once alongside the other derived values (after `can_sample_weeks = …`, `:1041`):

```python
    freq = _infer_snapshot_freq(n)
```

Add `"freq": freq,` to **both** returned dicts — the MultiIndex branch (`:1053-1061`) and the flat branch (`:1064-1068`).

- [ ] **Step 5: Run the backend test to verify it passes**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v
```
Expected: PASS, 6 tests.

- [ ] **Step 6: Write the failing frontend test**

Append to `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts`:

```ts
import { resolutionLabel } from './modelHorizonModel'

describe('resolutionLabel', () => {
  it('names the known frequencies', () => {
    expect(resolutionLabel('h')).toBe('Hourly (h)')
    expect(resolutionLabel('3h')).toBe('3-hourly')
    expect(resolutionLabel('D')).toBe('Daily (D)')
    expect(resolutionLabel('MS')).toBe('Monthly (MS)')
  })

  it('matches case-insensitively — pandas may emit "H" rather than "h"', () => {
    expect(resolutionLabel('H')).toBe('Hourly (h)')
  })

  it('says irregular rather than guessing when the backend could not infer', () => {
    expect(resolutionLabel(null)).toBe('Irregular')
    expect(resolutionLabel(undefined)).toBe('Irregular')
  })

  it('passes an unrecognised alias through verbatim', () => {
    expect(resolutionLabel('17min')).toBe('17min')
  })
})
```

- [ ] **Step 7: Run it to verify it fails**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: FAIL — `resolutionLabel is not exported by ./modelHorizonModel`.

- [ ] **Step 8: Implement `resolutionLabel`**

Append to `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`:

```ts
/**
 * The frequency options the snapshot constructor offers, and the labels the
 * Resolution stat card renders. Kept here rather than in the page so both the
 * card and the `<select>` read one list.
 */
export const FREQ_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'h',   label: 'Hourly (h)' },
  { value: '3h',  label: '3-hourly' },
  { value: '6h',  label: '6-hourly' },
  { value: 'D',   label: 'Daily (D)' },
  { value: 'W',   label: 'Weekly (W)' },
  { value: 'MS',  label: 'Monthly (MS)' },
]

/**
 * Human label for the resolution reported by `GET /snapshots`.
 *
 * Matching is case-insensitive because pandas has emitted both "h" and "H" for
 * hourly across versions. An alias we don't recognise passes through verbatim —
 * showing "17min" is honest; mapping it to "Hourly" is not. `null` means the
 * backend could not infer one, which is a real state (a two-snapshot network,
 * or a genuinely irregular index) and reads as "Irregular".
 */
export function resolutionLabel(freq: string | null | undefined): string {
  if (!freq) return 'Irregular'
  const hit = FREQ_OPTIONS.find(o => o.value.toLowerCase() === freq.toLowerCase())
  return hit ? hit.label : freq
}
```

- [ ] **Step 9: Run it to verify it passes**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: PASS, 13 tests.

- [ ] **Step 10: Widen the API type**

In `pypsa-gui/frontend/src/api/types.ts`, update `SnapshotInfo` (`:113-127`):

```ts
export interface SnapshotInfo {
  count: number; snapshots: string[]
  // Rows of `df_to_json(n.snapshot_weightings)`. Flat networks carry a
  // `snapshot` ISO string; MultiIndex networks carry `period` (number) +
  // `timestep` (ISO string) instead — hence the mixed value type.
  weightings: Record<string, number | string>[]
  // Present only when n.snapshots is a MultiIndex (multi-period planning).
  // Parallel to `snapshots`: periods[i] is the investment period that
  // snapshots[i]'s timestep belongs to.
  periods?: Array<number | string>
  // Min start / max end datetime across all flat uploaded time-series
  // (_user_ts). null when nothing flat has been uploaded. The Model Horizon
  // page uses this to default the snapshot range to the uploaded data.
  ts_start?: string | null
  ts_end?: string | null
  // True when a flat uploaded profile spans all 12 months of one year at
  // hourly resolution — gates the representative-week sampler.
  can_sample_weeks?: boolean
  // Snapshot resolution as a pandas offset alias ("h", "3h", "D"), measured
  // from the FIRST investment period on MultiIndex networks. null when the
  // backend could not infer one. The Resolution stat card renders this —
  // it used to render the page's own form state, which was never seeded
  // from the network.
  freq?: string | null
}
```

- [ ] **Step 11: Point the card at the network**

In `pypsa-gui/frontend/src/pages/ModelHorizon.tsx`:

1. Delete the local `FREQ_OPTIONS` constant (`:58-65`) and import it from the model module instead — extend the Task 1 import line:

```tsx
import { buildWeightingRows, resolutionLabel, FREQ_OPTIONS, type WeightingRow } from './modelHorizonModel'
```

2. Replace the `freqLabel` derivation (`:627`):

```tsx
  // Resolution is a property of the network, not of the form below. `freq`
  // state seeds a NEW index; it must never be read back as status.
  const freqLabel = resolutionLabel(snap?.freq)
```

3. Update the Resolution card's `sub` (`:659-663`) so it stops asserting something unrelated:

```tsx
        <StatCard
          eyebrow="Resolution"
          value={freqLabel}
          sub={isMultiPeriod ? 'per investment period' : 'timestep spacing'}
        />
```

- [ ] **Step 12: Type-check**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```
Expected: no errors.

- [ ] **Step 13: Commit**

```bash
git branch --show-current
git commit pypsa-gui/backend/routers/network.py \
           pypsa-gui/backend/tests/test_model_horizon_endpoints.py \
           pypsa-gui/frontend/src/api/types.ts \
           pypsa-gui/frontend/src/pages/modelHorizonModel.ts \
           pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts \
           pypsa-gui/frontend/src/pages/ModelHorizon.tsx \
  -m "fix(gui): the Resolution card reads the network, not a form field

\`freq\` was useState('h'), written only by the single-period select —
which never renders in multi-period mode, and resets on reload. The card
sitting in the 'what the network is RIGHT NOW' strip therefore claimed
Hourly for a 3-hourly MultiIndex and for a Daily network after a refresh.

GET /snapshots now reports the inferred resolution, measured over the
first period's slice so period seams don't read as irregular, with a
modal-delta fallback so representative-week indexes still report hourly.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: One discount path (B4)

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`, `modelHorizonModel.test.ts`
- Modify: `pypsa-gui/frontend/src/pages/ModelHorizon.tsx:385-422` (delete), `:807-841` (delete), `:862-1044` (table column)

**Interfaces:**
- Consumes: `modelHorizonModel.ts` from Tasks 1-2.
- Produces: `pvFactor(args: { period: number; refPeriod: number; years: number; discountRate: number; inflationRate: number }): number`

**Background the implementer needs:** the page currently offers two ways to set `investment_period_weightings.objective`. The manual "Apply discounts" button writes `(1+r)^-(p-base)`. The "Auto-discount" checkbox makes `solver_service.py:4410-4413` write `(1+r_real)^-(p-ref) × years[p]` at solve time, where `r_real = (1+discount_rate)/(1+inflation_rate) - 1`, clamped at `-0.999`, and `ref` is the first active period. With `years=10` these differ 10×. The code comment at `:843` claims they are equivalent. The manual path is being deleted; the checkbox stays and gains a preview of what it will write.

- [ ] **Step 1: Write the failing test**

Append to `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts`:

```ts
import { pvFactor } from './modelHorizonModel'

describe('pvFactor', () => {
  // Mirrors solver_service.py::_apply_modelling_assumptions step 4 exactly.
  // Any divergence here is the bug this replaced: the deleted manual
  // calculator omitted the `years` term and the inflation correction, so it
  // disagreed with what actually reached the LP by a factor of `years`.
  const base = { refPeriod: 2030, discountRate: 0.07, inflationRate: 0 }

  it('is 1 x years at the reference period', () => {
    expect(pvFactor({ ...base, period: 2030, years: 1 })).toBeCloseTo(1, 10)
    expect(pvFactor({ ...base, period: 2030, years: 10 })).toBeCloseTo(10, 10)
  })

  it('discounts later periods and multiplies by the period span', () => {
    // (1.07)^-10 = 0.508349...; x 10 years = 5.08349...
    expect(pvFactor({ ...base, period: 2040, years: 10 })).toBeCloseTo(5.083493, 5)
  })

  it('applies the Fisher correction when inflation is set', () => {
    // real r = 1.07/1.02 - 1 = 0.0490196...; (1+r)^-10 = 0.619669...
    expect(pvFactor({ ...base, period: 2040, years: 1, inflationRate: 0.02 }))
      .toBeCloseTo(0.619669, 5)
  })

  it('clamps a pathological real rate at -0.999 so the base stays positive', () => {
    const v = pvFactor({ period: 2040, refPeriod: 2030, years: 1, discountRate: 0, inflationRate: 5000 })
    expect(Number.isFinite(v)).toBe(true)
    expect(v).toBeGreaterThan(0)
  })

  it('handles a zero discount rate as an identity on the PV term', () => {
    expect(pvFactor({ period: 2050, refPeriod: 2030, years: 5, discountRate: 0, inflationRate: 0 }))
      .toBeCloseTo(5, 10)
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: FAIL — `pvFactor is not exported by ./modelHorizonModel`.

- [ ] **Step 3: Implement `pvFactor`**

Append to `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`:

```ts
/**
 * The `investment_period_weightings.objective` value that auto-discount will
 * write for one period at solve time.
 *
 * This MIRRORS `solver_service.py::_apply_modelling_assumptions` step 4 and
 * must stay in step with it. It exists so the period table can show the user
 * what the checkbox will do before they solve, rather than making them run a
 * solve to find out.
 *
 * The real rate uses the exact Fisher relation, not nominal − inflation, and
 * is clamped at -0.999 so `(1 + r)` stays positive under a pathological
 * inflation > nominal.
 */
export function pvFactor(args: {
  period: number
  refPeriod: number
  years: number
  discountRate: number
  inflationRate: number
}): number {
  const { period, refPeriod, years, discountRate, inflationRate } = args
  const nominal = Number.isFinite(discountRate) ? discountRate : 0
  const infl = Number.isFinite(inflationRate) ? inflationRate : 0
  let r = 1 + infl > 0 ? (1 + nominal) / (1 + infl) - 1 : nominal
  if (r <= -0.999) r = -0.999
  const pv = Math.pow(1 + r, -(period - refPeriod))
  return pv * (Number.isFinite(years) ? years : 1)
}
```

- [ ] **Step 4: Run it to verify it passes**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: PASS, 18 tests.

- [ ] **Step 5: Delete the manual calculator's state and handler**

In `pypsa-gui/frontend/src/pages/ModelHorizon.tsx`, delete outright:

- the `discRate` / `discBase` state and both `…TouchedRef` refs (`:385-393`)
- both hydration `useEffect`s for them (`:394-403`)
- the whole `applyDiscountFactors` function (`:405-422`)

- [ ] **Step 6: Delete the manual calculator's UI**

Delete the entire "Discount-factor calculator" block (`:807-841`) — the `<div className="flex items-center gap-2 mt-1 pt-2 border-t border-border/60">` containing the two number inputs and the "Apply discounts" button.

Then correct the now-false claim in the auto-discount checkbox's comment (`:843-846`), replacing it with:

```tsx
                {/* Auto-discount: the ONLY automated path to ipw.objective.
                    Writes PV × years per period at LP build time using the
                    solver settings' discount_rate and inflation_rate, and
                    reverts in restore() so the on-disk network keeps whatever
                    the user typed. The PV column in the table below previews
                    exactly what it will write. Manual edits to the objective
                    cell still work and are what to use for one-off factors. */}
```

- [ ] **Step 7: Add the PV preview column**

Still in `ModelHorizon.tsx`, extend the Task-2 import to include `pvFactor`.

Derive the reference period next to the other memos, after `periodWeightings` (`:133-136`):

```tsx
  // Auto-discount anchors on the first ACTIVE period — same rule as
  // solver_service's `ref_year = periods_active[0]`.
  const refPeriod = useMemo(
    () => (periods.length > 0 ? Math.min(...periods) : 0),
    [periods],
  )
  const autoDiscountOn = Boolean(cfg?.auto_discount_periods)
```

In the period table's `<thead>` (`:866-878`), insert a PV column immediately after Objective:

```tsx
                        <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                            title="What Auto-discount will write into `objective` at solve time: (1+real rate)^-(period-first period) x years. Preview only — nothing is written until you solve.">
                          PV ×<br />preview
                        </th>
```

In the `<tbody>` row, immediately after the Objective `<td>` (which currently ends at `:947`):

```tsx
                            <td className={`px-2 py-1 text-right font-mono text-[11px] ${autoDiscountOn ? 'text-text' : 'text-muted/40'}`}
                                title={autoDiscountOn
                                  ? `Auto-discount will set objective = ${pvFactor({ period, refPeriod, years, discountRate: cfg?.discount_rate ?? 0, inflationRate: cfg?.inflation_rate ?? 0 }).toFixed(4)} at solve time, overriding the value on the left.`
                                  : 'Auto-discount is off — the objective value on the left is what the LP uses.'}>
                              {pvFactor({
                                period,
                                refPeriod,
                                years,
                                discountRate: cfg?.discount_rate ?? 0,
                                inflationRate: cfg?.inflation_rate ?? 0,
                              }).toFixed(4)}
                            </td>
```

Finally, update the explanatory paragraph under the table (`:1031-1044`) — replace the sentence describing `objective` with:

```tsx
                  <code> objective</code> = LP-objective weight. When
                  Auto-discount is ON the <code>PV × preview</code> column is
                  what actually reaches the LP; the value you type here is
                  overridden at solve time and restored afterwards. With
                  Auto-discount OFF, what you type is what the LP uses.
```

- [ ] **Step 8: Type-check and re-run**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```
Expected: no errors. Any "declared but never read" error for `discRate`/`discBase`/`applyDiscountFactors` means a deletion was missed — finish it.

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: PASS, 18 tests.

- [ ] **Step 9: Commit**

```bash
git branch --show-current
git commit pypsa-gui/frontend/src/pages/modelHorizonModel.ts \
           pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts \
           pypsa-gui/frontend/src/pages/ModelHorizon.tsx \
  -m "fix(gui): one discount path, and it shows its work

'Apply discounts' wrote (1+r)^-(p-base). Auto-discount writes
(1+real r)^-(p-ref) x years[p] at solve time. With years=10 they
differ 10x, and the code comment claimed they were equivalent.

Delete the manual calculator; auto-discount is the only automated path.
The period table gains a read-only PV column previewing exactly what the
checkbox will write, so the number is visible without solving. Hand
editing the objective cell still covers the one-off case.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Adding a year stops flattening per-period ranges (B5)

**Files:**
- Modify: `pypsa-gui/backend/routers/network.py:1678-1697`
- Modify: `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`

**Interfaces:**
- Consumes: `_build_period_multiindex` (existing, `:469`), `_infer_snapshot_freq` (Task 2).
- Produces: no new symbols; `POST /api/network/investment_periods` changes behaviour only.

**Background the implementer needs:** the snapshot constructor offers "Different year per period", which builds a MultiIndex where each period has its **own** operational DatetimeIndex — the workflow for multi-year weather data. `set_investment_periods` then rebuilds with `[base_idx] * len(new_periods)` where `base_idx` is the FIRST period's slice, so adding one year collapses every period onto period 0's range. The flat→multi promotion path is different and correct: there is only one range, and every period legitimately gets it.

- [ ] **Step 1: Write the failing test**

Append to `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`:

```python
def _period_blocks(n):
    """{period: [iso, ...]} for the current MultiIndex snapshots."""
    sns = n.snapshots
    out: dict[int, list[str]] = {}
    for p, ts in sns:
        out.setdefault(int(p), []).append(ts.isoformat())
    return out


def _multi_index_per_period(pairs):
    """pairs: [(period, DatetimeIndex), ...] — one distinct block per period."""
    import numpy as np
    period_level = np.concatenate([np.full(len(blk), p) for p, blk in pairs])
    timestep_level = pd.DatetimeIndex(np.concatenate([blk.values for _, blk in pairs]))
    mi = pd.MultiIndex.from_arrays(
        [period_level, timestep_level], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    return mi


def _distinct_range_network():
    """3 periods, each on a DIFFERENT operational year — the multi-year
    weather-data workflow the 'Different year per period' mode exists for."""
    pairs = [
        (2030, pd.date_range("2019-01-01", periods=4, freq="h")),
        (2040, pd.date_range("2020-01-01", periods=4, freq="h")),
        (2050, pd.date_range("2021-01-01", periods=4, freq="h")),
    ]
    n = pypsa.Network()
    n.set_snapshots(_multi_index_per_period(pairs))
    n.investment_periods = [2030, 2040, 2050]
    n.add("Bus", "B1")
    return n


def test_adding_a_period_preserves_each_existing_periods_own_range(client, install_network):
    live = install_network(_distinct_range_network())
    before = _period_blocks(live)
    assert before[2030][0].startswith("2019")
    assert before[2040][0].startswith("2020")
    assert before[2050][0].startswith("2021")

    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2040, 2050, 2060]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(PyPSAService.get_network())
    # Each pre-existing period keeps ITS OWN operational year. Before this fix
    # all three collapsed onto 2019 — period 0's range.
    assert after[2030] == before[2030]
    assert after[2040] == before[2040]
    assert after[2050] == before[2050]


def test_a_newly_added_period_inherits_the_first_periods_range_as_a_template(client, install_network):
    install_network(_distinct_range_network())
    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2040, 2050, 2060]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(PyPSAService.get_network())
    assert 2060 in after
    assert after[2060][0].startswith("2019")
    assert len(after[2060]) == 4


def test_removing_a_period_drops_only_its_block(client, install_network):
    live = install_network(_distinct_range_network())
    before = _period_blocks(live)

    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2050]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(PyPSAService.get_network())
    assert sorted(after) == [2030, 2050]
    assert after[2030] == before[2030]
    assert after[2050] == before[2050]


def test_flat_to_multi_promotion_still_gives_every_period_the_flat_range(client, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    install_network(n)

    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2040]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(PyPSAService.get_network())
    assert after[2030] == after[2040]
    assert after[2030][0].startswith("2024")
```

Add the import this needs to the file's import block:

```python
from services.pypsa_service import PyPSAService
```

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v -k "period"
```
Expected: `test_adding_a_period_preserves_each_existing_periods_own_range` FAILS — `after[2040]` starts with `2019`, not `2020`, because every period was rebuilt from period 0's range. `test_removing_a_period_drops_only_its_block` fails the same way. Capture the assertion diff.

- [ ] **Step 3: Preserve each period's own block**

In `pypsa-gui/backend/routers/network.py`, replace the block that determines the base index and rebuilds (`:1678-1697`) with:

```python
        # Determine each period's operational block. A period that exists both
        # before and after keeps ITS OWN timesteps — the previous code rebuilt
        # every period from the FIRST period's range, which silently destroyed
        # the "Different year per period" setup (2030→2019 weather, 2040→2020,
        # …) the moment the user added or removed a year.
        if is_multi:
            level0 = n.snapshots.get_level_values(0)
            existing_periods = sorted(level0.unique().tolist())
            existing_blocks = {
                int(p): pd.DatetimeIndex(
                    n.snapshots[level0 == p].get_level_values(1),
                )
                for p in existing_periods
            }
            base_idx = existing_blocks[int(existing_periods[0])]
        else:
            existing_periods = []
            existing_blocks = {}
            base_idx = pd.DatetimeIndex(n.snapshots)

        # Only rebuild snapshots if the period set actually changed.
        if existing_periods != new_periods:
            _backup_network_ts_to_user_ts(n)
            captured_weights = _capture_snapshot_weights_per_timestep(n)
            # Surviving periods keep their own block; a genuinely new period
            # inherits the first existing period's range as a template (on a
            # flat→multi promotion there is only one range, so every period
            # legitimately gets it).
            blocks = [existing_blocks.get(int(p), base_idx) for p in new_periods]
            mi = _build_period_multiindex(new_periods, blocks)
            n.set_snapshots(mi)
            _reapply_snapshot_weights(n, captured_weights)
            _reapply_user_ts_to_network(n)
```

Then update the changelog line just below (`:1707-1711`) so it no longer claims a single uniform step count:

```python
    change_log_service.log(
        "update", "Network", "investment_periods",
        f"Set investment periods: {new_periods} "
        f"({len(n.snapshots)} snapshots total; existing periods kept their own "
        f"operational range)",
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v
```
Expected: PASS, 10 tests.

- [ ] **Step 5: Run the whole backend suite**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests -q
```
Expected: no NEW failures versus the suite's state before this plan started. Record the summary line. If `test_model_horizon_endpoints.py` causes failures in unrelated files that pass when run alone, that is the shared-singleton cross-contamination the Global Constraints warn about — report it rather than working around it.

- [ ] **Step 6: Commit**

```bash
git branch --show-current
git commit pypsa-gui/backend/routers/network.py \
           pypsa-gui/backend/tests/test_model_horizon_endpoints.py \
  -m "fix(gui): adding an investment year stops flattening per-period ranges

set_investment_periods rebuilt with [base_idx] * len(new_periods), where
base_idx was the FIRST period's slice. So the snapshot constructor's
'Different year per period' mode — the one that exists for multi-year
weather data — was destroyed by clicking + on a year chip, with no
warning.

Surviving periods now keep their own block; only a genuinely new period
inherits the first period's range as a template. Flat→multi promotion is
unchanged: there is one range and every period legitimately gets it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Build Year offers the periods you configured (B6)

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/properties/cardKit.tsx:338-367`
- Create: `pypsa-gui/frontend/src/layout/properties/buildYearOptions.ts`
- Create: `pypsa-gui/frontend/src/layout/properties/buildYearOptions.test.ts`

**Interfaces:**
- Consumes: `networkApi.getInvestmentPeriods` (existing, `api/network.ts:152`), `nk` from `utils/queryKeys`.
- Produces: `buildYearOptions(periods: number[], currentValue: number | null | undefined, currentYear: number): number[]`

**Background the implementer needs:** `useBuildYearOptions` reads `cfg.investment_periods` from SolverConfig. **Nothing in the frontend ever writes that field** — Model Horizon writes `n.investment_periods` through `POST /network/investment_periods`, a different store. So the hook always takes its fallback branch and offers a generic 5-year grid. Since `build_year ≤ period` gates whether an asset is available to the LP, a user with periods 2026/2027/2028 gets offered 2025/2030/2035/… and can silently build an asset the model ignores. `cfg.investment_periods` is left in place — `solver_service.py:4329` uses it for an API-level promotion path that is a separate feature.

Extracting the pure selection logic first is what makes this testable without rendering a Properties card.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/layout/properties/buildYearOptions.test.ts`:

```ts
// The Build Year dropdown read cfg.investment_periods, a SolverConfig field no
// frontend code ever writes — Model Horizon writes n.investment_periods on the
// network. So the dropdown always fell to its generic 5-year grid, and a user
// with periods 2026/2027/2028 was offered 2025/2030/2035/... Since
// `build_year <= period` gates asset availability, picking one of those makes
// the asset invisible to the LP with no error.
import { describe, it, expect } from 'vitest'
import { buildYearOptions } from './buildYearOptions'

describe('buildYearOptions', () => {
  it('offers exactly the configured investment periods, sorted', () => {
    expect(buildYearOptions([2028, 2026, 2027], null, 2026))
      .toEqual([2026, 2027, 2028])
  })

  it('falls back to a 5-year grid when no periods are configured', () => {
    const opts = buildYearOptions([], null, 2026)
    expect(opts).toEqual([2025, 2030, 2035, 2040, 2045, 2050, 2055, 2060])
  })

  it('merges the asset current value in so a non-standard year is not lost', () => {
    expect(buildYearOptions([2030, 2040], 2033, 2026))
      .toEqual([2030, 2033, 2040])
  })

  it('does not duplicate a current value that is already an option', () => {
    expect(buildYearOptions([2030, 2040], 2040, 2026))
      .toEqual([2030, 2040])
  })

  it('ignores a zero / blank current value — PyPSA default, not a real year', () => {
    expect(buildYearOptions([2030, 2040], 0, 2026)).toEqual([2030, 2040])
    expect(buildYearOptions([2030, 2040], undefined, 2026)).toEqual([2030, 2040])
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/layout/properties/buildYearOptions.test.ts
```
Expected: FAIL — `Failed to resolve import "./buildYearOptions"`.

- [ ] **Step 3: Extract the pure selection**

Create `pypsa-gui/frontend/src/layout/properties/buildYearOptions.ts`:

```ts
/**
 * Options for the Build Year dropdown.
 *
 * Prefer the network's configured investment periods so a multi-period run only
 * lets users pick years the LP actually models — `build_year <= period` gates
 * whether an asset is available at all, so an off-grid year silently removes it
 * from the model. With no periods configured (single-period overnight runs),
 * fall back to a 35-year span in 5-year steps.
 *
 * The asset's current value is always merged in so an asset created with a
 * non-standard year doesn't silently lose it when the form opens.
 */
export function buildYearOptions(
  periods: number[],
  currentValue: number | null | undefined,
  currentYear: number,
): number[] {
  let opts: number[]
  if (periods.length > 0) {
    opts = periods.slice().sort((a, b) => a - b)
  } else {
    const start = Math.floor(currentYear / 5) * 5
    opts = Array.from({ length: 8 }, (_, i) => start + i * 5)
  }
  if (
    currentValue != null &&
    Number.isFinite(currentValue) &&
    currentValue > 0 &&
    !opts.includes(currentValue)
  ) {
    opts = [...opts, currentValue].sort((a, b) => a - b)
  }
  return opts
}
```

- [ ] **Step 4: Run it to verify it passes**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/layout/properties/buildYearOptions.test.ts
```
Expected: PASS, 5 tests.

- [ ] **Step 5: Point the hook at the network store**

In `pypsa-gui/frontend/src/layout/properties/cardKit.tsx`, replace `useBuildYearOptions` (`:338-367`) with:

```tsx
// Options for the build-year dropdown. Reads the NETWORK's investment periods
// (`/network/investment_periods`) — the store Model Horizon writes and the LP
// reads. This used to read `cfg.investment_periods` from SolverConfig, which no
// frontend code ever writes, so it always fell through to the generic grid and
// a user with periods 2026/2027/2028 was offered 2025/2030/2035/…
export function useBuildYearOptions(currentValue: number | null | undefined): number[] {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: ip } = useQuery({
    queryKey: nk(currentProject, 'investmentPeriods'),
    queryFn: networkApi.getInvestmentPeriods,
    staleTime: 60_000,
  })
  const periods = useMemo(
    () => (ip?.periods ?? []).map(Number).filter(Number.isFinite),
    [ip?.periods],
  )
  return useMemo(
    () => buildYearOptions(periods, currentValue, new Date().getFullYear()),
    [periods, currentValue],
  )
}
```

Add the imports this needs to the top of `cardKit.tsx` (check which are already present before adding):

```tsx
import { networkApi } from '../../api/network'
import { buildYearOptions } from './buildYearOptions'
```

- [ ] **Step 6: Type-check**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```
Expected: no errors. If `useMemo` or `nk` are not yet imported in `cardKit.tsx`, add them.

- [ ] **Step 7: Run the full frontend suite**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run
```
Expected: no NEW failures. `PropertiesPanel.rescale.test.tsx` renders cards and may now issue an extra query — if it fails, add `networkApi.getInvestmentPeriods` to that test's existing mock rather than reverting the hook.

- [ ] **Step 8: Commit**

```bash
git branch --show-current
git commit pypsa-gui/frontend/src/layout/properties/buildYearOptions.ts \
           pypsa-gui/frontend/src/layout/properties/buildYearOptions.test.ts \
           pypsa-gui/frontend/src/layout/properties/cardKit.tsx \
  -m "fix(gui): Build Year offers the investment periods you configured

useBuildYearOptions read cfg.investment_periods — a SolverConfig field
nothing in the frontend writes. Model Horizon writes n.investment_periods
on the network. So the dropdown always took its fallback branch: with
periods 2026/2027/2028 configured it offered 2025/2030/2035/...

build_year <= period gates asset availability, so picking one of those
made the asset invisible to the LP with no error. Read the network store.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Honest range label and the remaining cleanup (B7, B8, B9, B10)

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`, `modelHorizonModel.test.ts`
- Modify: `pypsa-gui/frontend/src/pages/ModelHorizon.tsx:211-221,289-295,628-634`
- Modify: `pypsa-gui/backend/routers/network.py:1604-1621`

**Interfaces:**
- Consumes: `modelHorizonModel.ts` from Tasks 1-3.
- Produces: `horizonRangeLabel(snapshots: string[] | undefined, periods: Array<number | string> | undefined, isMultiPeriod: boolean): string`

**Background the implementer needs:** `rangeStr` prints the first and last **timestep**, and multi-period networks replicate one operational year under every period — so a 2030/2040/2050 model reads "2024-01-01 → 2024-12-31", hiding the horizon entirely. This is the same confusion the `toDisplay` remap already fixed in `pages/results/asset/HorizonFilter.tsx`.

- [ ] **Step 1: Write the failing test**

Append to `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts`:

```ts
import { horizonRangeLabel } from './modelHorizonModel'

describe('horizonRangeLabel', () => {
  it('shows the plain date span on a flat network', () => {
    expect(horizonRangeLabel(
      ['2024-01-01T00:00:00', '2024-06-01T00:00:00', '2024-12-31T23:00:00'],
      undefined, false,
    )).toBe('2024-01-01 → 2024-12-31')
  })

  it('leads with the investment periods on a multi-period network', () => {
    // Snapshots replicate ONE operational year under every period, so printing
    // the raw first/last timestep hides the horizon: a 2030/2040/2050 model
    // read as "2024-01-01 → 2024-12-31".
    expect(horizonRangeLabel(
      ['2024-01-01T00:00:00', '2024-12-31T23:00:00'],
      [2030, 2050], true,
    )).toBe('2030…2050 × op. 01-01→12-31')
  })

  it('handles a single investment period without a range arrow', () => {
    expect(horizonRangeLabel(
      ['2024-01-01T00:00:00', '2024-01-02T00:00:00'],
      [2030], true,
    )).toBe('2030 × op. 01-01→01-02')
  })

  it('degrades to a mode word when there are no snapshots', () => {
    expect(horizonRangeLabel([], [], false)).toBe('flat horizon')
    expect(horizonRangeLabel(undefined, undefined, true)).toBe('multi-period horizon')
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: FAIL — `horizonRangeLabel is not exported by ./modelHorizonModel`.

- [ ] **Step 3: Implement it**

Append to `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`:

```ts
/**
 * The sub-label under the Snapshots stat card.
 *
 * Multi-period networks replicate ONE operational year under every investment
 * period, so the raw first/last timestep carries the BASE year and says nothing
 * about the horizon — a 2030/2040/2050 model read as "2024-01-01 → 2024-12-31".
 * Lead with the period span and reduce the operational window to MM-DD, which
 * is the part that actually varies. Same reasoning as the `toDisplay` remap in
 * `pages/results/asset/HorizonFilter.tsx`.
 */
export function horizonRangeLabel(
  snapshots: string[] | undefined,
  periods: Array<number | string> | undefined,
  isMultiPeriod: boolean,
): string {
  if (!snapshots || snapshots.length === 0) {
    return isMultiPeriod ? 'multi-period horizon' : 'flat horizon'
  }
  const first = snapshots[0]
  const last = snapshots[snapshots.length - 1]
  const nums = (periods ?? []).map(Number).filter(Number.isFinite)
  if (nums.length === 0) {
    return `${first.slice(0, 10)} → ${last.slice(0, 10)}`
  }
  const lo = Math.min(...nums)
  const hi = Math.max(...nums)
  const span = lo === hi ? `${lo}` : `${lo}…${hi}`
  // MM-DD only — the operational year is a base year, not a planning year.
  return `${span} × op. ${first.slice(5, 10)}→${last.slice(5, 10)}`
}
```

- [ ] **Step 4: Run it to verify it passes**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/modelHorizonModel.test.ts
```
Expected: PASS, 22 tests.

- [ ] **Step 5: Wire it, and clear the three small items**

In `pypsa-gui/frontend/src/pages/ModelHorizon.tsx`:

1. Extend the model import with `horizonRangeLabel`, and replace the whole `rangeStr` IIFE (`:628-634`) with:

```tsx
  const rangeStr = horizonRangeLabel(snap?.snapshots, snap?.periods, snapshotsAreMulti)
```

2. **B8** — delete the entire `updateLoadScaler` mutation (`:289-295`) and its preceding comment block (`:285-288`). It has no call sites; `updateLoadScalersByCarrier` replaced it.

3. **B9** — in `applyPeriods.onSuccess` (`:213-219`), add the solver-config invalidation so a removed period's budget and load scalers do not linger in the cache:

```tsx
    onSuccess: () => {
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'investmentPeriods') })
      qc.invalidateQueries({ queryKey: nk(proj, 'snapshots') })
      qc.invalidateQueries({ queryKey: nk(proj, 'meta') })
      // capex_budget_per_period and load_scalers_by_carrier are keyed by period
      // year; a removed period leaves its entries behind, and the table renders
      // from this cache.
      qc.invalidateQueries({ queryKey: nk(proj, 'solverConfig') })
      toast.success('Investment periods saved')
    },
```

- [ ] **Step 6: Surface the overwritten-weights warning (B10)**

In `pypsa-gui/backend/routers/network.py`, add the flag to `sample_representative_weeks`' response dict (`:1614-1621`):

```python
    return {
        "count": len(n.snapshots),
        "n_weeks": config.n_weeks,
        "seed": config.seed,
        "multi_period": is_multi,
        "timesteps_per_period": len(sampled_idx),
        "weeks": week_meta,
        # True when the network carried non-default snapshot_weightings before
        # sampling. Rep-week sampling necessarily replaces them (the prior
        # weights have no mapping onto the new sparse index), and until now that
        # was recorded only in the audit log — the user was never told.
        "had_custom_weights": _had_custom_weights,
    }
```

Then in `pypsa-gui/frontend/src/pages/ModelHorizon.tsx`, extend the `sampleWeeks` mutation's `onSuccess` (`:506-512`):

```tsx
    onSuccess: r => {
      invalidateSnapshotDependent()
      setSampledWeeks(r.weeks)
      toast.success(
        `Sampled ${r.weeks.length} week(s) → ${r.count} snapshots` +
        (r.multi_period ? ` (${r.timesteps_per_period}/period × periods)` : ''),
      )
      if (r.had_custom_weights) {
        toast(
          'Your previous snapshot weights were replaced by the representative-week scaling.',
          { icon: '⚠️', duration: 6000 },
        )
      }
    },
```

Add `had_custom_weights?: boolean` to the `sampleWeeks` response type in `pypsa-gui/frontend/src/api/network.ts` (find the `sampleWeeks` declaration and extend its generic).

- [ ] **Step 7: Type-check and run everything**

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```
Expected: no errors.

Run:
```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run
```
Expected: full suite green, no new failures.

Run (from repo root):
```bash
pixi run python -m pytest pypsa-gui/backend/tests -q
```
Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git branch --show-current
git commit pypsa-gui/frontend/src/pages/modelHorizonModel.ts \
           pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts \
           pypsa-gui/frontend/src/pages/ModelHorizon.tsx \
           pypsa-gui/frontend/src/api/network.ts \
           pypsa-gui/backend/routers/network.py \
  -m "fix(gui): honest horizon label, and the small Model Horizon cleanup

The Snapshots card printed the first and last timestep, and multi-period
networks replicate one operational year under every period — so a
2030/2040/2050 model read '2024-01-01 → 2024-12-31', hiding the horizon.
Lead with the period span, reduce the operational window to MM-DD.

Also: drop the dead updateLoadScaler mutation, invalidate solverConfig
when periods change so a removed period's budget and load scalers don't
linger, and tell the user when representative-week sampling replaced
their custom snapshot weights — that warning only reached the audit log.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification: the whole set

After Task 6, confirm the defects are actually gone rather than assumed gone.

- [ ] **Full suites, both sides, recorded output**

```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run
cd "$(git rev-parse --show-toplevel)" && pixi run python -m pytest pypsa-gui/backend/tests -q
```

- [ ] **Live check of B1** — the one defect whose failure mode is invisible in a unit test

Start the GUI (`bash pypsa-gui/start.sh`), build a 3-period MultiIndex with a short operational range, open Model Horizon, and edit the **first** row's Objective weight. Confirm the Period column shows the first period, the edited row keeps its new value after refetch, and no other row changed. Before this plan, the last period's row changed instead.

- [ ] **State the artifact status plainly**

These changes are source-only. The `.app` in `/Applications` and any DMG are stale. Say so in the final report — do not describe the fixes as reaching the packaged app without running `bash pypsa-gui/build-macos.sh`.

---

## Self-Review

**Spec coverage:** B1 → Task 1; B2 → Task 1; B3 → Task 2; B4 → Task 3; B5 → Task 4; B6 → Task 5; B7, B8, B9, B10 → Task 6; B11 → the two new test files created in Tasks 1, 2 and extended in 3, 4, 5, 6. No spec requirement is unassigned.

**Deviation from the spec, recorded deliberately:** the spec named the frontend test file `ModelHorizon.test.tsx`. The plan instead extracts pure functions into `modelHorizonModel.ts` and tests them in `modelHorizonModel.test.ts`, because every case in the spec's test matrix is a pure derivation and testing it through a rendered component would need a QueryClient, a Zustand store and four mocked endpoints to assert arithmetic. This also removes ~90 lines from a 1,489-line page, which serves the deferred decomposition. Same coverage, cheaper tests.

**Type consistency:** `snapshotWeightKey` / `buildWeightingRows` / `WeightingRow` / `WeightingTableRow` (Task 1) are used with those exact names in Tasks 1 and 6. `resolutionLabel` and `FREQ_OPTIONS` (Task 2) are consumed by the card and the `<select>`. `pvFactor`'s named-argument object (Task 3) matches its two call sites. `_infer_snapshot_freq` (Task 2) is imported by name in the backend test. `buildYearOptions` (Task 5) matches its test and its hook. The `updateOneWeight` argument field is renamed `iso` → `key` in Task 1 and used as `key` in the same task's table wiring.

**Placeholder scan:** no TBD/TODO, no "add error handling", no "similar to Task N" — every step carries the code it needs.
