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
