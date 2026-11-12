// The hour-by-day heatmap as a chart type of the energy chart kit (ui-modern
// 02, slice 3; VISUALS-1): one column per calendar day, one row per hour of
// day, colour strength for the value. A full year is 365 × 24 cells ("8760").
// Maps shown together pass one scale (hourDay.ts), so a colour means the same
// value in each. Drawn as SVG, so the SVG export works.
import { memo, useId, useRef, useState, type MouseEvent } from 'react'
import { ResponsiveContainer } from 'recharts'
import toast from 'react-hot-toast'
import {
  downloadCSV, ChartActions, ChartFrame, CHART_HEIGHT, CHART_TICK, ChartZero,
} from './shared'
import { MAX_CELLS } from './reportCharts'
import { heatStrength, type HeatScale, type HourDayGrid } from './hourDay'

// Room for the hour ticks (left), the axis caption (top) and the date ticks (bottom).
const PAD = { left: 30, top: 18, right: 8, bottom: 20 }
const HOUR_TICKS = [0, 6, 12, 18, 23]
const pad2 = (n: number) => String(n).padStart(2, '0')

/** Which day columns carry a date tick: month starts on a long window, evenly spaced days on a short one. */
export function dayTicks(days: readonly string[], max = 8): number[] {
  if (days.length <= max) return days.map((_, i) => i)
  const months = days.map((d, i) => (i === 0 || d.slice(0, 7) !== days[i - 1].slice(0, 7) ? i : -1)).filter(i => i >= 0)
  if (months.length >= 3) {
    const step = Math.ceil(months.length / 12)
    return months.filter((_, k) => k % step === 0)
  }
  const step = Math.ceil(days.length / max)
  return days.map((_, i) => i).filter(i => i % step === 0)
}

export interface HourDayHeatmapProps {
  title: string
  /** The number the chart answers with, as `chartFigure` gives it. */
  figure?: string
  hint?: string
  grid: HourDayGrid
  /** The colour scale, from `heatScale`. Maps shown together pass the same one. */
  scale: HeatScale | null
  /** Said in the key when the scale is shared: "same scale as the other storage carriers". */
  scaleNote?: string
  /** A theme colour token, e.g. `carrierColour('electricity')`. */
  colour: string
  /** A value with its unit, for the key and the readout; the readout also passes the cell, for a second figure. */
  format: (v: number, cell?: { day: number; hour: number }) => string
  /** The value's CSV column, with the unit: `price_EUR_per_MWh`. */
  csv: string
  /** File name for the SVG and CSV exports, without the extension. */
  filename: string
  /** Why there is no map (for example an all-periods view), as one sentence. */
  unavailable?: string | null
  testId?: string
}

// Memoised: a year is 8760 cells, and pointing at one must not redraw them all.
const Cells = memo(function Cells({
  width = 0, height = 0, grid, scale, colour, hatchId, onHover, title, summary,
}: {
  width?: number; height?: number
  grid: HourDayGrid; scale: HeatScale; colour: string; hatchId: string; title: string; summary: string
  onHover: (cell: { day: number; hour: number } | null) => void
}) {
  const w = Math.max(0, width - PAD.left - PAD.right)
  const h = Math.max(0, height - PAD.top - PAD.bottom)
  const cw = w / grid.days.length
  const ch = h / 24
  const move = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const day = Math.floor((e.clientX - box.left - PAD.left) / cw)
    const hour = Math.floor((e.clientY - box.top - PAD.top) / ch)
    onHover(day >= 0 && day < grid.days.length && hour >= 0 && hour < 24 ? { day, hour } : null)
  }
  return (
    <svg width={width} height={height} role="img" onMouseMove={move} onMouseLeave={() => onHover(null)}
      aria-label={`${title}. ${summary}`}>
      <title>{title}</title>
      <desc>{summary}</desc>
      <defs>
        <pattern id={hatchId} width={4} height={4} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1={0} y1={0} x2={0} y2={4} stroke="var(--color-axis)" strokeWidth={1} />
        </pattern>
      </defs>
      <text x={2} y={PAD.top - 7} textAnchor="start" fontSize={10.5} fill="var(--color-axis)">Hour of day</text>
      <g transform={`translate(${PAD.left},${PAD.top})`} shapeRendering="crispEdges">
        {grid.cells.map((row, d) => row.map((v, hr) => (
          <rect key={`${d}-${hr}`} x={d * cw} y={hr * ch} width={cw} height={ch}
            data-cell={v == null ? 'none' : 'value'}
            fill={v == null ? `url(#${hatchId})` : colour}
            fillOpacity={v == null ? 1 : heatStrength(v, scale)} />
        )))}
      </g>
      {HOUR_TICKS.map(hr => (
        <text key={hr} {...CHART_TICK} x={PAD.left - 5} y={PAD.top + (hr + 0.5) * ch + 3.5} textAnchor="end" data-hour-tick="">
          {pad2(hr)}
        </text>
      ))}
      {dayTicks(grid.days).map(d => (
        <text key={d} {...CHART_TICK} x={PAD.left + (d + 0.5) * cw} y={height - 6}
          textAnchor={d === 0 ? 'start' : 'middle'} data-day-tick="">
          {d === 0 ? grid.days[d] : grid.days[d].slice(5)}
        </text>
      ))}
    </svg>
  )
})

/** The unit in a CSV column name: `price_EUR_per_MWh` → "EUR/MWh"; none when it carries none. */
function unitOf(csv: string): string | null {
  const m = /_(EUR_per_\w+|MW|MWh|pct)$/.exec(csv)
  return m ? m[1].replace('_per_', '/').replace('pct', '%') : null
}

export default function HourDayHeatmap({
  title, figure, hint, grid, scale, scaleNote, colour, format, csv, filename, unavailable, testId,
}: HourDayHeatmapProps) {
  const chartRef = useRef<HTMLDivElement | null>(null)
  const hatchId = `hd-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const [hover, setHover] = useState<{ day: number; hour: number } | null>(null)
  const values = grid.cells.flat()
  // No map: say why in the frame, never a blank plot.
  const reason = unavailable
    || (grid.days.length === 0 || !scale ? 'No hours in this window.'
      : values.every(v => v == null || v === 0) ? 'Every hour is zero in this window.'
      : null)
  if (reason || !scale) {
    return (
      <ChartFrame testId={testId} title={title} hint={hint} bodyClassName="p-3">
        <ChartZero>{reason}</ChartZero>
      </ChartFrame>
    )
  }
  const gaps = values.some(v => v == null)
  const at = hover ? grid.cells[hover.day][hover.hour] : null
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
            downloadCSV(`${filename}.csv`, ['date', 'hour', csv],
              grid.days.flatMap((day, d) => grid.cells[d].map((v, hr) => [day, hr, v ?? ''])))
            toast.success('Exported')
          }}
          filename={filename}
          report={(() => {
            const rows = grid.days.flatMap((day, d) => grid.cells[d].map((v, hr) => [day, hr, v]))
            const kept = Math.floor(MAX_CELLS / 3)
            return {
              view: 'hour-day-heatmap', figure: `${filename}_${grid.days[0]}_${grid.days[grid.days.length - 1]}`, unit: unitOf(csv),
              columns: ['date', 'hour', csv], rows, rowsTotal: rows.length,
              note: [`${format(scale.min)} → ${format(scale.max)}, stronger colour is higher`, scaleNote,
                rows.length > kept ? `table holds the first ${kept.toLocaleString('en')} of ${rows.length.toLocaleString('en')} hours; the picture shows all` : undefined,
              ].filter(Boolean).join(' · ') || undefined,
            }
          })()} />
      }
      bodyClassName="p-3"
    >
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={CHART_HEIGHT.md}>
          <Cells grid={grid} scale={scale} colour={colour} hatchId={hatchId} onHover={setHover} title={title}
            summary={`${grid.days.length} days by hour of day, ${grid.days[0]} to ${grid.days[grid.days.length - 1]}, from ${
              format(scale.min)} to ${format(scale.max)}. The CSV export holds every value.`} />
        </ResponsiveContainer>
      </div>
      {/* The key: what the colour strength and the hatch mean, in text. */}
      <div className="mt-1 flex min-h-[18px] flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-muted" data-heat-key="">
        <span className="flex items-center gap-1.5">
          <span className="font-mono tabular-nums">{format(scale.min)}</span>
          <span aria-hidden className="h-2 w-24 rounded-sm border border-border"
            style={{ background: `linear-gradient(to right, color-mix(in srgb, ${colour} 8%, transparent), ${colour})` }} />
          <span className="font-mono tabular-nums">{format(scale.max)}</span>
          <span>stronger colour is higher</span>
        </span>
        {scaleNote && <span>{scaleNote}</span>}
        {gaps && (
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="h-2 w-4 rounded-sm border border-border"
              style={{ background: 'repeating-linear-gradient(45deg, var(--color-axis) 0, var(--color-axis) 1px, transparent 1px, transparent 4px)' }} />
            no snapshot
          </span>
        )}
        <span className="ml-auto font-mono tabular-nums text-text" data-heat-readout="">
          {hover
            ? `${grid.days[hover.day]} ${pad2(hover.hour)}:00 · ${at == null ? 'no snapshot' : format(at, hover)}`
            : 'Hover a cell to read its value'}
        </span>
      </div>
    </ChartFrame>
  )
}
