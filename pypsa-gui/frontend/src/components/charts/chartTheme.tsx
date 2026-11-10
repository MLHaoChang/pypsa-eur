// The one chart theme every Results chart is built from (ui-modern issue 02,
// slice 1; owner decisions VISUALS-1, UI-MODERN-1, DENSITY-1).
//
// Small on purpose: later chart types (duration curve, 8760 heatmap, Sankey,
// tornado) and the KPI strip (issue 03) build on exactly these pieces.
//
//   carrierColour(carrier, i)   a carrier's colour, as a CSS token
//                               (`var(--color-carrier-*)`, light + dark)
//   CHART_HEIGHT.sm|md|lg       the three chart heights; no other heights
//   CHART_TICK                  axis ticks: mono, tabular figures
//   seriesDash(i)               a line style per series, so lines that share
//                               a hue still read apart (not hue alone)
//   directLabel(...)            the series name drawn on the series itself
//   thickestIndex(rows, key)    where a stacked band is widest (label spot)
//   LABEL_PASS                  props for the invisible pass that paints
//                               band labels above every band
//   bandLabelIndices(...)       which bands get a direct label, and where;
//                               none when the names could collide
//   applyReducedMotion()        no chart animation under reduced motion
//   <ChartSourceProvider>       set once by Results: which solve and window
//                               the charts read, and whether it is out of date
//   chartFigure('424.20 MW', 'worst hour')  the header's number, rounded
//                               like the KPI tile (3 s.f.): "424 MW worst hour"
//   <ChartFrame>                one header pattern: title, the number, hint,
//                               actions; then the chart; then the source line
//                               and an "Out of date" mark when it is stale
//   <ChartSourceLine>           that footer alone, for a card with its own header
//
// The axis unit caption stays `yAxisLabel` in pages/results/shared.tsx, and
// grid / legend / tooltip stay CHART_GRID / CHART_LEGEND / CHART_TOOLTIP
// there: those already were the shared standard (UX overhaul S4).
import { createContext, useContext, type CSSProperties, type ReactNode } from 'react'
import { Area, Bar, Line, Pie, Scatter, Tooltip } from 'recharts'
import { StatePill } from '../ResultState'
import { kpiFigure, splitValueUnit } from '../../utils/format'

// ── Carrier colours ──────────────────────────────────────────────────────────
// Carrier key -> token suffix. Exact match wins, then a substring match (so
// "offwind-ac" still reads as offshore wind, "solar-hsat" as solar). Order
// matters for the substring pass: the more specific key comes first.
const CARRIER_TOKEN: ReadonlyArray<readonly [string, string]> = [
  ['offwind', 'offwind'],
  ['onwind', 'wind'],
  ['wind', 'wind'],
  ['rooftop', 'rooftop'],
  ['solar', 'solar'],
  ['pv', 'solar'],
  ['ccgt', 'ccgt'],
  ['ocgt', 'gas'],
  ['gas', 'gas'],
  ['lignite', 'lignite'],
  ['coal', 'coal'],
  ['oil', 'oil'],
  ['nuclear', 'nuclear'],
  ['biomass', 'biomass'],
  ['geothermal', 'geothermal'],
  ['ror', 'ror'],
  ['hydro', 'hydro'],
  ['battery', 'storage'],
  ['storage', 'storage'],
  ['load', 'load'],
  ['electricity', 'electricity'],
  ['heat', 'heat'],
  ['hydrogen', 'h2'],
  ['h2', 'h2'],
  ['electrolys', 'h2'],
  ['grid', 'grid'],
  ['link_inflows', 'link-in'],
  ['link_outflows', 'link-out'],
]
const EXACT = new Map(CARRIER_TOKEN)
/** How many `--color-series-*` tokens exist for carriers with no token. */
export const SERIES_TOKENS = 8

/**
 * A carrier's chart colour as a CSS custom property, so it follows the light
 * and dark theme. Unknown carriers get `--color-series-<n>`, picked by `i`
 * (their position in the chart), so the same chart always colours alike.
 */
export function carrierColour(carrier: string | null | undefined, i = 0): string {
  const k = (carrier ?? '').toLowerCase()
  let token = EXACT.get(k)
  if (!token && k) token = CARRIER_TOKEN.find(([match]) => k.includes(match))?.[1]
  if (token) return `var(--color-carrier-${token})`
  return `var(--color-series-${(Math.abs(Math.trunc(i)) % SERIES_TOKENS) + 1})`
}

// ── Sizes and ticks ──────────────────────────────────────────────────────────
/** The three chart heights (px): a small companion chart, the default, a hero chart. */
export const CHART_HEIGHT = { sm: 140, md: 220, lg: 300 } as const
export type ChartSize = keyof typeof CHART_HEIGHT

/** Axis tick text: mono with tabular figures, so numbers line up. Size stays DENSITY-1's 10 px. */
export const CHART_TICK = {
  fontSize: 10,
  fill: 'var(--color-axis)',
  fontFamily: 'var(--font-family-mono)',
  style: { fontVariantNumeric: 'tabular-nums' } as CSSProperties,
}

// ── Series that differ by more than hue ─────────────────────────────────────
const DASHES = [undefined, '6 3', '2 3', '8 3 2 3'] as const
/** The line style for the i-th line series: solid, dashed, dotted, dash-dot, repeating. */
export function seriesDash(i: number): string | undefined {
  return DASHES[Math.abs(Math.trunc(i)) % DASHES.length]
}

/**
 * The row where `key` is largest: where a stacked band has room for its name.
 * The outer 8% of a long series is skipped, so a centred name is never cut
 * off at the plot's edge.
 */
export function thickestIndex(rows: ReadonlyArray<Record<string, unknown>>, key: string): number {
  let best = -1
  let bestV = 0
  const edge = rows.length >= 20 ? Math.ceil(rows.length * 0.08) : 0
  rows.forEach((r, i) => {
    if (i < edge || i >= rows.length - edge) return
    const v = Math.abs(Number(r[key]))
    if (Number.isFinite(v) && v > bestV) { best = i; bestV = v }
  })
  return best
}

/**
 * The series name drawn on the series at row `index` (Recharts `label` prop of
 * a `<Line>` or `<Area>`): just under an area band's top edge, or just above a
 * line point. Nothing is drawn when `index` is negative (an all-zero series).
 */
export function directLabel(name: string, index: number, where: 'band' | 'line' = 'band') {
  const DirectLabel = (p: { x?: number; y?: number; index?: number }) => {
    if (index < 0 || p.index !== index || p.x == null || p.y == null) return null
    return (
      <text
        x={p.x} y={where === 'band' ? p.y + 11 : p.y - 5}
        textAnchor="middle" fontSize={10} fontWeight={600}
        fill="var(--color-text)" stroke="var(--color-bg)" strokeWidth={3}
        paintOrder="stroke" data-direct-label={name}
      >
        {name}
      </text>
    )
  }
  return DirectLabel
}

/**
 * Which bands of a stack carry their name, and at which row. A band is named
 * where it is widest, only when that is at least `minShare` of `peak`; two
 * names keep `gap` (a share of the rows) apart, so they never overlap; and a
 * stack of more than `maxSeries` bands is left to the legend alone.
 */
export function bandLabelIndices(
  rows: ReadonlyArray<Record<string, unknown>>, keys: readonly string[], peak: number,
  { minShare = 1 / 8, maxSeries = 6, gap = 0.15 } = {},
): Map<string, number> {
  const out = new Map<string, number>()
  if (keys.length > maxSeries || !(peak > 0)) return out
  const minGap = Math.max(1, Math.ceil(rows.length * gap))
  const taken: number[] = []
  for (const k of keys) {
    const i = thickestIndex(rows, k)
    if (i < 0 || Math.abs(Number(rows[i][k])) < peak * minShare) continue
    if (taken.some(t => Math.abs(t - i) < minGap)) continue
    taken.push(i)
    out.set(k, i)
  }
  return out
}

/**
 * Props for a second, invisible pass of a stacked band that only carries its
 * direct label. Recharts paints each band's label with the band, so a later
 * band would cover it; repeat the labelled bands last, in the same order, on
 * their own `stackId` (same tops), with these props.
 */
export const LABEL_PASS = {
  fillOpacity: 0, strokeWidth: 0, legendType: 'none' as const, tooltipType: 'none' as const,
  isAnimationActive: false, activeDot: false,
}

// ── Reduced motion ───────────────────────────────────────────────────────────
/**
 * Turn off Recharts' series and tooltip animation when the viewer asks for
 * reduced motion. Called once at start-up (main.tsx); a series that sets
 * `isAnimationActive` itself keeps its own choice.
 */
export function applyReducedMotion(
  reduce = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
): void {
  if (!reduce) return
  for (const C of [Area, Bar, Line, Pie, Scatter, Tooltip] as unknown as Array<{ defaultProps?: Record<string, unknown> }>) {
    C.defaultProps = { ...C.defaultProps, isAnimationActive: false }
  }
}

// ── Source run and freshness ─────────────────────────────────────────────────
export interface ChartSource {
  /** What the charts read, in plain words: "Latest solve · solved · 2030-07-01 00:00 → 2030-07-07 23:00 · all periods". */
  line: string | null
  /** The network changed since that solve: every chart carries "Out of date" and dims. */
  stale: boolean
}
const SourceCtx = createContext<ChartSource>({ line: null, stale: false })
export const useChartSource = () => useContext(SourceCtx)
export function ChartSourceProvider({ value, children }: { value: ChartSource; children: ReactNode }) {
  return <SourceCtx.Provider value={value}>{children}</SourceCtx.Provider>
}

// ── The header's number ──────────────────────────────────────────────────────
/**
 * A chart's number for its header, rounded exactly as the KPI tile rounds it
 * (so a chart and its tile never disagree), plus what it is: "47.2 MW peak".
 * `formatted` is a value with its unit, as fmtPower / fmtEnergy / fmtCurrency give it.
 */
export function chartFigure(formatted: string, word?: string): string {
  const { value, unit } = splitValueUnit(formatted)
  const f = kpiFigure(value, unit)
  return [f.value, f.unit, word].filter(Boolean).join(' ')
}

// ── The frame ────────────────────────────────────────────────────────────────
/**
 * One header pattern for every run chart. `title` says what the chart shows
 * and is distinct per chart; `figure` is the number it answers with ("47.2 MW
 * peak"), set in mono. `basis` adds to the source line what only this chart
 * applies (for example "each hour weighted × 52.14"). The chart inside uses a
 * CHART_HEIGHT.
 */
export function ChartFrame({
  title, figure, hint, count, right, basis, children, className, bodyClassName, testId,
}: {
  title: ReactNode
  figure?: ReactNode
  hint?: ReactNode
  count?: number | string
  right?: ReactNode
  basis?: string
  children: ReactNode
  className?: string
  bodyClassName?: string
  testId?: string
}) {
  const { stale } = useChartSource()
  return (
    <section
      data-testid={testId}
      data-chart-frame=""
      className={`rounded-[10px] border border-border bg-bg overflow-hidden
        shadow-[0_1px_0_rgba(10,14,20,0.04)] ${className ?? ''}`}
    >
      <header className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-border bg-bg-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 min-w-0">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] [overflow-wrap:anywhere]">{title}</h3>
          {figure != null && figure !== '' && (
            <span className="font-mono text-[12px] font-semibold text-text tabular-nums shrink-0" data-chart-figure="">
              {figure}
            </span>
          )}
          {count != null && <span className="font-mono text-[11px] text-muted shrink-0">{count}</span>}
          {hint && <span className="text-[11px] text-muted [overflow-wrap:anywhere]">{hint}</span>}
        </div>
        {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
      </header>
      <div className={`${bodyClassName ?? 'p-3'} ${stale ? 'opacity-60' : ''}`}>{children}</div>
      <ChartSourceLine basis={basis} />
    </section>
  )
}

/**
 * The frame's footer on its own, for a chart card that keeps its own header
 * (a collapsible group): the source line, then "Out of date" when stale.
 */
export function ChartSourceLine({ basis, className }: { basis?: string; className?: string }) {
  const source = useChartSource()
  const line = [source.line, basis].filter(Boolean).join(' · ')
  if (!line && !source.stale) return null
  return (
    <footer className={`flex flex-wrap items-center gap-2 text-[10.5px] text-muted font-mono ${className ?? 'px-4 pb-2.5 -mt-1'}`}
      data-chart-source="">
      {source.stale && <StatePill state="stale" />}
      {line && <span className="[overflow-wrap:anywhere]">{line}</span>}
    </footer>
  )
}
