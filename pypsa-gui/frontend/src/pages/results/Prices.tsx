import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend,
} from 'recharts'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import type { Bus } from '../../api/types'
import { type TSPayload, shortStamp, downloadCSV, KPI, ChartActions,
  ChartCard, Seg, CHART_GRID, CHART_AXIS, CHART_LEGEND, CHART_TOOLTIP, yAxisLabel,
  useSeasonalViewMode, SeasonalLineCardGrid, durationCurvePoints,
  WEEKLY_MIN_DAYS, MONTHLY_MIN_DAYS } from './shared'
import { useResultsFilter, resolveRange } from './filterContext'
import { useResultsWindow } from '../../hooks/useResultsWindow'

// ── Prices tab ───────────────────────────────────────────────────────────────
// Extracted from LoadFlow. Three sections:
//   1. KPI strip — system mean / peak / trough marginal price, CO₂ shadow price,
//      negative-hour count.
//   2. Per-bus prices table (mean/peak/trough), CSV-exportable.
//   3. Hourly marginal-price chart (Average / Per-bus toggle, LP-dual /
//      Merit-order toggle), plus the Price drivers table identifying the
//      marginal generator for each high-price (bus, snapshot) cell.
//
// Multi-period: respects the period sub-tab via resolveRange + the parallel
// `periods` array on the TS payload. Aggregated view scans every period;
// per-period view limits to a contiguous slice.

type PricesPayload = TSPayload & {
  data_adjusted?: number[][]
  negative_hours?: number
  // Diagnostic: 'lp' = LP duals captured normally; 'fallback' = duals were
  // all zero so the backend substituted highest-marginal-cost dispatching
  // generator per snapshot. The frontend surfaces a banner in the latter
  // case so users don't mistake fallback values for real LP duals.
  source?: 'lp' | 'fallback'
  fallback_per_snapshot?: number[]
  note?: string
}

const PER_BUS_CHART_CAP = 12

export default function Prices() {
  const currentProject = useUIStore(s => s.currentProject)
  // Positional bounds for the active Horizon filter, resolved against the
  // SNAPSHOT INDEX (not a results payload) — same hook Dispatch uses so the
  // prices query below can be windowed before any payload exists.
  const { win, winValid } = useResultsWindow(currentProject)
  // Refs for SVG export — one per chart section.
  const hourlyChartRef   = useRef<HTMLDivElement | null>(null)
  const durationChartRef = useRef<HTMLDivElement | null>(null)
  // Marginal prices are LP duals — they only exist on the LOPF/SCLOPF solve.
  // PyPSA's `n.pf()` doesn't compute duals, so always pin source='lopf'.
  const { data: priceTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'prices', 'lopf', win.from, win.to),
    queryFn: () => resultsApi.getPrices('lopf', win),
    enabled: winValid,
  })
  // CO₂ emissions for the cap shadow price KPI. Other CO₂ data lives in the
  // Emissions tab.
  const { data: emissions } = useQuery({
    queryKey: nk(currentProject, 'results', 'emissions'),
    queryFn: resultsApi.getEmissions,
  })
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses })

  const filter = useResultsFilter()
  const tsP = priceTS as PricesPayload | null
  const refPeriods = tsP?.periods
  const range = resolveRange(tsP?.index ?? [], filter, refPeriods)

  // Per-bus stats over the active period range.
  const busStats = useMemo(() => {
    if (!tsP) return null
    const byName = new Map((buses as Bus[]).map(b => [b.name, b]))
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsP.data.length - 1, range.to)
    return tsP.columns.map((name, ci) => {
      let s = 0, n = 0, peak = -Infinity, trough = Infinity
      if (rFrom <= rTo) {
        for (let row = rFrom; row <= rTo; row++) {
          const v = tsP.data[row][ci]
          if (Number.isFinite(v)) {
            s += v; n += 1
            if (v > peak) peak = v
            if (v < trough) trough = v
          }
        }
      }
      const mean = n ? s / n : 0
      return {
        name,
        carrier: byName.get(name)?.carrier ?? '',
        v_nom:   byName.get(name)?.v_nom ?? 0,
        meanPrice: mean,
        peakPrice: n ? peak : 0,
        troughPrice: n ? trough : 0,
      }
    }).sort((a, b) => b.meanPrice - a.meanPrice)
  }, [tsP, buses, range.from, range.to])

  const [priceView, setPriceView] = useState<'avg' | 'per_bus'>('avg')
  const [priceSource, setPriceSource] = useState<'lp' | 'merit'>('lp')
  // Seasonal averaged view (Weekly / Monthly / Full timeframe). The hook
  // greys out modes that can't be filled — needs ≥ N days of data in every
  // season — and falls back gracefully.
  const {
    setViewMode: setViewModePersist, effectiveMode,
    weeklyAvailable, monthlyAvailable,
  } = useSeasonalViewMode('results:prices-viewmode', tsP?.index ?? [], range)
  const [driverThreshold, setDriverThreshold] = useState<number>(2000)
  const { data: priceDrivers } = useQuery({
    queryKey: nk(currentProject, 'results', 'price_drivers', driverThreshold),
    queryFn: () => resultsApi.getPriceDrivers(driverThreshold, 200),
    enabled: !!tsP,
  })

  // Hourly chart rows — period-scoped.
  const priceChart = useMemo(() => {
    if (!tsP || tsP.columns.length === 0) return null
    const grid = priceSource === 'merit' && tsP.data_adjusted ? tsP.data_adjusted : tsP.data
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsP.data.length - 1, range.to)
    const avgRows: Array<{ snapshot: string; avg: number }> = []
    for (let row = rFrom; row <= rTo; row++) {
      let s = 0, n = 0
      for (const v of grid[row]) {
        if (Number.isFinite(v)) { s += v; n += 1 }
      }
      avgRows.push({ snapshot: tsP.index[row], avg: n ? s / n : 0 })
    }
    const ordered = (busStats ?? []).map(b => b.name)
    const cols = ordered.slice(0, PER_BUS_CHART_CAP)
    const colIdxByName = new Map(tsP.columns.map((c, i) => [c, i]))
    const perBusRows: Array<Record<string, number | string | null>> = []
    for (let row = rFrom; row <= rTo; row++) {
      const obj: Record<string, number | string | null> = { snapshot: tsP.index[row] }
      for (const c of cols) {
        const i = colIdxByName.get(c)
        const v = i != null ? grid[row][i] : null
        obj[c] = Number.isFinite(v as number) ? (v as number) : null
      }
      perBusRows.push(obj)
    }
    return {
      avgRows, perBusRows, cols,
      busTotal: tsP.columns.length,
      negativeHours: tsP.negative_hours ?? 0,
      hasAdjusted: !!tsP.data_adjusted,
    }
  }, [tsP, busStats, priceSource, range.from, range.to])

  // Price duration curve — sorted descending, x-axis = % of hours, y-axis = €/MWh.
  // Single trace (system mean). Reads "what % of hours did the price exceed X?"
  // off the chart at a glance.
  const durationCurve = useMemo(() => {
    if (!priceChart) return null
    const arr = priceChart.avgRows.map(r => r.avg).filter(v => Number.isFinite(v))
    if (arr.length === 0) return null
    return durationCurvePoints(arr).map(p => ({ pct: p.pct, price: p.value }))
  }, [priceChart])

  // KPI strip: weighted-mean / peak / trough across the slice + system spread
  // (max − min across buses) as a congestion proxy.
  const kpis = useMemo(() => {
    if (!busStats || busStats.length === 0) return null
    const means = busStats.map(b => b.meanPrice)
    const peaks = busStats.map(b => b.peakPrice)
    const troughs = busStats.map(b => b.troughPrice)
    const systemMean = means.reduce((a, b) => a + b, 0) / means.length
    const systemPeak = Math.max(...peaks)
    const systemTrough = Math.min(...troughs)
    // Spread = max system-wide peak minus min system-wide trough. Tells the
    // user how locationally varied prices got — a proxy for congestion.
    const spread = systemPeak - systemTrough
    return { systemMean, systemPeak, systemTrough, spread }
  }, [busStats])

  const exportBusStatsCSV = () => {
    if (!busStats) return
    downloadCSV('results_prices_per_bus.csv',
      ['name', 'carrier', 'v_nom_kV', 'mean_eur_per_mwh', 'peak_eur_per_mwh', 'trough_eur_per_mwh'],
      busStats.map(b => [b.name, b.carrier, b.v_nom, b.meanPrice.toFixed(3), b.peakPrice.toFixed(3), b.troughPrice.toFixed(3)]),
    )
    toast.success(`Exported ${busStats.length} rows`)
  }

  if (!tsP) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No marginal-price data yet. Run a LOPF / SCLOPF solve (header → <span className="font-medium text-text">Run LOPF</span>) — prices come from the LP dual variables.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm [&>*]:shrink-0">

      {/* ── Fallback banner (LP duals all zero → analytical fallback) ─── */}
      {tsP.source === 'fallback' && (
        <div className="rounded border border-warn/40 bg-warn/5 px-3 py-2 text-[11px] text-warn">
          <span className="font-semibold">Approximated prices — LP duals weren't captured.</span>
          {' '}
          {tsP.note ?? 'Showing the highest-marginal-cost dispatching generator per snapshot as a proxy.'}
          {' '}These numbers ignore congestion and bus locational variation;
          treat them as an order-of-magnitude estimate, not a market price.
        </div>
      )}

      {/* ── KPI strip ──────────────────────────────────────────── */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text mb-2.5 tracking-[-0.005em]">System prices</h3>
        <div className="grid grid-cols-4 gap-3">
          <KPI accent label="System mean"
               value={kpis ? `${kpis.systemMean.toFixed(2)} €/MWh` : '—'}
               hint="Mean across buses, then averaged over snapshots in the active range. Equivalent to the simple system-wide price." />
          <KPI label="System peak"
               value={kpis ? `${kpis.systemPeak.toFixed(2)} €/MWh` : '—'}
               hint="Highest price observed at any bus×snapshot in the active range. Large values usually point at a binding scarcity hour." />
          <KPI label="System trough"
               value={kpis ? `${kpis.systemTrough.toFixed(2)} €/MWh` : '—'}
               hint="Lowest price observed. Negative values can appear when curtailment_cost > 0 on a marginal renewable." />
          <KPI label="Price spread"
               value={kpis ? `${kpis.spread.toFixed(2)} €/MWh` : '—'}
               hint="Difference between system peak and trough — a coarse congestion proxy. Zero = uniform pricing, large = strong locational variation." />
          {emissions?.cap.active && emissions.cap.shadow_price_eur_per_tCO2 != null && (
            <KPI label="CO₂ shadow price"
                 value={`${emissions.cap.shadow_price_eur_per_tCO2.toFixed(2)} €/tCO₂`}
                 hint="LP dual on the active CO₂ cap. = the marginal cost of one extra tCO₂ avoided." />
          )}
          {priceChart && priceChart.negativeHours > 0 && (
            <KPI label="Negative-price hours"
                 value={`${priceChart.negativeHours}`}
                 hint="Snapshots where at least one bus saw a negative LP dual. Typically a curtailment_cost artefact — switch to Merit order on the chart below." />
          )}
        </div>
      </section>

      {/* ── Per-bus table ──────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Buses · marginal prices ({busStats?.length ?? 0})</h3>
          <button onClick={exportBusStatsCSV} disabled={!busStats}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent disabled:opacity-40"
          ><Download size={11} /> CSV</button>
        </div>
        {!busStats ? (
          <div className="border border-border rounded p-4 text-center text-xs text-muted">
            No marginal prices (LOPF wasn't run, or this network has no global cost objective).
          </div>
        ) : (
          <div className="border border-border rounded overflow-auto max-h-72">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 600 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">v_nom</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Mean €/MWh</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Peak</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Trough</th>
                </tr>
              </thead>
              <tbody>
                {busStats.map((b, i) => (
                  <tr key={b.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 font-mono text-[11px]">{b.name}</td>
                    <td className="px-2 py-1 text-[11px] text-muted">{b.carrier}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{b.v_nom} kV</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{b.meanPrice.toFixed(2)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{b.peakPrice.toFixed(2)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{b.troughPrice.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Hourly marginal price chart ───────────────────────── */}
      {priceChart && (
        <ChartCard
          title="Hourly marginal price"
          hint={priceView === 'per_bus' && priceChart.busTotal > priceChart.cols.length
            ? `top ${priceChart.cols.length} of ${priceChart.busTotal} buses by mean`
            : undefined}
          right={
            <>
              <Seg
                value={priceSource}
                onChange={setPriceSource}
                title="LP dual = raw shadow price of the power-balance constraint. Merit order = LP dual + curtailment_cost added back at hours where a renewable was the marginal dispatch unit."
                options={[
                  { value: 'lp', label: 'LP dual' },
                  { value: 'merit', label: 'Merit order', disabled: !priceChart.hasAdjusted },
                ]}
              />
              <Seg
                value={priceView}
                onChange={setPriceView}
                options={[
                  { value: 'avg', label: 'Average' },
                  { value: 'per_bus', label: 'Per bus' },
                ]}
              />
              <Seg
                value={effectiveMode}
                onChange={setViewModePersist}
                options={[
                  { value: 'weekly', label: 'Weekly', disabled: !weeklyAvailable,
                    title: weeklyAvailable
                      ? 'Averaged week per season — 4 charts (Mon–Sun, 168 h)'
                      : `Greyed out — needs ≥ ${WEEKLY_MIN_DAYS} days of data in all 4 seasons` },
                  { value: 'monthly', label: 'Monthly', disabled: !monthlyAvailable,
                    title: monthlyAvailable
                      ? 'Averaged month per season — 4 charts'
                      : `Greyed out — needs ≥ ${MONTHLY_MIN_DAYS} days of data in all 4 seasons` },
                  { value: 'timeframe', label: 'Full timeframe',
                    title: 'The full dataset — single hourly chart' },
                ]}
              />
              <ChartActions
                chartRef={hourlyChartRef}
                onExportCSV={() => {
                  if (priceView === 'avg') {
                    downloadCSV(`prices_hourly_avg_${priceSource}.csv`,
                      ['snapshot', 'price_EUR_per_MWh'],
                      priceChart.avgRows.map(r => [r.snapshot, r.avg]))
                  } else {
                    const header = ['snapshot', ...priceChart.cols]
                    downloadCSV(`prices_hourly_per_bus_${priceSource}.csv`, header,
                      priceChart.perBusRows.map(r => [r.snapshot, ...priceChart.cols.map(c => r[c] ?? '')]))
                  }
                  toast.success('Exported')
                }}
                filename={`prices_hourly_${priceView}_${priceSource}`} />
            </>
          }
          bodyClassName="p-3"
        >
          {priceSource === 'lp' && priceChart.negativeHours > 0 && (
            <div className="mb-2 text-[10px] text-muted italic">
              {priceChart.negativeHours} snapshot{priceChart.negativeHours === 1 ? '' : 's'} contain at least one
              negative-price bus. This is the LP shadow price; setting <code>curtailment_cost</code> on renewables
              subsidises their dispatch by that amount, which the LP dual reflects as a negative price when the
              renewable is marginal. Switch to <span className="font-medium">Merit order</span> to see prices
              without that subsidy.
            </div>
          )}
          <div ref={hourlyChartRef}>
            {effectiveMode !== 'timeframe' ? (
              <SeasonalLineCardGrid
                mode={effectiveMode}
                rows={priceView === 'avg' ? priceChart.avgRows : priceChart.perBusRows}
                cols={priceView === 'avg' ? ['avg'] : priceChart.cols}
                colors={priceView === 'avg' ? ['#0369a1'] : [
                  '#dc2626', '#0369a1', '#16a34a', '#d97706', '#7c3aed',
                  '#db2777', '#0891b2', '#65a30d', '#c2410c', '#6d28d9',
                  '#e11d48', '#1d4ed8',
                ]}
                unit="€/MWh"
                valueFormatter={(v: number) => `${v.toFixed(2)} €/MWh`}
                height={170}
              />
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <LineChart
                  data={priceView === 'avg' ? priceChart.avgRows : priceChart.perBusRows}
                  margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
                >
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                  <YAxis {...CHART_AXIS} label={yAxisLabel('€/MWh')} />
                  <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} €/MWh`} />
                  {priceView === 'per_bus' && priceChart.cols.length <= 8 && (
                    <Legend {...CHART_LEGEND} />
                  )}
                  {priceView === 'avg' ? (
                    <Line type="monotone" dataKey="avg" name="Average"
                          stroke="#0369a1" strokeWidth={1.8} dot={false} />
                  ) : (
                    priceChart.cols.map((c, i) => (
                      <Line key={c} type="monotone" dataKey={c} name={c}
                            stroke={[
                              '#dc2626', '#0369a1', '#16a34a', '#d97706', '#7c3aed',
                              '#db2777', '#0891b2', '#65a30d', '#c2410c', '#6d28d9',
                              '#e11d48', '#1d4ed8',
                            ][i % 12]}
                            strokeWidth={1.2} dot={false} />
                    ))
                  )}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </ChartCard>
      )}

      {/* ── Price duration curve ─────────────────────────────── */}
      {/* System mean price sorted high-to-low. X axis = % of hours,
          Y axis = €/MWh. Reads "what % of hours did the price exceed X" at
          a glance — the natural shape for revenue/cost/utilisation analysis. */}
      {durationCurve && durationCurve.length > 0 && (
        <ChartCard
          title="Price duration curve"
          hint="system mean, sorted descending"
          right={
            <ChartActions
              chartRef={durationChartRef}
              onExportCSV={() => {
                downloadCSV('prices_duration_curve.csv',
                  ['pct_of_hours', 'price_EUR_per_MWh'],
                  durationCurve.map(r => [r.pct, r.price]))
                toast.success('Exported')
              }}
              filename="prices_duration_curve" />
          }
          bodyClassName="p-3"
        >
          <div ref={durationChartRef}>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={durationCurve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="price-duration-grad" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#0369a1" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#0369a1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="pct" type="number" domain={[0, 100]}
                  tickFormatter={(v: number) => `${v.toFixed(0)} %`} {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} label={yAxisLabel('€/MWh')} />
                <RTooltip {...CHART_TOOLTIP}
                  labelFormatter={(v: number) => `${v.toFixed(1)} % of hours`}
                  formatter={(v: number) => `${v.toFixed(2)} €/MWh`} />
                <Area type="monotone" dataKey="price" stroke="#0369a1" fill="url(#price-duration-grad)"
                      strokeWidth={1.6} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {/* ── Price drivers table ───────────────────────────────── */}
      {priceTS && (
        <section>
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Price drivers</h3>
            <label className="text-[10px] text-muted ml-2">|price| ≥</label>
            <input type="number" step="100" min={0}
              value={driverThreshold}
              onChange={e => setDriverThreshold(Math.max(0, Number(e.target.value) || 0))}
              className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono w-20" />
            <span className="text-[10px] text-muted">€/MWh</span>
            {priceDrivers && (
              <span className="text-[10px] text-muted ml-auto">
                {priceDrivers.total_above_threshold} cell{priceDrivers.total_above_threshold === 1 ? '' : 's'}
                {priceDrivers.truncated && ` (showing top ${priceDrivers.rows.length})`}
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted mb-2">
            For every (bus, snapshot) above the threshold, the table shows the most-likely
            marginal generator and a tag pointing at the cause:
            <span className="ml-1 px-1 rounded bg-danger/10 text-danger font-mono text-[9px]">load_shedding</span>
            <span className="ml-1 px-1 rounded bg-warn/10 text-warn font-mono text-[9px]">thermal_peaker</span>
            <span className="ml-1 px-1 rounded bg-accent/10 text-accent font-mono text-[9px]">transmission</span>
            <span className="ml-1 px-1 rounded bg-muted/10 text-muted font-mono text-[9px]">unattributed</span>.
          </p>
          {!priceDrivers || priceDrivers.rows.length === 0 ? (
            <div className="border border-border rounded p-3 text-center text-[11px] text-muted">
              No cells above {driverThreshold} €/MWh.
            </div>
          ) : (
            <div className="border border-border rounded overflow-auto max-h-80">
              <table className="w-full text-xs border-collapse" style={{ minWidth: 720 }}>
                <thead className="sticky top-0 bg-bg-2 z-10">
                  <tr className="border-b border-border">
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Snapshot</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Bus</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Price (€/MWh)</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Marginal gen</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Gen MC</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Dispatch</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Diagnosis</th>
                  </tr>
                </thead>
                <tbody>
                  {priceDrivers.rows.map((r, i) => (
                    <tr key={i} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[10px]">{shortStamp(r.snapshot)}</td>
                      <td className="px-2 py-1 font-mono text-[11px]">{r.bus}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right font-semibold ${
                        r.price < 0 ? 'text-accent' : r.price > 1000 ? 'text-danger' : 'text-text'
                      }`}>{r.price.toFixed(2)}</td>
                      <td className="px-2 py-1 font-mono text-[10px] truncate" style={{ maxWidth: 180 }}
                        title={r.marginal_gen ?? ''}>
                        {r.marginal_gen ?? '—'}
                      </td>
                      <td className="px-2 py-1 text-[10px] text-muted">{r.carrier || '—'}</td>
                      <td className="px-2 py-1 font-mono text-[10px] text-right">{r.marginal_cost.toFixed(2)}</td>
                      <td className="px-2 py-1 font-mono text-[10px] text-right">{r.dispatch.toFixed(1)}</td>
                      <td className="px-2 py-1">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${
                          r.diagnosis === 'load_shedding' ? 'bg-danger/10 text-danger' :
                          r.diagnosis === 'thermal_peaker' ? 'bg-warn/10 text-warn' :
                          r.diagnosis === 'transmission'   ? 'bg-accent/10 text-accent' :
                                                              'bg-muted/10 text-muted'
                        }`}>{r.diagnosis}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
