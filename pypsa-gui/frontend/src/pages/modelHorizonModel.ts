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
