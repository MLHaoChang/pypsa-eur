import { useMemo, useRef } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import toast from 'react-hot-toast'
import { Download } from 'lucide-react'
import { CHART_AXIS, CHART_GRID, CHART_TOOLTIP, colourForCarrier, downloadSVG }
  from '../shared'
import { downloadPNG } from './exportPng'
import { fmtNum } from './format'
import type { AssetResultsResponse, ColumnSpec } from './types'

/**
 * Group the selected series by unit.
 *
 * One unit → one chart, which is the common case (p, available and
 * curtailment are all MW). Three units → three stacked charts sharing the
 * X axis. No dual axes: they invite false visual correlation, and they buy
 * nothing in the single-unit case.
 *
 * First-seen order is preserved so adding a series never reshuffles the
 * layout under the user's cursor.
 */
export function groupColumnsByUnit(columns: ColumnSpec[]):
  Array<{ unit: string; columns: ColumnSpec[] }> {
  const order: string[] = []
  const byUnit = new Map<string, ColumnSpec[]>()
  for (const c of columns) {
    const unit = c.unit || '–'
    if (!byUnit.has(unit)) { byUnit.set(unit, []); order.push(unit) }
    byUnit.get(unit)!.push(c)
  }
  return order.map(unit => ({ unit, columns: byUnit.get(unit)! }))
}

function UnitChart(
  { data, unit, columns, xKey, assetName }: {
    data: Array<Record<string, unknown>>; unit: string
    columns: ColumnSpec[]; xKey: string; assetName: string
  },
) {
  const ref = useRef<HTMLDivElement>(null)
  const base = `${assetName}_${unit.replace(/[^A-Za-z0-9]+/g, '_')}`
  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 px-1 mb-1">
        <span className="text-[10px] uppercase tracking-wider text-muted">{unit}</span>
        <span className="flex-1" />
        <button
          onClick={() => {
            downloadSVG(ref.current, `${base}.svg`)
              ? toast.success('Exported SVG')
              : toast.error('Chart not ready — try again once it renders')
          }}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
        ><Download size={11} /> SVG</button>
        <button
          onClick={async () => {
            const ok = await downloadPNG(ref.current, `${base}.png`)
            ok ? toast.success('Exported PNG')
               : toast.error('Chart not ready — try again once it renders')
          }}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
        ><Download size={11} /> PNG</button>
      </div>
      <div ref={ref} style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid {...CHART_GRID} />
            <XAxis dataKey={xKey} {...CHART_AXIS} minTickGap={40} />
            <YAxis {...CHART_AXIS} />
            {/* Two decimals here too, so hovering a point and reading the
                same point in the table cannot disagree. */}
            <Tooltip
              {...CHART_TOOLTIP}
              formatter={(value: unknown, name: unknown) =>
                [fmtNum(value), name as string] as [string, string]}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {columns.map((c, i) => (
              <Line key={c.id} type="monotone" dataKey={c.id} name={c.label}
                stroke={colourForCarrier(c.metric_id, i)} dot={false}
                strokeWidth={1.25} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

const ISO_YEAR = /^(\d{4})(-.*)$/

/**
 * The X value one row is plotted against.
 *
 * PyPSA replays ONE operational year under every investment period, so every
 * timestep of a multi-period network arrives stamped with the same base year
 * (2026, say) no matter which period it belongs to — the period lives only in
 * the parallel `periods` array. Plotting those raw ran the calendar
 * January→December once per period along a single axis: on a three-period
 * network the same date appeared three times, and the chart read as itself
 * pasted end-to-end three times.
 *
 * Rebasing each timestep onto ITS period's year turns the axis into a genuine
 * monotonic timeline (2027-01-01 → 2029-12-31): every date appears exactly
 * once, and there is still one line per series rather than one per
 * series × period. Results.tsx already applies this correction to the
 * horizon-filter inputs (`toDisplay` there); the chart was the last surface
 * still showing the raw replication year.
 *
 * An earlier attempt prefixed instead — `2027 · 06-15 00:00`. That made the X
 * values unique, so the lines stopped overdrawing, but it left the calendar
 * repeating along the axis (the reported symptom) and produced a
 * self-contradicting `2027 · 2026-01` on monthly buckets, where the prefix
 * says one year and the label says another.
 */
function plotStamp(
  stamp: string, period: number | string | null | undefined, monthly: boolean,
): string {
  const m = ISO_YEAR.exec(stamp)
  if (!m) return stamp                       // not a shape we recognise
  const year = period == null ? m[1] : String(period)
  // Monthly buckets are `YYYY-MM` and stop there.
  if (monthly) return `${year}${m[2]}`
  // `YYYY-MM-DDThh:mm:ss` → `YYYY-MM-DD hh:mm`. Snapshot seconds are always
  // :00 and cost a fifth of the tick's width.
  return `${year}${m[2].slice(0, 12).replace('T', ' ')}`
}

/**
 * The exact rows the chart plots, and the key they are plotted against.
 * Pure, so a test can assert the X values without rendering recharts — the
 * same reason `tableRows` is pure.
 */
export function chartRows(data: AssetResultsResponse):
  { xKey: string; rows: Array<Record<string, unknown>> } {
  const xKey = data.mode === 'duration' ? 'rank'
    : data.mode === 'monthly' ? 'month' : 'snapshot'
  // `duration` sorts every series independently and reports `periods: null` —
  // rank 1 is not a moment in time, so there is nothing to rebase.
  const timeIndexed = data.mode !== 'duration'
  const monthly = data.mode === 'monthly'
  const periods = data.periods

  const rows = data.index.map((stamp, i) => {
    const x = timeIndexed ? plotStamp(stamp, periods?.[i], monthly) : stamp
    const row: Record<string, unknown> = { [xKey]: x }
    for (const c of data.columns) row[c.id] = data.series[c.id]?.[i] ?? null
    return row
  })
  return { xKey, rows }
}

export default function AssetCharts(
  { data, onShowAll }: {
    data: AssetResultsResponse
    /** Restores every applicable metric — see the empty state below. */
    onShowAll?: () => void
  },
) {
  const groups = useMemo(() => groupColumnsByUnit(data.columns), [data.columns])
  const { xKey, rows } = useMemo(() => chartRows(data), [data])

  if (groups.length === 0) {
    return (
      <p className="p-4 text-[11px] text-muted">
        No time series selected.{' '}
        {onShowAll && (
          <button onClick={onShowAll} className="text-accent hover:underline">
            Show all
          </button>
        )}
      </p>
    )
  }
  return (
    <div className="overflow-y-auto">
      {groups.map(g => (
        <UnitChart key={g.unit} unit={g.unit} columns={g.columns}
          data={rows} xKey={xKey} assetName={data.asset.name} />
      ))}
    </div>
  )
}
