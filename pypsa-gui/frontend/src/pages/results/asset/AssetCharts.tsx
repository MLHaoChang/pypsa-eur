import { useMemo, useRef } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import toast from 'react-hot-toast'
import { Download } from 'lucide-react'
import { CHART_AXIS, CHART_GRID, CHART_TOOLTIP, colourForCarrier, downloadSVG,
  stampWithPeriod } from '../shared'
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

/**
 * The exact rows the chart plots, and the key they are plotted against.
 * Pure, so a test can assert the X values without rendering recharts — the
 * same reason `tableRows` is pure.
 *
 * PyPSA replays ONE operational year under every investment period, so on a
 * multi-period network `index` repeats: a one-day request returns 24 stamps
 * three times over, identical strings, distinguishable only by the parallel
 * `periods` array. Plotting against the bare stamp drew the same calendar day
 * once per period with nothing to tell them apart — the axis read as a chart
 * that had been pasted end-to-end three times. The table has consumed
 * `periods` since it was added; the chart never did.
 *
 * The prefix goes on only when more than one distinct period is present. With
 * a single period selected the axis is already unambiguous, and prefixing
 * every tick with the same year is noise.
 */
export function chartRows(data: AssetResultsResponse):
  { xKey: string; rows: Array<Record<string, unknown>> } {
  const xKey = data.mode === 'duration' ? 'rank'
    : data.mode === 'monthly' ? 'month' : 'snapshot'
  // `duration` sorts every series independently and reports `periods: null` —
  // rank 1 is not a moment in time, so there is nothing to qualify.
  const periods = data.periods
  const multi = !!periods && new Set(periods).size > 1

  const rows = data.index.map((stamp, i) => {
    const period = periods?.[i]
    // Monthly buckets are already `YYYY-MM`; `stampWithPeriod` would slice
    // that down to the bare month. Prefix those directly instead.
    const x = !multi || period == null ? stamp
      : data.mode === 'monthly' ? `${period} · ${stamp}`
      : stampWithPeriod(stamp, Number(period))
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
