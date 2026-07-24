import { useState, useCallback, useRef, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import {
  Download, Upload, FileSpreadsheet, CheckCircle2, Circle, AlertCircle,
  BarChart3, Layers, Zap, Flame, Atom, Gauge, ArrowDownToLine, ChevronDown,
  Coins, Trash2,
} from 'lucide-react'
import { networkApi } from '../api/network'
import type {
  Load, Generator, Link,
  LoadProfileMeta, GeneratorProfileMeta, LinkProfileMeta,
  LoadSection, TimeseriesData,
} from '../api/types'
import { appLog } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { safeMax, safeMin } from '../utils/numeric'
import toast from 'react-hot-toast'
import { PageHeader } from '../components/PageKit'

// ── Config ─────────────────────────────────────────────────────────────────────

const ACCENT = '#2f81f7'

// Qualitative palette used when stacking many series in the aggregate view.
// Each column index gets a distinct, visually-separable colour.
const PALETTE = [
  '#2f81f7', '#f97316', '#10b981', '#ec4899', '#a855f7', '#eab308',
  '#06b6d4', '#ef4444', '#84cc16', '#6366f1', '#f43f5e', '#14b8a6',
  '#0ea5e9', '#d946ef', '#22c55e', '#fb923c', '#8b5cf6', '#facc15',
  '#0284c7', '#be123c', '#65a30d', '#7c3aed', '#dc2626', '#0d9488',
]
const colorFor = (i: number) => PALETTE[i % PALETTE.length]

type TabId = 'loads' | 'renewable' | 'conventional' | 'dr' | 'links'

interface TabConfig {
  id: TabId
  label: string
  subtitle: string
  // Display unit on the chart axes / summary table.
  unit: ProfileUnit
  // When true, the underlying values are pu but each series is multiplied by
  // its own p_nom before rendering — so the chart shows actual MW output
  // (capacity factor × installed capacity) rather than 0–1 availability.
  scaleByPnom?: boolean
  attribute: string
  component: 'loads' | 'generators' | 'links'
}

const TABS: TabConfig[] = [
  { id: 'loads',        label: 'Loads',          subtitle: 'p_set · MW',           unit: 'MW',                       attribute: 'p_set',    component: 'loads'      },
  { id: 'renewable',    label: 'Renewables',      subtitle: 'p_max_pu × p_nom · MW', unit: 'MW', scaleByPnom: true, attribute: 'p_max_pu', component: 'generators' },
  // Conventional / Demand Response / Links share the multi-attribute pattern
  // (one tab, sub-toggle for which attribute to upload). The `attribute` here
  // is the default; the actual attribute is overridden per-render by the
  // sub-toggle (CONV_ATTRS / DR_ATTRS / LINK_ATTRS).
  { id: 'conventional', label: 'Conventional',    subtitle: 'availability · must-run · marginal cost', unit: 'pu', attribute: 'p_max_pu', component: 'generators' },
  { id: 'dr',           label: 'Demand Response', subtitle: 'shedding availability · activation cost', unit: 'pu', attribute: 'p_max_pu', component: 'generators' },
  { id: 'links',        label: 'Links',           subtitle: 'availability · must-run · marginal cost', unit: 'pu', attribute: 'p_max_pu', component: 'links'      },
]

// Sub-attributes available on the Conventional tab.
type ConvAttr = 'p_max_pu' | 'p_min_pu' | 'marginal_cost'

interface ConvAttrConfig {
  id: ConvAttr
  label: string
  shortLabel: string
  Icon: typeof Gauge
  description: string
  /** Display unit. p_max_pu / p_min_pu are dimensionless 0-1; marginal_cost
   *  is €/MWh — that swap drives chart axis labels, summary table headers
   *  and the Σ-stat formatter. */
  unit: 'pu' | '€/MWh'
}

const CONV_ATTRS: ConvAttrConfig[] = [
  { id: 'p_max_pu',      label: 'Availability', shortLabel: 'p_max_pu',      Icon: Gauge, unit: 'pu',
    description: 'Time-varying upper bound on dispatch (e.g. nuclear refuelling, coal outages, hydro derating).' },
  { id: 'p_min_pu',      label: 'Must-run',     shortLabel: 'p_min_pu',      Icon: ArrowDownToLine, unit: 'pu',
    description: 'Time-varying minimum dispatch — forces the plant to produce at least p_min_pu × p_nom every hour.' },
  { id: 'marginal_cost', label: 'Marginal cost', shortLabel: 'marginal_cost', Icon: Coins, unit: '€/MWh',
    description: 'Hourly dispatch cost (€/MWh). Overrides the static `marginal_cost` per snapshot — fuel-price traces, market scenarios. Cleared via uploading a profile of all-NaN cells.' },
]

// Demand-Response sub-attributes. PyPSA-Eur models DR as a Generator that
// produces "negative load" — `p_max_pu` is the time-varying SHEDDING
// availability (when can demand be reduced?), `marginal_cost` is the
// €/MWh activation penalty. `p_min_pu` is included for users modelling
// forced-shed minimums but isn't the common case.
const DR_ATTRS: ConvAttrConfig[] = [
  { id: 'p_max_pu',      label: 'Shedding availability', shortLabel: 'p_max_pu', Icon: Gauge, unit: 'pu',
    description: 'Time-varying upper bound on the DR generator dispatch (0–1). 1.0 = full DR capacity can be shed at this snapshot; 0 = DR unavailable.' },
  { id: 'p_min_pu',      label: 'Forced-shed minimum', shortLabel: 'p_min_pu', Icon: ArrowDownToLine, unit: 'pu',
    description: 'Time-varying minimum DR activation — forces the LP to shed at least this much demand every hour. Rare; usually 0.' },
  { id: 'marginal_cost', label: 'Activation cost', shortLabel: 'marginal_cost', Icon: Coins, unit: '€/MWh',
    description: 'Per-MWh penalty the LP pays for activating DR. Drives the merit-order placement of DR relative to thermal plants.' },
]

// Link sub-attributes. p_max_pu / p_min_pu let the user pin the Link
// dispatch on a profile (see Compare-View modelling note). marginal_cost
// is the per-MWh cost of running the Link (useful for P2X conversion
// penalties beyond what bus0's marginal price already captures).
type LinkAttr = 'p_max_pu' | 'p_min_pu' | 'marginal_cost'
const LINK_ATTRS: ConvAttrConfig[] = [
  { id: 'p_max_pu',      label: 'Availability', shortLabel: 'p_max_pu', Icon: Gauge, unit: 'pu',
    description: 'Time-varying upper bound on |p0| ÷ p_nom_opt. Caps how much the Link can dispatch each hour.' },
  { id: 'p_min_pu',      label: 'Must-run', shortLabel: 'p_min_pu', Icon: ArrowDownToLine, unit: 'pu',
    description: 'Time-varying minimum dispatch (forces the Link to dispatch at least this much). Pair with the same p_max_pu profile to make the Link follow an exact must-run schedule (e.g. fixed datacenter load).' },
  { id: 'marginal_cost', label: 'Marginal cost', shortLabel: 'marginal_cost', Icon: Coins, unit: '€/MWh',
    description: 'Per-MWh dispatch cost for the Link itself (in addition to bus0 input cost). Drives the LP’s decision on when to use this Link vs alternatives.' },
]

interface LoadSectionConfig {
  id: LoadSection
  label: string
  Icon: typeof Zap
  shapeNote: string
}

const LOAD_SECTIONS: LoadSectionConfig[] = [
  { id: 'electricity', label: 'Electricity', Icon: Zap,
    shapeNote: 'Double-peak daily profile (morning + evening) scaled to each load’s p_set.' },
  { id: 'hydrogen',    label: 'Hydrogen',    Icon: Atom,
    shapeNote: 'Industrial baseline (~95 % weekday, ~60 % weekend) scaled to each load’s p_set.' },
  { id: 'heat',        label: 'Heat',        Icon: Flame,
    shapeNote: 'Morning + evening heat-demand peaks with a low overnight floor; minimal weekend reduction.' },
  { id: 'other',       label: 'Other',       Icon: Layers,
    shapeNote: 'Loads on buses with carriers we don’t recognise — uses the electricity shape by default.' },
]

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtMW(v: number) {
  return v >= 1000 ? `${(v / 1000).toFixed(2)} GW` : `${v.toFixed(1)} MW`
}
function fmtPu(v: number) {
  return `${(v * 100).toFixed(1)} %`
}
function fmtEur(v: number) {
  // €/MWh values commonly span 0-10k. Keep 2 decimals at the typical range,
  // collapse to integer above 1000 to avoid noise on extreme cells.
  if (Math.abs(v) >= 1000) return `${v.toFixed(0)} €/MWh`
  return `${v.toFixed(2)} €/MWh`
}
// Σ of a profile's values. For an MW profile (load p_set, renewables
// p_max_pu × p_nom) the sum over hourly snapshots is energy in MWh; for a
// raw pu profile (availability / must-run / DR / links) the sum is
// full-load-equivalent hours; for €/MWh the sum is a "cost-hours" integral
// — meaningful only as a relative magnitude between scenarios, so we
// format it as the mean €/MWh + count of hours instead of summing.
function fmtEnergy(v: number) {
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)} TWh`
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)} GWh`
  return `${v.toFixed(1)} MWh`
}
export type ProfileUnit = 'MW' | 'pu' | '€/MWh'
function fmtSum(v: number, unit: ProfileUnit) {
  if (unit === 'MW')   return fmtEnergy(v)
  if (unit === 'pu')   return `${v.toFixed(0)} h`
  // €/MWh — Σ over hourly snapshots is in €·h/MWh, an awkward unit.
  // Render the mean cost (Σ/n_hours) one decimal above the chart instead.
  // Caller passes v as the SUM; chart components also have access to n_rows
  // separately, but this helper keeps the signature symmetrical — display
  // the raw sum with a "€·h/MWh" tag so users can spot magnitude differences.
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)} M€·h/MWh`
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)} k€·h/MWh`
  return `${v.toFixed(1)} €·h/MWh`
}
// Pill label for the Σ stat.
const sumLabel = (unit: ProfileUnit) =>
  unit === 'MW' ? 'Σ Energy'
  : unit === 'pu' ? 'Σ FLH'
  : 'Σ Cost·h'
// Per-value formatter picker — used by chart tooltips + summary tables.
function fmtFor(unit: ProfileUnit): (v: number) => string {
  if (unit === 'MW')   return fmtMW
  if (unit === 'pu')   return fmtPu
  return fmtEur
}

const CATEGORY_LABEL: Record<string, string> = {
  electrolyzer: 'Electrolyzer',
  fuel_cell: 'Fuel Cell',
  other: 'Other',
}

// ── UploadZone ─────────────────────────────────────────────────────────────────

function UploadZone({ onFile, label }: { onFile: (f: File) => void; label?: string }) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) onFile(f)
  }, [onFile])

  return (
    <div
      onDrop={onDrop}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onClick={() => inputRef.current?.click()}
      className={`border-2 border-dashed rounded-lg p-6 flex flex-col items-center gap-2 cursor-pointer transition-colors
        ${dragging ? 'border-accent bg-accent/5' : 'border-border hover:border-accent/50 hover:bg-accent/3'}`}
    >
      <Upload size={24} className={dragging ? 'text-accent' : 'text-muted'} />
      <p className="text-xs font-semibold text-text">{label ?? 'Drop Excel or CSV here'}</p>
      <p className="text-[10px] text-muted text-center">Columns = names · Index = timestamps (ISO 8601)</p>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }}
      />
    </div>
  )
}

// ── Wheel-zoom hook (shared by ProfileChart + AggregateChart) ──────────────────
// Maintains a `{from, to}` slice over a series of length `total`. Scroll up =
// zoom in (smaller span) anchored at the cursor position so the point under
// the mouse stays put. Scroll down = zoom out, capped at the full series.
//
// We attach a native non-passive wheel listener via `addEventListener` rather
// than React's `onWheel` because React registers synthetic wheel handlers as
// passive — we need `preventDefault()` to stop the page from scrolling while
// the cursor is over the chart. `containerRef` is exposed for the caller to
// attach to the wrapping `<div>`.
//
// MIN_SPAN keeps the chart legible at maximum zoom (24 hourly points = one day
// at hourly resolution). For series shorter than that we cap to the full
// length so the hook still works on small samples.
function useTimeSeriesZoom(total: number): {
  containerRef: (node: HTMLDivElement | null) => void
  range: { from: number; to: number } | null
  reset: () => void
  isZoomed: boolean
} {
  const MIN_SPAN = 24
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
  // Callback-ref pattern (NOT useRef): the chart's wrapper <div> doesn't
  // mount until data has loaded (early `if (isLoading) return …` above the
  // chart body). A useRef + useEffect-with-[]-deps would bind once at mount
  // when the ref is still null and never re-bind. Using setState as the
  // "ref" forces the wheel-binding effect to re-run when the DOM element
  // actually becomes available.
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null)
  // Mirror state into refs so the wheel handler can stay stable across
  // re-renders — re-binding addEventListener on every range change causes
  // mid-scroll jitter on rapid wheel-tick sequences.
  const totalRef = useRef(total)
  const rangeRef = useRef(range)
  useEffect(() => { totalRef.current = total }, [total])
  useEffect(() => { rangeRef.current = range }, [range])

  useEffect(() => {
    if (!containerEl) return
    const el = containerEl
    const handler = (e: WheelEvent) => {
      const tot = totalRef.current
      if (tot <= 0) return
      e.preventDefault()

      const rect = el.getBoundingClientRect()
      const x = e.clientX - rect.left
      const frac = Math.max(0, Math.min(1, rect.width > 0 ? x / rect.width : 0.5))

      const current = rangeRef.current ?? { from: 0, to: tot - 1 }
      const curSpan = current.to - current.from + 1
      // 25 % zoom per wheel notch — small enough that several ticks are needed
      // for a dramatic change, big enough that one tick is visibly responsive.
      const factor = e.deltaY < 0 ? 1 / 1.25 : 1.25
      const minSpan = Math.min(MIN_SPAN, tot)
      const newSpan = Math.max(minSpan, Math.min(tot, Math.round(curSpan * factor)))

      // Anchor the new range so the time index under the cursor doesn't move.
      const pivot = current.from + frac * curSpan
      let newFrom = Math.round(pivot - frac * newSpan)
      let newTo = newFrom + newSpan - 1
      if (newFrom < 0) { newFrom = 0; newTo = newSpan - 1 }
      if (newTo > tot - 1) { newTo = tot - 1; newFrom = Math.max(0, newTo - newSpan + 1) }

      // Fully zoomed out → null so the "Reset zoom" button hides itself.
      if (newFrom === 0 && newTo === tot - 1) setRange(null)
      else setRange({ from: newFrom, to: newTo })
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [containerEl])

  const effRange = useMemo(() => {
    if (total <= 0) return null
    if (!range) return { from: 0, to: total - 1 }
    const f = Math.max(0, Math.min(range.from, total - 1))
    const t = Math.max(f, Math.min(range.to, total - 1))
    return { from: f, to: t }
  }, [range, total])

  const reset = useCallback(() => setRange(null), [])
  return { containerRef: setContainerEl, range: effRange, reset, isZoomed: range !== null }
}

// ── ProfileChart (single component) ────────────────────────────────────────────

function ProfileChart({ name, component, attribute, unit, scale }: {
  name: string
  component: 'loads' | 'generators' | 'links'
  attribute: string
  unit: ProfileUnit
  // When set, every value is multiplied by this factor before rendering.
  // Used by the Renewables tab to convert p_max_pu × p_nom → MW.
  scale?: number
}) {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: tsData, isLoading } = useQuery({
    queryKey: nk(currentProject, 'timeseries', component, attribute, name),
    queryFn: () => networkApi.getTimeseries(component, attribute, [name]),
  })

  // Hook order must be stable across renders — the wheel-zoom hook MUST
  // run before any early returns below. Initialise it from the (possibly
  // missing) tsData length; the hook handles total=0 cleanly by returning a
  // null range.
  const totalPoints = tsData?.index.length ?? 0
  const zoom = useTimeSeriesZoom(totalPoints)

  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-muted text-xs">Loading…</div>
  }

  if (!tsData || !tsData.columns.includes(name)) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
        <AlertCircle size={20} />
        <p className="text-xs">No profile uploaded for this {component === 'loads' ? 'load' : component === 'links' ? 'link' : 'generator'}</p>
      </div>
    )
  }

  const colIdx = tsData.columns.indexOf(name)
  const factor = scale ?? 1
  const chartData = tsData.index.map((ts, i) => {
    const raw = tsData.data[i]?.[colIdx]
    return {
      ts: ts.slice(0, 13).replace('T', ' '),
      value: (raw === null || raw === undefined) ? null : raw * factor,
    }
  })

  // Slice the rendered data to the zoom range; stats below stay on the full
  // series so the user sees absolute numbers regardless of zoom level.
  const visibleData = zoom.range
    ? chartData.slice(zoom.range.from, zoom.range.to + 1)
    : chartData

  const vals = chartData.map(d => d.value).filter((v): v is number => v != null)
  const peak = safeMax(vals)
  const avg  = vals.reduce((a, b) => a + b, 0) / (vals.length || 1)
  const min  = safeMin(vals)
  const sum  = vals.reduce((a, b) => a + b, 0)
  const fmt  = fmtFor(unit)

  return (
    <div className="flex flex-col h-full gap-2">
      {/* Stat pills */}
      <div className="flex gap-3 px-1 shrink-0">
        {([['Peak', peak], ['Avg', avg], ['Min', min]] as [string, number][]).map(([label, v]) => (
          <div key={label} className="flex flex-col items-center">
            <span className="text-[9px] text-muted uppercase tracking-wide">{label}</span>
            <span className="text-xs font-mono text-text">{fmt(v)}</span>
          </div>
        ))}
        {/* Σ of the profile — MWh for an MW profile, full-load hours for pu. */}
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-muted uppercase tracking-wide">{sumLabel(unit)}</span>
          <span className="text-xs font-mono text-text">{fmtSum(sum, unit)}</span>
        </div>
        <div className="ml-auto flex flex-col items-end">
          <span className="text-[9px] text-muted uppercase tracking-wide">Points</span>
          <span className="text-xs font-mono text-text">
            {zoom.isZoomed ? `${visibleData.length}/${vals.length}` : vals.length}
          </span>
        </div>
      </div>

      {/* Chart — scroll to zoom (cursor anchors the focus point). */}
      <div ref={zoom.containerRef} className="flex-1 min-h-0 relative">
        {zoom.isZoomed && (
          <button
            onClick={zoom.reset}
            className="absolute top-1 right-1 z-10 text-[10px] bg-bg/90 border border-border rounded px-2 py-0.5 hover:bg-border/30"
            title="Reset zoom to full series"
          >
            Reset zoom
          </button>
        )}
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={visibleData} margin={{ top: 4, right: 8, bottom: 20, left: 44 }}>
            <defs>
              <linearGradient id="tsGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={ACCENT} stopOpacity={0.25} />
                <stop offset="95%" stopColor={ACCENT} stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="ts" tick={{ fill: '#9ca3af', fontSize: 9 }} interval="preserveStartEnd" />
            <YAxis
              tick={{ fill: '#9ca3af', fontSize: 10 }}
              tickFormatter={
                unit === 'MW' ? v => v >= 1000 ? `${(v / 1000).toFixed(1)}G` : `${v}`
                : unit === '€/MWh' ? v => Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v.toFixed(0)}`
                : v => `${(v * 100).toFixed(0)}%`
              }
              width={48}
              domain={unit === 'pu' ? [0, 1] : undefined}
            />
            <Tooltip
              contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 11 }}
              formatter={(v: number) => [fmt(v), attribute]}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={ACCENT}
              strokeWidth={1.5}
              fill="url(#tsGrad)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── AggregateChart (stacked, one colour per name) ──────────────────────────────

function AggregateChart({ component, attribute, names, unit, label, scaleByName }: {
  component: 'loads' | 'generators' | 'links'
  attribute: string
  names: string[]
  unit: ProfileUnit
  label: string
  // Per-column scale factor. When provided, each column's values are
  // multiplied by `scaleByName[col]` before rendering. Used to render the
  // Renewables aggregate as p_max_pu × p_nom in MW. Missing entries default
  // to 1 so unscaled tabs work without passing an empty map.
  scaleByName?: Record<string, number>
}) {
  // Stable cache key: sorted comma-joined names. Same set → same query.
  const namesKey = names.slice().sort().join(',')
  const currentProject = useUIStore(s => s.currentProject)
  const { data: tsData, isLoading, isError, refetch } = useQuery<TimeseriesData>({
    queryKey: nk(currentProject, 'timeseries-multi', component, attribute, namesKey),
    queryFn: () => networkApi.getTimeseries(component, attribute, names),
    enabled: names.length > 0,
  })

  // Wheel-zoom hook — MUST run before the early-return ladder below so hook
  // order stays stable across loading / error / empty / data-loaded renders.
  const totalPoints = tsData?.index.length ?? 0
  const zoom = useTimeSeriesZoom(totalPoints)

  if (names.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
        <AlertCircle size={20} />
        <p className="text-xs">Select at least one item, or upload a profile, to aggregate</p>
      </div>
    )
  }
  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-muted text-xs">Loading…</div>
  }
  if (isError) {
    // Distinguishes a real backend failure (timeout, 5xx) from "no data" so
    // the user doesn't misread a transient network error as data loss.
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
        <AlertCircle size={20} className="text-danger" />
        <p className="text-xs text-danger">Failed to load profiles — backend may be busy</p>
        <button onClick={() => refetch()} className="text-[11px] text-accent hover:underline">Retry</button>
      </div>
    )
  }
  if (!tsData || tsData.columns.length === 0 || tsData.index.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
        <AlertCircle size={20} />
        <p className="text-xs">No profiles uploaded for {label.toLowerCase()} yet</p>
      </div>
    )
  }

  const cols = tsData.columns
  const fmt  = fmtFor(unit)

  // One row per timestamp keyed by column name. Recharts expects data in this
  // form for multi-series charts.
  const chartData = tsData.index.map((ts, i) => {
    const row: Record<string, string | number | null> = { ts: ts.slice(0, 13).replace('T', ' ') }
    cols.forEach((c, j) => {
      const v = tsData.data[i]?.[j]
      const factor = scaleByName?.[c] ?? 1
      row[c] = (v === null || v === undefined) ? null : v * factor
    })
    return row
  })

  // Apply the zoom range (hook itself was called above the early returns).
  const visibleData = zoom.range
    ? chartData.slice(zoom.range.from, zoom.range.to + 1)
    : chartData

  // Peak / mean computed on the row-wise SUM across all stacked columns —
  // i.e. the height of the combined stack at each timestamp.
  const sumPerRow = chartData.map(row =>
    cols.reduce((acc, c) => acc + (typeof row[c] === 'number' ? (row[c] as number) : 0), 0),
  )
  const peak = safeMax(sumPerRow)
  const mean = sumPerRow.length ? sumPerRow.reduce((a, b) => a + b, 0) / sumPerRow.length : 0
  // Total energy under the combined stack — Σ of the per-timestamp stack
  // heights. MWh for an MW stack, full-load hours for a pu stack.
  const sumTotal = sumPerRow.reduce((a, b) => a + b, 0)

  return (
    <div className="flex flex-col h-full gap-2">
      <div className="flex gap-3 px-1 shrink-0">
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-muted uppercase tracking-wide">Series</span>
          <span className="text-xs font-mono text-text">{cols.length}/{names.length}</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-muted uppercase tracking-wide">Σ peak</span>
          <span className="text-xs font-mono text-text">{fmt(peak)}</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-muted uppercase tracking-wide">Σ avg</span>
          <span className="text-xs font-mono text-text">{fmt(mean)}</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-muted uppercase tracking-wide">{sumLabel(unit)}</span>
          <span className="text-xs font-mono text-text">{fmtSum(sumTotal, unit)}</span>
        </div>
        <div className="ml-auto flex flex-col items-end">
          <span className="text-[9px] text-muted uppercase tracking-wide">Points</span>
          <span className="text-xs font-mono text-text">
            {zoom.isZoomed ? `${visibleData.length}/${tsData.index.length}` : tsData.index.length}
          </span>
        </div>
      </div>

      <div ref={zoom.containerRef} className="flex-1 min-h-0 relative">
        {zoom.isZoomed && (
          <button
            onClick={zoom.reset}
            className="absolute top-1 right-1 z-10 text-[10px] bg-bg/90 border border-border rounded px-2 py-0.5 hover:bg-border/30"
            title="Reset zoom to full series"
          >
            Reset zoom
          </button>
        )}
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={visibleData} margin={{ top: 4, right: 8, bottom: 20, left: 44 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="ts" tick={{ fill: '#9ca3af', fontSize: 9 }} interval="preserveStartEnd" />
            <YAxis
              tick={{ fill: '#9ca3af', fontSize: 10 }}
              tickFormatter={
                unit === 'MW' ? v => v >= 1000 ? `${(v / 1000).toFixed(1)}G` : `${v}`
                : unit === '€/MWh' ? v => Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v.toFixed(0)}`
                : v => `${(v * 100).toFixed(0)}%`
              }
              width={48}
            />
            <Tooltip
              contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 11 }}
              formatter={(v: number, n: string) => [fmt(v), n]}
              itemSorter={item => -((item.value as number) ?? 0)}
            />
            {cols.map((c, i) => (
              <Area
                key={c}
                type="monotone"
                dataKey={c}
                name={c}
                stackId="agg"
                stroke={colorFor(i)}
                strokeWidth={0.6}
                fill={colorFor(i)}
                fillOpacity={0.75}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Compact legend below the chart — colour swatch + name. Wraps so that
          large series counts stay readable without bloating the chart area. */}
      <div className="shrink-0 flex flex-wrap gap-x-3 gap-y-1 px-1 pt-1 max-h-20 overflow-y-auto">
        {cols.map((c, i) => (
          <div key={c} className="flex items-center gap-1.5 min-w-0">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
              style={{ background: colorFor(i) }}
            />
            <span className="text-[10px] text-text truncate" title={c}>{c}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── List items ─────────────────────────────────────────────────────────────────

function LoadItem({
  load, meta, selected, checked, onClick, onCheck, onDownload, onUpload, onDelete,
}: {
  load: Load
  meta: LoadProfileMeta | undefined
  selected: boolean
  checked: boolean
  onClick: () => void
  onCheck: () => void
  onDownload: () => void
  onUpload: (f: File) => void
  // Optional — only rendered when this component has an uploaded profile.
  // Clicking drops the (loads, p_set, <name>) entry from _user_ts via the
  // generic timeseries delete endpoint.
  onDelete?: () => void
}) {
  const has = meta?.has_profile === true
  const fileRef = useRef<HTMLInputElement>(null)

  return (
    <div
      className={`group flex items-center gap-2 px-3 py-2 transition-colors border-b border-border/40
        ${selected ? 'bg-accent/10' : 'hover:bg-accent/5'}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onCheck}
        onClick={e => e.stopPropagation()}
        className="accent-accent shrink-0 cursor-pointer"
        title="Include in aggregate"
      />
      <button onClick={onClick} className="flex-1 min-w-0 flex items-center gap-2 text-left">
        {has ? <CheckCircle2 size={13} className="shrink-0 text-success" /> : <Circle size={13} className="shrink-0 text-muted" />}
        <div className="flex-1 min-w-0">
          <p className={`text-xs font-medium truncate ${selected ? 'text-accent' : 'text-text'}`}>{load.name}</p>
          <p className="text-[10px] text-muted truncate">{load.bus}</p>
        </div>
        {has && meta.has_profile && <span className="text-[9px] text-success font-mono shrink-0">{meta.rows}h</span>}
      </button>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); onDownload() }}
        className="opacity-40 group-hover:opacity-100 hover:text-accent transition-all p-0.5 shrink-0"
        title={`Download single-load template for '${load.name}'`}
      >
        <Download size={12} />
      </button>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); fileRef.current?.click() }}
        className="opacity-40 group-hover:opacity-100 hover:text-accent transition-all p-0.5 shrink-0"
        title={`Upload profile for '${load.name}' (single-column file)`}
      >
        <Upload size={12} />
      </button>
      {has && onDelete && (
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="opacity-40 group-hover:opacity-100 hover:text-danger transition-all p-0.5 shrink-0"
          title={`Delete uploaded profile for '${load.name}'`}
        >
          <Trash2 size={12} />
        </button>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) onUpload(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}

function GenItem({ gen, meta, hasActive, activeRows, selected, checked, onClick, onCheck, onDownload, onUpload, onDelete, attrLabel }: {
  gen: Generator
  meta: GeneratorProfileMeta | undefined
  // Whether the generator has a profile for the CURRENTLY-ACTIVE sub-attribute
  // (p_max_pu / p_min_pu / marginal_cost). Drives the row's check icon, the
  // rows-badge, and whether the trash button is shown. Defaults to the
  // top-level p_max_pu has_profile when not provided (back-compat with the
  // Renewables tab, which has only one attribute).
  hasActive?: boolean
  activeRows?: number | null
  selected: boolean
  checked: boolean
  onClick: () => void
  onCheck: () => void
  onDownload: () => void
  onUpload: (f: File) => void
  // Drops the (generators, <attrLabel>, <gen.name>) profile from _user_ts.
  // Rendered only when the generator has an uploaded profile for the
  // currently-active attribute.
  onDelete?: () => void
  // Short label of the active attribute (e.g. "p_max_pu", "p_min_pu") — shows
  // in the per-row tooltip so the user knows which profile slot the upload
  // will land in.
  attrLabel: string
}) {
  const has = hasActive !== undefined ? hasActive : meta?.has_profile === true
  const rowsShown = activeRows !== undefined ? activeRows : (meta?.has_profile ? meta.rows : null)
  const fileRef = useRef<HTMLInputElement>(null)
  return (
    <div
      className={`group flex items-center gap-2 px-3 py-2 transition-colors border-b border-border/40
        ${selected ? 'bg-accent/10' : 'hover:bg-accent/5'}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onCheck}
        onClick={e => e.stopPropagation()}
        className="accent-accent shrink-0 cursor-pointer"
        title="Include in aggregate"
      />
      <button onClick={onClick} className="flex-1 min-w-0 flex items-center gap-2 text-left">
        {has ? <CheckCircle2 size={13} className="shrink-0 text-success" /> : <Circle size={13} className="shrink-0 text-muted" />}
        <div className="flex-1 min-w-0">
          <p className={`text-xs font-medium truncate ${selected ? 'text-accent' : 'text-text'}`}>{gen.name}</p>
          <p className="text-[10px] text-muted truncate">{gen.bus} · {gen.carrier}</p>
        </div>
        {has && rowsShown != null && <span className="text-[9px] text-success font-mono shrink-0">{rowsShown}h</span>}
      </button>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); onDownload() }}
        className="opacity-40 group-hover:opacity-100 hover:text-accent transition-all p-0.5 shrink-0"
        title={`Download single-generator ${attrLabel} template for '${gen.name}'`}
      >
        <Download size={12} />
      </button>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); fileRef.current?.click() }}
        className="opacity-40 group-hover:opacity-100 hover:text-accent transition-all p-0.5 shrink-0"
        title={`Upload ${attrLabel} profile for '${gen.name}' (single-column file)`}
      >
        <Upload size={12} />
      </button>
      {has && onDelete && (
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="opacity-40 group-hover:opacity-100 hover:text-danger transition-all p-0.5 shrink-0"
          title={`Delete ${attrLabel} profile for '${gen.name}'`}
        >
          <Trash2 size={12} />
        </button>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) onUpload(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}

function LinkItem({ link, meta, hasActive, activeRows, selected, checked, onClick, onCheck, onDownload, onUpload, onDelete, attrLabel }: {
  link: Link
  meta: LinkProfileMeta | undefined
  // Whether the link has a profile for the CURRENTLY-active attribute (the
  // sub-toggle). Independent of meta.has_profile, which always describes
  // p_max_pu — a link can have p_min_pu but no p_max_pu and we want the
  // checkmark + delete button to react to the active slot.
  hasActive: boolean
  activeRows: number | undefined
  selected: boolean
  checked: boolean
  onClick: () => void
  onCheck: () => void
  onDownload: () => void
  onUpload: (f: File) => void
  // Drops the (links, <attrLabel>, <link.name>) profile from _user_ts.
  // Rendered only when hasActive is true.
  onDelete?: () => void
  // Active attribute short label (e.g. 'p_max_pu', 'p_min_pu',
  // 'marginal_cost') — surfaces in the upload / delete tooltips so the
  // user knows which slot the gesture targets.
  attrLabel: string
}) {
  const has = hasActive
  const catLabel = meta ? CATEGORY_LABEL[meta.category] ?? meta.category : ''
  const fileRef = useRef<HTMLInputElement>(null)
  return (
    <div
      className={`group flex items-center gap-2 px-3 py-2 transition-colors border-b border-border/40
        ${selected ? 'bg-accent/10' : 'hover:bg-accent/5'}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onCheck}
        onClick={e => e.stopPropagation()}
        className="accent-accent shrink-0 cursor-pointer"
        title="Include in aggregate"
      />
      <button onClick={onClick} className="flex-1 min-w-0 flex items-center gap-2 text-left">
        {has ? <CheckCircle2 size={13} className="shrink-0 text-success" /> : <Circle size={13} className="shrink-0 text-muted" />}
        <div className="flex-1 min-w-0">
          <p className={`text-xs font-medium truncate ${selected ? 'text-accent' : 'text-text'}`}>{link.name}</p>
          <p className="text-[10px] text-muted truncate">{link.bus0} → {link.bus1}</p>
        </div>
        <span className="text-[9px] text-muted font-mono shrink-0">
          {has && activeRows != null ? `${activeRows}h` : catLabel}
        </span>
      </button>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); onDownload() }}
        className="opacity-40 group-hover:opacity-100 hover:text-accent transition-all p-0.5 shrink-0"
        title={`Download single-link template for '${link.name}'`}
      >
        <Download size={12} />
      </button>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); fileRef.current?.click() }}
        className="opacity-40 group-hover:opacity-100 hover:text-accent transition-all p-0.5 shrink-0"
        title={`Upload ${attrLabel} profile for '${link.name}' (single-column file)`}
      >
        <Upload size={12} />
      </button>
      {has && onDelete && (
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="opacity-40 group-hover:opacity-100 hover:text-danger transition-all p-0.5 shrink-0"
          title={`Delete ${attrLabel} profile for '${link.name}'`}
        >
          <Trash2 size={12} />
        </button>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) onUpload(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────────

export default function TimeSeriesManager() {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const [activeTab, setActiveTab] = useState<TabId>('loads')
  const [selectedName, setSelectedName] = useState<string | null>(null)

  // Section sub-tab (only used on Loads).
  const [loadSection, setLoadSection] = useState<LoadSection>('electricity')
  // Conventional / DR / Links sub-attribute. p_max_pu = availability ceiling,
  // p_min_pu = must-run floor, marginal_cost = €/MWh activation. Same upload
  // pattern; only the active attribute and per-attribute meta lookup change.
  const [convAttr, setConvAttr] = useState<ConvAttr>('p_max_pu')
  // DR uses the same shape but its own state so a user can have, e.g.,
  // 'Activation cost' selected on DR while leaving Conventional on
  // 'Availability'. Same for Links.
  const [drAttr, setDrAttr] = useState<ConvAttr>('p_max_pu')
  const [linkAttr, setLinkAttr] = useState<LinkAttr>('p_max_pu')
  // View mode applies to ALL tabs. Aggregate stacks each selected (or visible)
  // series with its own colour.
  const [viewMode, setViewMode] = useState<'individual' | 'aggregate'>('individual')
  // Multi-select set; reset on tab change so cross-tab state doesn't leak.
  const [selectedNames, setSelectedNames] = useState<Set<string>>(new Set())

  const tabCfg = TABS.find(t => t.id === activeTab)!
  // Effective attribute for the active tab. Conventional / DR / Links each
  // swap their attribute via a sub-toggle; the other tabs use tabCfg as-is.
  const effectiveAttr =
    activeTab === 'conventional' ? convAttr
    : activeTab === 'dr'         ? drAttr
    : activeTab === 'links'      ? linkAttr
    : tabCfg.attribute
  const convAttrCfg = CONV_ATTRS.find(a => a.id === convAttr)!
  // Config block for the currently-active sub-toggle. Drives the chart's
  // unit suffix + "Must-run/Availability/…" copy in the upload pane. Returns
  // CONV_ATTRS' config for non-conventional tabs that don't have a sub-
  // toggle, but it isn't consumed for those (effectiveAttr === tabCfg.attribute).
  const activeAttrCfg: ConvAttrConfig =
    activeTab === 'dr'    ? (DR_ATTRS.find(a => a.id === drAttr) ?? DR_ATTRS[0])
    : activeTab === 'links' ? (LINK_ATTRS.find(a => a.id === linkAttr) ?? LINK_ATTRS[0])
    : convAttrCfg

  const { data: loads = [] }      = useQuery({ queryKey: nk(currentProject, 'loads'),      queryFn: networkApi.getLoads })
  const { data: generators = [] } = useQuery({ queryKey: nk(currentProject, 'generators'), queryFn: networkApi.getGenerators })
  const { data: links = [] }      = useQuery({ queryKey: nk(currentProject, 'links'),      queryFn: networkApi.getLinks })
  const { data: loadProfiles = {} as Record<string, LoadProfileMeta> } = useQuery({
    queryKey: nk(currentProject, 'load_profiles'),
    queryFn: networkApi.getLoadProfiles,
  })
  const { data: genProfiles = {} as Record<string, GeneratorProfileMeta> } = useQuery({
    queryKey: nk(currentProject, 'generator_profiles'),
    queryFn: networkApi.getGeneratorProfiles,
  })
  const { data: linkProfiles = {} as Record<string, LinkProfileMeta> } = useQuery({
    queryKey: nk(currentProject, 'link_profiles'),
    queryFn: networkApi.getLinkProfiles,
  })

  const handleTabChange = (tab: TabId) => {
    setActiveTab(tab)
    setSelectedName(null)
    // Selection set holds names from the previous tab — drop it so checkboxes
    // start empty in the new tab.
    setSelectedNames(new Set())
  }

  const loadsList  = loads      as Load[]
  const gensList   = generators as Generator[]
  const linksList  = links      as Link[]
  const lProfiles  = loadProfiles as Record<string, LoadProfileMeta>
  const gProfiles  = genProfiles  as Record<string, GeneratorProfileMeta>
  const liProfiles = linkProfiles as Record<string, LinkProfileMeta>

  // Map of generator name → installed capacity (p_nom). Used by the
  // Renewables tab to render p_max_pu time-series in MW (cap × factor)
  // rather than as a 0–1 fraction.
  const pNomByGenerator = useMemo(() => {
    const m: Record<string, number> = {}
    gensList.forEach(g => { m[g.name] = g.p_nom })
    return m
  }, [gensList])

  // Per-name scale factor for the active tab. Empty / undefined for tabs that
  // already display in their native unit (loads in MW, conventional/links in pu).
  const scaleByName: Record<string, number> | undefined = tabCfg.scaleByPnom
    ? pNomByGenerator
    : undefined
  // For the single-component ProfileChart we only need the one selected name's
  // scale factor (defaults to 1 when not scaling).
  const scaleForSelected = tabCfg.scaleByPnom && selectedName
    ? (pNomByGenerator[selectedName] ?? 1)
    : undefined

  // Section-filtered loads (only relevant on the Loads tab).
  const sectionLoads = useMemo(() => {
    if (activeTab !== 'loads') return loadsList
    return loadsList.filter(l => (lProfiles[l.name]?.section ?? 'electricity') === loadSection)
  }, [activeTab, loadsList, lProfiles, loadSection])

  // Counts displayed in the section sub-tab badges.
  const sectionCounts = useMemo<Record<LoadSection, number>>(() => {
    const counts: Record<LoadSection, number> = { electricity: 0, hydrogen: 0, heat: 0, other: 0 }
    for (const l of loadsList) {
      const sec: LoadSection = lProfiles[l.name]?.section ?? 'electricity'
      counts[sec] = (counts[sec] ?? 0) + 1
    }
    return counts
  }, [loadsList, lProfiles])

  // Conventional tab catches both 'conventional' AND 'other' categories.
  // 'other' is the carrier-classifier's catch-all bucket for custom carriers
  // that don't match the renewable / conventional / DR keyword sets (e.g.
  // 'heat-dump', 'wind-spill', anything user-coined). Those gens are
  // functionally dispatchable units the user wants to tune from the
  // Conventional tab — without this fallback they'd be invisible in the
  // Time Series Manager. Backend ALSO maps a handful of dump/spill/slack
  // keywords directly to 'conventional' for known patterns (see
  // _CONVENTIONAL_KW in network.py); 'other' is the safety net.
  const filteredGens = gensList.filter(g => {
    const cat = gProfiles[g.name]?.category
    if (activeTab === 'conventional') return cat === 'conventional' || cat === 'other'
    return cat === activeTab
  })
  const loadsWithProfile  = sectionLoads.filter(l => lProfiles[l.name]?.has_profile)
  const loadsWithout      = sectionLoads.filter(l => !lProfiles[l.name]?.has_profile)
  // For the Conventional / DR / Links tabs the icon/list reflects the
  // currently-active sub-attribute — the same component can have a
  // p_max_pu profile but no p_min_pu (or vice versa). Other tabs always
  // look at the top-level p_max_pu meta.
  const genHasProfileForActive = (name: string) => {
    const m = gProfiles[name]
    if (!m) return false
    if (activeTab === 'conventional') {
      if (convAttr === 'p_min_pu')      return m.p_min_pu?.has_profile === true
      if (convAttr === 'marginal_cost') return m.marginal_cost?.has_profile === true
    }
    if (activeTab === 'dr') {
      if (drAttr === 'p_min_pu')        return m.p_min_pu?.has_profile === true
      if (drAttr === 'marginal_cost')   return m.marginal_cost?.has_profile === true
    }
    return m.has_profile === true
  }
  // Row count for the active sub-attribute (matches the partition done by
  // genHasProfileForActive). Returns null when no profile is present.
  // Used to drive GenItem's rows badge — without this the badge always
  // showed p_max_pu rows even when the sub-toggle was on p_min_pu or
  // marginal_cost.
  const genActiveRows = (name: string): number | null => {
    const m = gProfiles[name]
    if (!m) return null
    if (activeTab === 'conventional' || activeTab === 'dr') {
      const sub = activeTab === 'conventional' ? convAttr : drAttr
      if (sub === 'p_min_pu')      return m.p_min_pu?.has_profile ? m.p_min_pu.rows : null
      if (sub === 'marginal_cost') return m.marginal_cost?.has_profile ? m.marginal_cost.rows : null
    }
    return m.has_profile ? m.rows : null
  }
  // Selector used by the summary table and chart header; returns the meta
  // block for the currently-active attribute (so peak/avg numbers match).
  const genActiveAttrMeta = (name: string) => {
    const m = gProfiles[name]
    if (!m) return undefined
    const subAttr = activeTab === 'conventional' ? convAttr
                  : activeTab === 'dr'           ? drAttr
                  : undefined
    if (subAttr === 'p_min_pu') {
      return m.p_min_pu?.has_profile ? m.p_min_pu : undefined
    }
    if (subAttr === 'marginal_cost') {
      return m.marginal_cost?.has_profile ? m.marginal_cost : undefined
    }
    return m.has_profile ? m : undefined
  }
  // Same trick for Links — the sub-toggle (p_max_pu / p_min_pu /
  // marginal_cost) drives which has_profile slot we inspect.
  const linkHasProfileForActive = (name: string) => {
    const m = liProfiles[name]
    if (!m) return false
    if (linkAttr === 'p_min_pu')      return m.p_min_pu?.has_profile === true
    if (linkAttr === 'marginal_cost') return m.marginal_cost?.has_profile === true
    return m.has_profile === true
  }
  // Row count for the active attribute (used by LinkItem's count badge).
  // Returns undefined when no profile is present.
  const linkActiveRows = (name: string): number | undefined => {
    const m = liProfiles[name]
    if (!m) return undefined
    if (linkAttr === 'p_min_pu')      return m.p_min_pu?.has_profile ? m.p_min_pu.rows : undefined
    if (linkAttr === 'marginal_cost') return m.marginal_cost?.has_profile ? m.marginal_cost.rows : undefined
    return m.has_profile ? m.rows : undefined
  }
  // Active-attribute meta block for the summary table. Top-level for
  // p_max_pu (back-compat); nested block for p_min_pu / marginal_cost.
  // Returns a minimal shape (has_profile + rows + mean + peak + sum) the
  // summary renderer reads — the unused top-level fields like category
  // aren't consumed there.
  const linkActiveAttrMeta = (name: string) => {
    const m = liProfiles[name]
    if (!m) return undefined
    if (linkAttr === 'p_min_pu')      return m.p_min_pu?.has_profile ? m.p_min_pu : undefined
    if (linkAttr === 'marginal_cost') return m.marginal_cost?.has_profile ? m.marginal_cost : undefined
    return m.has_profile ? m : undefined
  }
  const gensWithProfile   = filteredGens.filter(g => genHasProfileForActive(g.name))
  const gensWithout       = filteredGens.filter(g => !genHasProfileForActive(g.name))
  const linksWithProfile  = linksList.filter(l => linkHasProfileForActive(l.name))
  const linksWithout      = linksList.filter(l => !linkHasProfileForActive(l.name))

  // Section change resets the selection set so checkboxes don't carry over from
  // (e.g.) electricity loads into the hydrogen view.
  useEffect(() => { setSelectedNames(new Set()) }, [loadSection])

  const totalCount = activeTab === 'loads' ? sectionLoads.length
    : activeTab === 'links' ? linksList.length
    : filteredGens.length
  const withProfileCount = activeTab === 'loads' ? loadsWithProfile.length
    : activeTab === 'links' ? linksWithProfile.length
    : gensWithProfile.length

  // Items currently visible in the left list (after tab+section filtering).
  // Drives both "select all" and the default aggregate set. For generators we
  // use the attribute-aware lookup so the conventional must-run sub-view
  // doesn't claim profiles that are only defined for p_max_pu.
  const visibleItems = useMemo(() => {
    if (activeTab === 'loads') return sectionLoads.map(l => ({ name: l.name, hasProfile: lProfiles[l.name]?.has_profile === true }))
    if (activeTab === 'links') return linksList.map(l => ({ name: l.name, hasProfile: liProfiles[l.name]?.has_profile === true }))
    return filteredGens.map(g => ({ name: g.name, hasProfile: genHasProfileForActive(g.name) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, convAttr, sectionLoads, linksList, filteredGens, lProfiles, liProfiles, gProfiles])

  // ── Multi-select helpers ───────────────────────────────────────────────────

  const toggleCheck = (name: string) => {
    setSelectedNames(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }
  const selectAllVisible = () => {
    setSelectedNames(prev => {
      const next = new Set(prev)
      visibleItems.forEach(i => next.add(i.name))
      return next
    })
  }
  const clearSelection = () => setSelectedNames(new Set())

  // Names to feed into the aggregate. If the user explicitly checked some
  // visible items, use those. Otherwise default to every visible item with a
  // profile (so the chart isn't empty by default).
  const aggregateNames = useMemo(() => {
    const visibleSelected = visibleItems.filter(i => selectedNames.has(i.name)).map(i => i.name)
    if (visibleSelected.length > 0) return visibleSelected
    return visibleItems.filter(i => i.hasProfile).map(i => i.name)
  }, [visibleItems, selectedNames])

  // ── Upload mutations ───────────────────────────────────────────────────────

  const invalidateLoadCaches = () => {
    const proj = useUIStore.getState().currentProject
    qc.invalidateQueries({ queryKey: nk(proj, 'load_profiles') })
    qc.invalidateQueries({ queryKey: nk(proj, 'timeseries', 'loads', 'p_set') })
    qc.invalidateQueries({ queryKey: nk(proj, 'timeseries-multi', 'loads') })
    qc.invalidateQueries({ queryKey: nk(proj, 'load_aggregate') })
  }

  // Set by per-row upload handlers so the global onSuccess can verify the
  // file actually contained the expected column. One ref per component type
  // since the three upload mutations are distinct.
  const pendingSingleLoadRef = useRef<string | null>(null)
  const pendingSingleGenRef = useRef<string | null>(null)
  const pendingSingleLinkRef = useRef<string | null>(null)

  const uploadLoadMut = useMutation({
    mutationFn: (file: File) => networkApi.uploadLoadProfile(file),
    onSuccess: result => {
      invalidateLoadCaches()
      // A full-year upload onto a short network grows n.snapshots backend-side
      // (_ensure_snapshots_cover_user_ts). Invalidate ['snapshots'] so the
      // status-bar counter, SnapshotPicker and Model Horizon reflect the new
      // range — without this they keep showing the stale pre-upload count and
      // the user (wrongly) thinks the solve will run on the old horizon.
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'snapshots') })
      const target = pendingSingleLoadRef.current
      pendingSingleLoadRef.current = null
      if (target && !result.matched.includes(target)) {
        // Single-load upload that didn't actually contain the expected column.
        toast.error(`File did not contain a column named '${target}'.`)
        appLog('WARN', `Per-load upload skipped: file missing column '${target}'`)
        return
      }
      const n = result.matched.length, u = result.unmatched.length
      const msg = `Load profile uploaded: ${result.rows} rows for ${n} load${n !== 1 ? 's' : ''}${u ? ` (${u} unmatched ignored)` : ''} — model horizon: ${result.snapshot_count} snapshots`
      toast.success(msg); appLog('INFO', msg)
      if (u > 0) appLog('WARN', `Unmatched columns: ${result.unmatched.join(', ')}`)
      if (result.matched.length > 0 && !selectedName) setSelectedName(result.matched[0])
    },
    onError: (e: Error) => { pendingSingleLoadRef.current = null; toast.error(e.message); appLog('ERROR', `Load profile upload failed: ${e.message}`) },
  })

  const uploadGenMut = useMutation({
    // Mutation takes both the file and the attribute being uploaded so the
    // same hook serves p_max_pu (renewable / dr / conventional-availability)
    // and p_min_pu (conventional-must-run).
    mutationFn: ({ file, attribute }: { file: File; attribute: string }) =>
      networkApi.uploadGeneratorProfile(file, attribute),
    onSuccess: (result, variables) => {
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'generator_profiles') })
      // Prefix-invalidate so both p_max_pu and p_min_pu cached fetches refresh
      // (avoids a stale chart after switching attributes post-upload).
      qc.invalidateQueries({ queryKey: nk(proj, 'timeseries', 'generators') })
      qc.invalidateQueries({ queryKey: nk(proj, 'timeseries-multi', 'generators') })
      // See uploadLoadMut — a longer profile expands n.snapshots backend-side;
      // refresh the snapshot counter so the new horizon is visible everywhere.
      qc.invalidateQueries({ queryKey: nk(proj, 'snapshots') })
      const target = pendingSingleGenRef.current
      pendingSingleGenRef.current = null
      if (target && !result.matched.includes(target)) {
        toast.error(`File did not contain a column named '${target}'.`)
        appLog('WARN', `Per-generator upload skipped: file missing column '${target}'`)
        return
      }
      const n = result.matched.length, u = result.unmatched.length
      const attrLabel = variables.attribute
      const msg = `Generator ${attrLabel} uploaded: ${result.rows} rows for ${n} generator${n !== 1 ? 's' : ''}${u ? ` (${u} unmatched ignored)` : ''} — model horizon: ${result.snapshot_count} snapshots`
      toast.success(msg); appLog('INFO', msg)
      if (u > 0) appLog('WARN', `Unmatched columns: ${result.unmatched.join(', ')}`)
      if (result.matched.length > 0 && !selectedName) setSelectedName(result.matched[0])
    },
    onError: (e: Error) => { pendingSingleGenRef.current = null; toast.error(e.message); appLog('ERROR', `Generator profile upload failed: ${e.message}`) },
  })

  const uploadLinkMut = useMutation({
    // Like uploadGenMut, takes the attribute so the same hook serves
    // p_max_pu (availability), p_min_pu (must-run), and marginal_cost.
    mutationFn: ({ file, attribute }: { file: File; attribute: string }) =>
      networkApi.uploadLinkProfile(file, attribute),
    onSuccess: (result, variables) => {
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'link_profiles') })
      // Prefix-invalidate so every per-attribute cached fetch refreshes
      // (avoids a stale chart after switching attributes post-upload).
      qc.invalidateQueries({ queryKey: nk(proj, 'timeseries', 'links') })
      qc.invalidateQueries({ queryKey: nk(proj, 'timeseries-multi', 'links') })
      // See uploadLoadMut — a longer profile expands n.snapshots backend-side;
      // refresh the snapshot counter so the new horizon is visible everywhere.
      qc.invalidateQueries({ queryKey: nk(proj, 'snapshots') })
      const target = pendingSingleLinkRef.current
      pendingSingleLinkRef.current = null
      if (target && !result.matched.includes(target)) {
        toast.error(`File did not contain a column named '${target}'.`)
        appLog('WARN', `Per-link upload skipped: file missing column '${target}'`)
        return
      }
      const n = result.matched.length, u = result.unmatched.length
      const attrLabel = variables.attribute
      const msg = `Link ${attrLabel} uploaded: ${result.rows} rows for ${n} link${n !== 1 ? 's' : ''}${u ? ` (${u} unmatched ignored)` : ''} — model horizon: ${result.snapshot_count} snapshots`
      toast.success(msg); appLog('INFO', msg)
      if (u > 0) appLog('WARN', `Unmatched columns: ${result.unmatched.join(', ')}`)
      if (result.matched.length > 0 && !selectedName) setSelectedName(result.matched[0])
    },
    onError: (e: Error) => { pendingSingleLinkRef.current = null; toast.error(e.message); appLog('ERROR', `Link profile upload failed: ${e.message}`) },
  })

  // Generic delete mutation. Takes the (component, attribute, name) tuple
  // so the same hook serves every tab — Loads delete p_set, Generators
  // delete the active sub-attribute, Links delete the active sub-attribute.
  // On success, invalidate the same query keys the upload mutation does
  // so the profile-meta + per-attribute timeseries fetches refresh
  // immediately and the row's "has-profile" indicator flips to empty.
  const deleteTsMut = useMutation({
    mutationFn: (vars: { component: string; attribute: string; name?: string }) =>
      networkApi.deleteTimeseries(vars),
    onSuccess: (result, vars) => {
      const fam = vars.component  // 'loads' / 'generators' / 'links'
      const proj = useUIStore.getState().currentProject
      if (fam === 'loads') qc.invalidateQueries({ queryKey: nk(proj, 'load_profiles') })
      else if (fam === 'generators') qc.invalidateQueries({ queryKey: nk(proj, 'generator_profiles') })
      else if (fam === 'links') qc.invalidateQueries({ queryKey: nk(proj, 'link_profiles') })
      qc.invalidateQueries({ queryKey: nk(proj, 'timeseries', fam) })
      qc.invalidateQueries({ queryKey: nk(proj, 'timeseries-multi', fam) })
      qc.invalidateQueries({ queryKey: nk(proj, 'snapshots') })
      // Deselect the dropped name(s) so the chart pane doesn't try to
      // render a profile that no longer exists.
      const dropped = new Set(result.deleted)
      if (selectedName && dropped.has(selectedName)) setSelectedName(null)
      setSelectedNames(prev => {
        const next = new Set(prev)
        for (const n of dropped) next.delete(n)
        return next
      })
      const n = result.deleted.length
      const msg = `Deleted ${n} ${fam.slice(0, -1)} ${vars.attribute} profile${n !== 1 ? 's' : ''}: ${result.deleted.slice(0, 3).join(', ')}${n > 3 ? '…' : ''}`
      toast.success(msg); appLog('INFO', msg)
    },
    onError: (e: Error) => { toast.error(`Delete failed: ${e.message}`); appLog('ERROR', `Profile delete failed: ${e.message}`) },
  })

  const isPending = activeTab === 'loads' ? uploadLoadMut.isPending
    : activeTab === 'links' ? uploadLinkMut.isPending
    : uploadGenMut.isPending

  const handleFile = (f: File) =>
    activeTab === 'loads' ? uploadLoadMut.mutate(f)
    : activeTab === 'links' ? uploadLinkMut.mutate({ file: f, attribute: effectiveAttr })
    : uploadGenMut.mutate({ file: f, attribute: effectiveAttr })

  // ── Template horizon ──────────────────────────────────────────────────────
  // Default 'simulation' = use n.snapshots (the simulation horizon). Picker
  // lets the user override with a custom range or fall back to the original
  // 168-h sample. Persisted to localStorage so the choice survives reloads.
  type HorizonMode = 'simulation' | 'custom' | 'sample'
  const HORIZON_KEY = 'timeseries:template-horizon'
  type HorizonState = { mode: HorizonMode; start: string; end: string; freq: string }
  const loadHorizon = (): HorizonState => {
    try {
      const raw = localStorage.getItem(HORIZON_KEY)
      if (raw) return { mode: 'simulation', start: '', end: '', freq: 'h', ...JSON.parse(raw) }
    } catch { /* ignore */ }
    return { mode: 'simulation', start: '', end: '', freq: 'h' }
  }
  const [horizon, setHorizonState] = useState<HorizonState>(loadHorizon())
  const setHorizon = (next: HorizonState) => {
    setHorizonState(next)
    try { localStorage.setItem(HORIZON_KEY, JSON.stringify(next)) } catch { /* ignore */ }
  }
  const [horizonMenuOpen, setHorizonMenuOpen] = useState(false)
  // Read current simulation snapshots so the picker can show the user how big
  // the default-mode template will be. Refetches whenever the horizon changes
  // upstream (Model Horizon panel).
  const { data: snapInfo } = useQuery({
    queryKey: nk(currentProject, 'snapshots'),
    queryFn: networkApi.getSnapshots,
  })

  // Builds the request options the template endpoints expect, derived from the
  // current horizon state. Single source of truth — load / generator / link
  // download paths all call this.
  const horizonParams = (): {
    start?: string; end?: string; freq?: string; useSnapshots?: boolean
  } => {
    if (horizon.mode === 'custom' && horizon.start && horizon.end) {
      return { start: horizon.start, end: horizon.end, freq: horizon.freq, useSnapshots: false }
    }
    if (horizon.mode === 'sample') return { useSnapshots: false }
    return { useSnapshots: true }
  }

  const handleDownloadTemplate = async () => {
    try {
      const h = horizonParams()
      appLog('INFO', `Downloading ${activeTab} profile template (${horizon.mode})…`)
      let blob: Blob, filename: string
      if (activeTab === 'loads') {
        blob = await networkApi.downloadLoadTemplate({ section: loadSection, ...h })
        filename = `load_${loadSection}_template.xlsx`
      } else if (activeTab === 'links') {
        blob = await networkApi.downloadLinkTemplate(effectiveAttr, h)
        filename = `link_${effectiveAttr}_template.xlsx`
      } else {
        blob = await networkApi.downloadGeneratorTemplate(activeTab, effectiveAttr, h)
        filename = `${activeTab}_${effectiveAttr}_template.xlsx`
      }
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = filename; a.click()
      URL.revokeObjectURL(url)
      appLog('INFO', `Template downloaded: ${filename}`)
    } catch {
      toast.error('Template download failed')
      appLog('ERROR', 'Template download failed')
    }
  }

  const handleDownloadSingleLoadTemplate = async (loadName: string) => {
    try {
      const blob = await networkApi.downloadLoadTemplate({ loadName, ...horizonParams() })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `load_${loadName.replace(/\s+/g, '_')}_template.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      appLog('INFO', `Per-load template downloaded for '${loadName}'`)
    } catch {
      toast.error(`Template download failed for ${loadName}`)
    }
  }

  const handleSingleLoadUpload = (loadName: string, file: File) => {
    // The backend endpoint matches columns by name; a single-column file with
    // the load's name as the column header is the "individual" upload path.
    // We DON'T pass an onSuccess override here — that would replace the global
    // success handler and skip cache invalidation.
    pendingSingleLoadRef.current = loadName
    uploadLoadMut.mutate(file)
  }

  // Per-generator: download a single-column template using the active
  // attribute (p_max_pu for renewable/DR/availability; p_min_pu for must-run).
  // The backend derives the row shape from the generator's own carrier, so a
  // wind generator downloaded from the Renewables tab gets a wind profile.
  const handleDownloadSingleGenTemplate = async (genName: string) => {
    try {
      const blob = await networkApi.downloadGeneratorTemplate(
        activeTab === 'conventional' ? 'conventional'
          : activeTab === 'dr' ? 'dr'
          : 'renewable',
        effectiveAttr,
        horizonParams(),
        genName,
      )
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `generator_${genName.replace(/\s+/g, '_')}_${effectiveAttr}_template.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      appLog('INFO', `Per-generator template downloaded for '${genName}' (${effectiveAttr})`)
    } catch {
      toast.error(`Template download failed for ${genName}`)
    }
  }

  const handleSingleGenUpload = (genName: string, file: File) => {
    pendingSingleGenRef.current = genName
    uploadGenMut.mutate({ file, attribute: effectiveAttr })
  }

  const handleDownloadSingleLinkTemplate = async (linkName: string) => {
    try {
      const blob = await networkApi.downloadLinkTemplate(effectiveAttr, horizonParams(), linkName)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `link_${linkName.replace(/\s+/g, '_')}_${effectiveAttr}_template.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      appLog('INFO', `Per-link template downloaded for '${linkName}' (${effectiveAttr})`)
    } catch {
      toast.error(`Template download failed for ${linkName}`)
    }
  }

  const handleSingleLinkUpload = (linkName: string, file: File) => {
    pendingSingleLinkRef.current = linkName
    uploadLinkMut.mutate({ file, attribute: effectiveAttr })
  }

  // Selected-component meta. For generators + links we honour the active
  // attribute so the rows-pill above the chart matches what's plotted.
  const selectedMeta =
    activeTab === 'loads' ? lProfiles[selectedName ?? '']
    : activeTab === 'links' ? (selectedName ? linkActiveAttrMeta(selectedName) : undefined)
    : selectedName ? genActiveAttrMeta(selectedName)
    : undefined

  // Summary table items. For generators + links, swap in the active
  // attribute's meta block so peak/avg numbers reflect the chart, not
  // whichever attribute happens to be at the top level of the response.
  const summaryItems = activeTab === 'loads'
    ? loadsWithProfile.map(l => ({ name: l.name, bus: l.bus, meta: lProfiles[l.name] }))
    : activeTab === 'links'
    ? linksWithProfile.map(l => ({ name: l.name, bus: `${l.bus0} → ${l.bus1}`, meta: linkActiveAttrMeta(l.name) }))
    : gensWithProfile.map(g => ({ name: g.name, bus: g.bus, meta: genActiveAttrMeta(g.name) }))

  const sectionCfg = LOAD_SECTIONS.find(s => s.id === loadSection)!

  const showAggregate = viewMode === 'aggregate'
  // Title shown above the chart on the right pane.
  const aggregateTitle = activeTab === 'loads'
    ? `Σ ${sectionCfg.label}`
    : (activeTab === 'conventional' || activeTab === 'dr' || activeTab === 'links')
      ? `Σ ${tabCfg.label} · ${activeAttrCfg.label}`
      : `Σ ${tabCfg.label}`
  const explicitlySelectedInView = visibleItems.filter(i => selectedNames.has(i.name)).length

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <PageHeader
        eyebrow="DATA · TIME SERIES"
        title="Time series library"
        subtitle="Hourly profiles attached to network components, organized by category."
      />

      {/* ── Tab bar ────────────────────────────────────────────────────── */}
      <div className="flex items-end gap-0 px-3 pt-2 border-b border-border shrink-0 bg-panel">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors flex flex-col items-start gap-0.5
              ${activeTab === tab.id
                ? 'border-accent text-accent bg-bg rounded-t'
                : 'border-transparent text-muted hover:text-text'}`}
          >
            <span>{tab.label}</span>
            <span className="text-[9px] font-normal opacity-70">{tab.subtitle}</span>
          </button>
        ))}
      </div>

      {/* ── Toolbar: load section sub-tabs (loads only) + conventional sub-attribute toggle + view-mode toggle ── */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border shrink-0 bg-bg">
        {activeTab === 'loads' && LOAD_SECTIONS.map(sec => {
          const Icon = sec.Icon
          const active = sec.id === loadSection
          const count = sectionCounts[sec.id]
          return (
            <button
              key={sec.id}
              onClick={() => { setLoadSection(sec.id); setSelectedName(null) }}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors
                ${active
                  ? 'bg-accent/15 text-accent border border-accent/40'
                  : 'text-muted border border-transparent hover:bg-accent/5 hover:text-text'}`}
            >
              <Icon size={12} />
              <span>{sec.label}</span>
              <span className={`text-[9px] font-mono ${active ? 'text-accent' : 'text-muted/70'}`}>{count}</span>
            </button>
          )
        })}
        {/* Sub-attribute pills — shown on Conventional / DR / Links. Each
            tab tracks its OWN selection (convAttr / drAttr / linkAttr) so
            switching back to a tab preserves what was last chosen there.
            Selecting a different attribute clears the multi-select set
            since the previously selected components don't map cleanly
            (e.g. switching Conventional from p_max_pu to marginal_cost). */}
        {activeTab === 'conventional' && CONV_ATTRS.map(a => {
          const Icon = a.Icon
          const active = a.id === convAttr
          return (
            <button
              key={a.id}
              onClick={() => { setConvAttr(a.id); setSelectedNames(new Set()) }}
              title={a.description}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors
                ${active
                  ? 'bg-accent/15 text-accent border border-accent/40'
                  : 'text-muted border border-transparent hover:bg-accent/5 hover:text-text'}`}
            >
              <Icon size={12} />
              <span>{a.label}</span>
              <span className={`text-[9px] font-mono ${active ? 'text-accent' : 'text-muted/70'}`}>{a.shortLabel}</span>
            </button>
          )
        })}
        {activeTab === 'dr' && DR_ATTRS.map(a => {
          const Icon = a.Icon
          const active = a.id === drAttr
          return (
            <button
              key={a.id}
              onClick={() => { setDrAttr(a.id); setSelectedNames(new Set()) }}
              title={a.description}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors
                ${active
                  ? 'bg-accent/15 text-accent border border-accent/40'
                  : 'text-muted border border-transparent hover:bg-accent/5 hover:text-text'}`}
            >
              <Icon size={12} />
              <span>{a.label}</span>
              <span className={`text-[9px] font-mono ${active ? 'text-accent' : 'text-muted/70'}`}>{a.shortLabel}</span>
            </button>
          )
        })}
        {activeTab === 'links' && LINK_ATTRS.map(a => {
          const Icon = a.Icon
          const active = a.id === linkAttr
          return (
            <button
              key={a.id}
              onClick={() => { setLinkAttr(a.id); setSelectedNames(new Set()) }}
              title={a.description}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors
                ${active
                  ? 'bg-accent/15 text-accent border border-accent/40'
                  : 'text-muted border border-transparent hover:bg-accent/5 hover:text-text'}`}
            >
              <Icon size={12} />
              <span>{a.label}</span>
              <span className={`text-[9px] font-mono ${active ? 'text-accent' : 'text-muted/70'}`}>{a.shortLabel}</span>
            </button>
          )
        })}
        <div className="flex-1" />
        <div className="flex items-center gap-0 border border-border rounded overflow-hidden">
          <button
            onClick={() => setViewMode('individual')}
            className={`px-2 py-0.5 text-[10px] font-medium transition-colors
              ${viewMode === 'individual' ? 'bg-accent text-white' : 'text-muted hover:text-text bg-bg'}`}
          >
            Individual
          </button>
          <button
            onClick={() => setViewMode('aggregate')}
            className={`flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium transition-colors
              ${viewMode === 'aggregate' ? 'bg-accent text-white' : 'text-muted hover:text-text bg-bg'}`}
            title="Stack each selected (or visible) profile with its own colour"
          >
            <BarChart3 size={10} /> Aggregate
          </button>
        </div>
      </div>

      {/* ── Three-column body ──────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Left: component list */}
        <div className="w-56 shrink-0 border-r border-border flex flex-col overflow-hidden">
          <div className="px-3 py-2 border-b border-border shrink-0">
            <p className="text-xs font-semibold text-text">
              {activeTab === 'loads'
                ? `${sectionCfg.label} loads`
                : activeTab === 'links' ? 'Links' : 'Generators'}
            </p>
            <p className="text-[10px] text-muted mt-0.5">{withProfileCount}/{totalCount} with profile</p>
            {totalCount > 0 && (
              <div className="flex items-center gap-1 mt-1.5">
                <button
                  onClick={selectAllVisible}
                  className="text-[10px] text-accent hover:underline"
                  title="Select every item in this view"
                >Select all</button>
                <span className="text-[10px] text-muted">·</span>
                <button
                  onClick={clearSelection}
                  disabled={selectedNames.size === 0}
                  className="text-[10px] text-muted hover:text-text disabled:opacity-40"
                >Clear ({selectedNames.size})</button>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto">
            {totalCount === 0 && (
              <p className="p-3 text-xs text-muted">
                {activeTab === 'loads'
                  ? `No ${sectionCfg.label.toLowerCase()} loads in network.`
                  : activeTab === 'links' ? 'No links in network.' : `No ${activeTab} generators in network.`}
              </p>
            )}

            {activeTab === 'loads' ? (
              <>
                {loadsWithProfile.map(l => (
                  <LoadItem key={l.name} load={l} meta={lProfiles[l.name]}
                    selected={selectedName === l.name}
                    checked={selectedNames.has(l.name)}
                    onClick={() => setSelectedName(l.name)}
                    onCheck={() => toggleCheck(l.name)}
                    onDownload={() => handleDownloadSingleLoadTemplate(l.name)}
                    onUpload={f => handleSingleLoadUpload(l.name, f)}
                    onDelete={() => deleteTsMut.mutate({ component: 'loads', attribute: 'p_set', name: l.name })}
                  />
                ))}
                {loadsWithout.length > 0 && loadsWithProfile.length > 0 && (
                  <div className="px-3 py-1 border-t border-border/40">
                    <p className="text-[10px] text-muted font-semibold uppercase tracking-wide">No profile</p>
                  </div>
                )}
                {loadsWithout.map(l => (
                  <LoadItem key={l.name} load={l} meta={lProfiles[l.name]}
                    selected={selectedName === l.name}
                    checked={selectedNames.has(l.name)}
                    onClick={() => setSelectedName(l.name)}
                    onCheck={() => toggleCheck(l.name)}
                    onDownload={() => handleDownloadSingleLoadTemplate(l.name)}
                    onUpload={f => handleSingleLoadUpload(l.name, f)}
                  />
                ))}
              </>
            ) : activeTab === 'links' ? (
              <>
                {linksWithProfile.map(l => (
                  <LinkItem key={l.name} link={l} meta={liProfiles[l.name]}
                    hasActive={true}
                    activeRows={linkActiveRows(l.name)}
                    selected={selectedName === l.name}
                    checked={selectedNames.has(l.name)}
                    onClick={() => setSelectedName(l.name)}
                    onCheck={() => toggleCheck(l.name)}
                    onDownload={() => handleDownloadSingleLinkTemplate(l.name)}
                    onUpload={f => handleSingleLinkUpload(l.name, f)}
                    onDelete={() => deleteTsMut.mutate({ component: 'links', attribute: effectiveAttr, name: l.name })}
                    attrLabel={effectiveAttr}
                  />
                ))}
                {linksWithout.length > 0 && linksWithProfile.length > 0 && (
                  <div className="px-3 py-1 border-t border-border/40">
                    <p className="text-[10px] text-muted font-semibold uppercase tracking-wide">No profile</p>
                  </div>
                )}
                {linksWithout.map(l => (
                  <LinkItem key={l.name} link={l} meta={liProfiles[l.name]}
                    hasActive={false}
                    activeRows={undefined}
                    selected={selectedName === l.name}
                    checked={selectedNames.has(l.name)}
                    onClick={() => setSelectedName(l.name)}
                    onCheck={() => toggleCheck(l.name)}
                    onDownload={() => handleDownloadSingleLinkTemplate(l.name)}
                    onUpload={f => handleSingleLinkUpload(l.name, f)}
                    attrLabel={effectiveAttr}
                  />
                ))}
              </>
            ) : (
              <>
                {gensWithProfile.map(g => (
                  <GenItem key={g.name} gen={g} meta={gProfiles[g.name]}
                    hasActive={genHasProfileForActive(g.name)}
                    activeRows={genActiveRows(g.name)}
                    selected={selectedName === g.name}
                    checked={selectedNames.has(g.name)}
                    onClick={() => setSelectedName(g.name)}
                    onCheck={() => toggleCheck(g.name)}
                    onDownload={() => handleDownloadSingleGenTemplate(g.name)}
                    onUpload={f => handleSingleGenUpload(g.name, f)}
                    onDelete={() => deleteTsMut.mutate({ component: 'generators', attribute: effectiveAttr, name: g.name })}
                    attrLabel={effectiveAttr}
                  />
                ))}
                {gensWithout.length > 0 && gensWithProfile.length > 0 && (
                  <div className="px-3 py-1 border-t border-border/40">
                    <p className="text-[10px] text-muted font-semibold uppercase tracking-wide">No profile</p>
                  </div>
                )}
                {gensWithout.map(g => (
                  <GenItem key={g.name} gen={g} meta={gProfiles[g.name]}
                    hasActive={genHasProfileForActive(g.name)}
                    activeRows={genActiveRows(g.name)}
                    selected={selectedName === g.name}
                    checked={selectedNames.has(g.name)}
                    onClick={() => setSelectedName(g.name)}
                    onCheck={() => toggleCheck(g.name)}
                    onDownload={() => handleDownloadSingleGenTemplate(g.name)}
                    onUpload={f => handleSingleGenUpload(g.name, f)}
                    attrLabel={effectiveAttr}
                  />
                ))}
              </>
            )}
          </div>
        </div>

        {/* Centre: upload + info */}
        <div className="w-80 shrink-0 flex flex-col overflow-y-auto p-4 gap-4 border-r border-border">

          <div className="flex items-center gap-3">
            <h2 className="text-[13px] font-semibold text-text tracking-[-0.01em]">
              {activeTab === 'loads'
                ? `${sectionCfg.label} profiles`
                : (activeTab === 'conventional' || activeTab === 'dr' || activeTab === 'links')
                  ? `${tabCfg.label} · ${activeAttrCfg.label}`
                  : `${tabCfg.label} profiles`}
            </h2>
            <div className="flex-1" />
            {/* Split button: Joint template ↓ horizon options.
                The chevron opens a popover where the user can switch the
                template horizon (simulation snapshots / custom range / 1-week
                sample). The state is read by handleDownloadTemplate via
                horizonParams(). */}
            <div className="flex items-center relative">
              <button
                onClick={handleDownloadTemplate}
                className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-l text-xs text-muted hover:text-accent hover:border-accent transition-colors"
                title={activeTab === 'loads'
                  ? `Download joint template for ${sectionCfg.label.toLowerCase()} loads`
                  : (activeTab === 'conventional' || activeTab === 'dr' || activeTab === 'links')
                    ? `Download ${activeAttrCfg.label.toLowerCase()} (${activeAttrCfg.shortLabel}) template`
                    : 'Download joint template'}
              >
                <FileSpreadsheet size={13} />
                Joint template
                <span className="text-[10px] text-muted/70 ml-1">
                  ({horizon.mode === 'simulation'
                    ? `sim · ${snapInfo?.count ?? '?'} snaps`
                    : horizon.mode === 'sample' ? 'sample · 168h'
                    : 'custom'})
                </span>
              </button>
              <button
                onClick={() => setHorizonMenuOpen(o => !o)}
                className="flex items-center px-1.5 py-1.5 border border-l-0 border-border rounded-r text-muted hover:text-accent hover:border-accent transition-colors"
                title="Choose template horizon"
              >
                <ChevronDown size={11} />
              </button>
              {horizonMenuOpen && (
                <>
                  {/* dismiss layer */}
                  <div className="fixed inset-0 z-[100]" onClick={() => setHorizonMenuOpen(false)} />
                  <div className="absolute top-full right-0 mt-1 z-[101] w-72 bg-bg border border-border rounded shadow-lg p-3 text-xs">
                    <div className="font-semibold text-text mb-2">Template horizon</div>

                    {(['simulation', 'custom', 'sample'] as const).map(m => (
                      <label key={m} className="flex items-start gap-2 py-1 cursor-pointer">
                        <input
                          type="radio" name="horizon-mode" value={m}
                          checked={horizon.mode === m}
                          onChange={() => setHorizon({ ...horizon, mode: m })}
                          className="mt-0.5"
                        />
                        <div>
                          <div className="text-text">
                            {m === 'simulation' ? 'Simulation horizon (default)'
                              : m === 'custom' ? 'Custom range'
                              : 'Sample · 1 week (168 h)'}
                          </div>
                          <div className="text-[10px] text-muted">
                            {m === 'simulation'
                              ? snapInfo
                                ? `${snapInfo.count} snapshot(s) — ${snapInfo.snapshots?.[0]?.slice(0, 16) ?? '—'} → ${snapInfo.snapshots?.[snapInfo.count - 1]?.slice(0, 16) ?? '—'}`
                                : 'No snapshots yet — falls back to sample.'
                              : m === 'custom'
                              ? 'Choose your own start, end, frequency.'
                              : 'Hourly Monday-this-week template.'}
                          </div>
                        </div>
                      </label>
                    ))}

                    {horizon.mode === 'custom' && (
                      <div className="flex flex-col gap-2 mt-2 pt-2 border-t border-border/40">
                        <label className="flex flex-col gap-0.5">
                          <span className="text-[10px] text-muted">Start</span>
                          <input
                            type="datetime-local" value={horizon.start}
                            onChange={e => setHorizon({ ...horizon, start: e.target.value })}
                            className="px-1.5 py-1 border border-border rounded font-mono text-[11px] bg-bg"
                          />
                        </label>
                        <label className="flex flex-col gap-0.5">
                          <span className="text-[10px] text-muted">End</span>
                          <input
                            type="datetime-local" value={horizon.end}
                            onChange={e => setHorizon({ ...horizon, end: e.target.value })}
                            className="px-1.5 py-1 border border-border rounded font-mono text-[11px] bg-bg"
                          />
                        </label>
                        <label className="flex flex-col gap-0.5">
                          <span className="text-[10px] text-muted">Resolution</span>
                          <select
                            value={horizon.freq}
                            onChange={e => setHorizon({ ...horizon, freq: e.target.value })}
                            className="px-1.5 py-1 border border-border rounded text-[11px] bg-bg"
                          >
                            <option value="h">Hourly (h)</option>
                            <option value="3h">3-hourly</option>
                            <option value="6h">6-hourly</option>
                            <option value="D">Daily (D)</option>
                            <option value="W">Weekly (W)</option>
                            <option value="MS">Monthly (MS)</option>
                          </select>
                        </label>
                        {horizon.mode === 'custom' && (!horizon.start || !horizon.end) && (
                          <div className="text-[10px] text-warn">
                            Set both start and end before downloading.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>

          <UploadZone
            onFile={handleFile}
            label={activeTab === 'loads'
              ? `Drop a joint ${sectionCfg.label.toLowerCase()} profile file`
              : 'Drop Excel or CSV here'}
          />
          {isPending && <p className="text-xs text-accent animate-pulse text-center">Uploading…</p>}

          <div className="border border-border/60 rounded-lg p-3 bg-panel text-xs text-muted space-y-1">
            <p className="font-semibold text-text text-[11px]">Expected file format</p>
            <p>• First column: timestamps (e.g. <span className="font-mono">2024-01-01 00:00</span>)</p>
            {activeTab === 'loads' ? (
              <>
                <p>• Remaining columns: one per load, named exactly as the load name</p>
                <p>• Single-load uploads: one column with that load’s name (icons in the list)</p>
                <p>• Values: active power demand in <span className="font-mono">MW</span></p>
                <p className="pt-1 text-[10px]">{sectionCfg.shapeNote}</p>
              </>
            ) : activeTab === 'links' ? (
              <>
                <p>• Remaining columns: one per link, named exactly as the link name</p>
                <p>• Values: <span className="font-mono">{activeAttrCfg.shortLabel}</span>{' '}in{' '}
                  <span className="font-mono">{activeAttrCfg.unit}</span>
                  {activeAttrCfg.unit === 'pu' && ' (0 to 1)'}</p>
                <p className="pt-1 text-[10px]">{activeAttrCfg.description}</p>
                {linkAttr === 'p_min_pu' && (
                  <p className="text-[10px] text-amber-600">
                    Tip: to model a fixed-profile must-run Link (e.g. a datacenter), upload the
                    SAME file as both <span className="font-mono">p_max_pu</span> AND
                    {' '}<span className="font-mono">p_min_pu</span>. The LP will then dispatch the
                    Link exactly on the profile (no freedom either way).
                  </p>
                )}
              </>
            ) : activeTab === 'dr' ? (
              <>
                <p>• Remaining columns: one per DR generator, named exactly as the generator name</p>
                <p>• Values: <span className="font-mono">{activeAttrCfg.shortLabel}</span>{' '}in{' '}
                  <span className="font-mono">{activeAttrCfg.unit}</span>
                  {activeAttrCfg.unit === 'pu' && ' (0 to 1)'}</p>
                <p className="pt-1 text-[10px]">{activeAttrCfg.description}</p>
              </>
            ) : activeTab === 'conventional' ? (
              <>
                <p>• Remaining columns: one per generator, named exactly as the generator name</p>
                <p>• Values: <span className="font-mono">{activeAttrCfg.shortLabel}</span>{' '}in{' '}
                  <span className="font-mono">{activeAttrCfg.unit}</span>
                  {activeAttrCfg.unit === 'pu' && ' (0 to 1)'}</p>
                <p className="pt-1 text-[10px]">{activeAttrCfg.description}</p>
                {convAttr === 'p_min_pu' && (
                  <p className="text-[10px] text-amber-600">
                    Note: a hard must-run floor can cause infeasibility if demand falls below
                    Σ <span className="font-mono">p_min_pu · p_nom</span>. Configure load-shedding or a slack generator.
                  </p>
                )}
                {convAttr === 'marginal_cost' && (
                  <p className="text-[10px] text-amber-600">
                    Note: overrides the static <span className="font-mono">marginal_cost</span> per
                    snapshot during solve. Reverts on save → reload only if the project's network.nc
                    captures generators_t.marginal_cost (it does). Empty cells fall back to the static
                    scalar.
                  </p>
                )}
              </>
            ) : (
              <>
                <p>• Remaining columns: one per generator, named exactly as the generator name</p>
                <p>• Values: capacity factor in <span className="font-mono">pu</span> (0 to 1)</p>
              </>
            )}
            <p>• Formats: <span className="font-mono">.xlsx</span>, <span className="font-mono">.xls</span>, <span className="font-mono">.csv</span></p>
          </div>

          {summaryItems.length > 0 && (() => {
            // Active unit for the summary table — the sub-toggle (on
            // Conventional / DR / Links tabs) drives this. p_max_pu /
            // p_min_pu keep 'pu'; marginal_cost switches to €/MWh.
            const summaryUnit: ProfileUnit =
              (activeTab === 'conventional' || activeTab === 'dr' || activeTab === 'links')
                ? activeAttrCfg.unit : tabCfg.unit
            const summaryFmt = fmtFor(summaryUnit)
            return (
            <div>
              <p className="text-[11px] font-semibold text-text mb-2">Uploaded profiles</p>
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="border-b border-border bg-panel">
                    {['Name', activeTab === 'links' ? 'Route' : 'Bus', 'Pts', 'Peak', 'Avg', sumLabel(summaryUnit)].map(h => (
                      <th key={h} className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summaryItems.map((item, i) => {
                    if (!item.meta?.has_profile) return null
                    return (
                      <tr key={item.name} onClick={() => setSelectedName(item.name)}
                        className={`border-b border-border/40 cursor-pointer transition-colors
                          ${selectedName === item.name ? 'bg-accent/10' : i % 2 === 0 ? 'bg-bg hover:bg-accent/5' : 'bg-panel hover:bg-accent/5'}`}
                      >
                        <td className="px-2 py-1 font-semibold text-text truncate max-w-[80px]">{item.name}</td>
                        <td className="px-2 py-1 text-muted truncate max-w-[60px]">{item.bus}</td>
                        <td className="px-2 py-1 font-mono">{item.meta.rows}</td>
                        <td className="px-2 py-1 font-mono">{
                          activeTab === 'loads'
                            ? fmtMW(item.meta.peak)
                            : tabCfg.scaleByPnom
                              ? fmtMW(item.meta.peak * (pNomByGenerator[item.name] ?? 0))
                              : summaryFmt(item.meta.peak)
                        }</td>
                        <td className="px-2 py-1 font-mono">{
                          activeTab === 'loads'
                            ? fmtMW(item.meta.mean)
                            : tabCfg.scaleByPnom
                              ? fmtMW(item.meta.mean * (pNomByGenerator[item.name] ?? 0))
                              : summaryFmt(item.meta.mean)
                        }</td>
                        {/* Σ Energy (MWh) for MW profiles, Σ FLH (hours) for
                            pu profiles, Σ €·h/MWh for marginal_cost. */}
                        <td className="px-2 py-1 font-mono">{
                          activeTab === 'loads'
                            ? fmtEnergy(item.meta.sum)
                            : tabCfg.scaleByPnom
                              ? fmtEnergy(item.meta.sum * (pNomByGenerator[item.name] ?? 0))
                              : fmtSum(item.meta.sum, summaryUnit)
                        }</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            )
          })()}
        </div>

        {/* Right: chart */}
        <div className="flex-1 min-w-0 border-l border-border flex flex-col overflow-hidden">
          <div className="px-3 py-2 border-b border-border shrink-0 flex items-center gap-2">
            {showAggregate ? (
              <>
                <BarChart3 size={12} className="text-muted" />
                <span className="text-xs font-semibold text-text truncate">
                  {aggregateTitle}{explicitlySelectedInView > 0
                    ? ` (selected ${explicitlySelectedInView})`
                    : ` (all ${aggregateNames.length})`}
                </span>
              </>
            ) : (
              <>
                <Download size={12} className="text-muted" />
                <span className="text-xs font-semibold text-text truncate">
                  {selectedName ?? 'Select a component'}
                </span>
                {selectedMeta?.has_profile && (
                  <span className="ml-auto text-[9px] text-success font-mono shrink-0">
                    {selectedMeta.rows} pts
                  </span>
                )}
              </>
            )}
          </div>

          <div className="flex-1 min-h-0 p-3">
            {/* Sub-toggle attribute defines its own unit (pu for p_max_pu /
                p_min_pu, €/MWh for marginal_cost). Pick the attribute's unit
                when on Conventional / DR / Links; otherwise fall back to the
                tab's own unit. */}
            {(() => {
              const chartUnit: ProfileUnit =
                (activeTab === 'conventional' || activeTab === 'dr' || activeTab === 'links')
                  ? activeAttrCfg.unit : tabCfg.unit
              return showAggregate
                ? <AggregateChart
                    component={tabCfg.component}
                    attribute={effectiveAttr}
                    names={aggregateNames}
                    unit={chartUnit}
                    label={activeTab === 'loads' ? sectionCfg.label : tabCfg.label}
                    scaleByName={scaleByName}
                  />
                : selectedName
                  ? <ProfileChart
                      name={selectedName}
                      component={tabCfg.component}
                      attribute={effectiveAttr}
                      unit={chartUnit}
                      scale={scaleForSelected}
                    />
                  : (
                    <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
                      <p className="text-xs">Click a component to preview its profile</p>
                    </div>
                  )
            })()}
          </div>
        </div>

      </div>
    </div>
  )
}
