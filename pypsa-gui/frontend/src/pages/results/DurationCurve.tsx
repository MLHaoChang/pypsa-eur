// The duration curve as a chart type of the energy chart kit (ui-modern 02,
// slice 2; VISUALS-1). Values sorted high to low over "% of hours": how often
// a load, a residual load or a price sits above a level. One frame, one
// height, theme colours, and series told apart by more than hue.
//
// Two-run comparison (CompareView) keeps its own chart: its curves arrive
// pre-sorted from the backend, clipped to a percentile band.
import { useId, useRef, type ReactNode } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, Legend, ReferenceLine,
} from 'recharts'
import toast from 'react-hot-toast'
import {
  durationCurvePoints, downloadCSV, ChartActions, ChartFrame, CHART_HEIGHT,
  CHART_GRID, CHART_AXIS, CHART_LEGEND, CHART_TOOLTIP, yAxisLabel, refLine,
  directLabel, seriesDash, isAllZero, ChartZero,
} from './shared'

export interface DurationSeries {
  /** The row field this series reads. */
  key: string
  /** Its name on the line, in the key and in the tooltip. */
  name: string
  /** A theme colour token, e.g. `carrierColour('load')`. */
  colour: string
  /** Its CSV column, with the unit: `load_MW`. */
  csv: string
}

export type DurationRow = { pct: number } & Record<string, number>

/**
 * Rows for a duration curve: each series sorted high to low ON ITS OWN (the
 * duration-curve convention), over x = % of hours, 0 to 100. Every series
 * covers the same hours, so they must have the same length.
 */
export function durationRows(series: Record<string, number[]>): DurationRow[] {
  const keys = Object.keys(series)
  const n = keys.length ? series[keys[0]].length : 0
  if (keys.some(k => series[k].length !== n)) {
    throw new Error('Duration curve: every series needs the same number of hours.')
  }
  const sorted = keys.map(k => durationCurvePoints(series[k]))
  return Array.from({ length: n }, (_, i) => {
    const row: DurationRow = { pct: sorted[0][i].pct }
    keys.forEach((k, s) => { row[k] = sorted[s][i].value })
    return row
  })
}

export interface DurationCurveProps {
  title: string
  /** The number the chart answers with, as `chartFigure` gives it: "47.2 MW peak". */
  figure?: string
  hint?: string
  /** A sentence above the plot, when the curve needs explaining. */
  note?: ReactNode
  rows: DurationRow[]
  /** One curve each. The first is solid; later ones are dashed. */
  series: DurationSeries[]
  /** The y-axis unit, drawn above the axis. */
  unit: string
  /** A value with its unit, for the tooltip. */
  format: (v: number) => string
  /** A y-axis tick; plain numbers when left out. */
  tickFormat?: (v: number) => string
  yWidth?: number
  /** The mean, drawn as a labelled reference line. */
  mean?: number | null
  /** File name for the SVG and CSV exports, without the extension. */
  filename: string
  testId?: string
}

export default function DurationCurve({
  title, figure, hint, note, rows, series, unit, format, tickFormat, yWidth, mean, filename, testId,
}: DurationCurveProps) {
  const chartRef = useRef<HTMLDivElement | null>(null)
  // Gradient ids are document-wide: two curves on one page must not share one.
  const gid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const several = series.length > 1
  // Nothing to sort, or nothing above zero: say so in the frame, never a blank plot.
  const reason = rows.length === 0 ? 'No hours in this window.'
    : isAllZero(rows, series.map(s => s.key)) ? 'Every hour is zero in this window.'
    : null
  if (reason) {
    return (
      <ChartFrame testId={testId} title={title} hint={hint} bodyClassName="p-3">
        <ChartZero>{reason}</ChartZero>
      </ChartFrame>
    )
  }
  return (
    <ChartFrame
      testId={testId}
      title={title}
      figure={figure}
      hint={hint}
      right={
        <ChartActions
          chartRef={chartRef}
          onExportCSV={() => {
            downloadCSV(`${filename}.csv`,
              ['pct_of_hours', ...series.map(s => s.csv)],
              rows.map(r => [r.pct, ...series.map(s => r[s.key])]))
            toast.success('Exported')
          }}
          filename={filename} />
      }
      bodyClassName="p-3"
    >
      {note && <p className="text-[11px] text-muted mb-2">{note}</p>}
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={CHART_HEIGHT.md}>
          <AreaChart data={rows} margin={{ top: 18, right: 8, left: 0, bottom: 12 }}>
            <defs>
              {series.map((s, i) => (
                <linearGradient key={s.key} id={`dc-${gid}-${i}`} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor={s.colour} stopOpacity={i === 0 ? 0.22 : 0.28} />
                  <stop offset="100%" stopColor={s.colour} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid {...CHART_GRID} />
            <XAxis dataKey="pct" type="number" domain={[0, 100]}
              tickFormatter={(v: number) => `${v.toFixed(0)} %`} {...CHART_AXIS}
              label={{ value: '% of hours', position: 'insideBottom', offset: -8,
                       style: { fontSize: 10.5, fill: 'var(--color-axis)' } }} />
            <YAxis {...CHART_AXIS} {...(yWidth != null ? { width: yWidth } : {})}
              tickFormatter={tickFormat} label={yAxisLabel(unit)} />
            <RTooltip {...CHART_TOOLTIP}
              labelFormatter={(v: number) => `${Number(v).toFixed(1)} % of hours`}
              formatter={(v: number, name: string) => [format(v), name]} />
            {several && <Legend {...CHART_LEGEND} />}
            {series.map((s, i) => (
              <Area key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.colour}
                strokeDasharray={seriesDash(i)}
                fill={`url(#dc-${gid}-${i})`} strokeWidth={i === 0 ? 1.4 : 1.6} isAnimationActive={false}
                // Names sit apart along the curve; a single series is named by the title.
                label={several
                  ? directLabel(s.name, Math.floor(rows.length * Math.min(0.1 + 0.5 * i, 0.9)), 'line')
                  : undefined} />
            ))}
            {mean != null && Number.isFinite(mean) && (
              <ReferenceLine {...refLine('mean', 'mean', mean, unit)} />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  )
}
