import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import type { Generator } from '../../api/types'
import {
  type TSPayload, type WeightCtx, weightedSum, aggregateTS,
  isRenewableCarrier, shortStamp,
  CHART_GRID, CHART_AXIS, CHART_TOOLTIP, yAxisLabel,
} from './shared'
import { resolveRange, useResultsFilter } from './filterContext'

// ── Curtailment result tab ──────────────────────────────────────────────────
// Standalone view of renewable-curtailment data the LP rejected (cost-
// feasible energy that wasn't dispatched, typically because the bus was
// transmission-bound or storage was full). Pulls the same /results/curtailment
// time series the Dispatch tab uses as an overlay, but here we surface it
// as a dedicated panel with per-carrier breakdown + time-series chart.

const ACCENT = '#d97706'    // amber — matches the curtailment toggle in Dispatch

export default function Curtailment() {
  const filter = useResultsFilter()
  const { data: curtailTS } = useQuery({ queryKey: ['results', 'curtailment'], queryFn: resultsApi.getCurtailment })
  const { data: generators = [] } = useQuery({ queryKey: ['generators'], queryFn: networkApi.getGenerators })
  const { data: snap } = useQuery({ queryKey: ['snapshots'], queryFn: networkApi.getSnapshots })
  const { data: inv  } = useQuery({ queryKey: ['investmentPeriods'], queryFn: networkApi.getInvestmentPeriods })

  // WeightCtx assembled from /snapshots + /investmentPeriods — same recipe as
  // AggregatedResultsBody in Results.tsx so the per-period × snapshot-weight
  // scaling matches the Dispatch tab's curtailment KPI.
  const weightCtx: WeightCtx = useMemo(() => ({
    snapshots: snap?.snapshots ?? (curtailTS as TSPayload | null)?.index,
    snapshotPeriods: (curtailTS as TSPayload | null)?.periods ?? snap?.periods,
    snapshotWeights: (snap?.weightings as unknown) as WeightCtx['snapshotWeights'],
    periodWeights: inv?.weightings as unknown as WeightCtx['periodWeights'],
  }), [snap, inv, curtailTS])

  const refIndex: string[] = (curtailTS as TSPayload | null)?.index ?? snap?.snapshots ?? []
  const refPeriods = (curtailTS as TSPayload | null)?.periods ?? snap?.periods
  const range = resolveRange(refIndex, filter, refPeriods)

  // Per-carrier curtailment energy: each renewable generator's curtailment
  // column is summed by carrier so multi-carrier networks (e.g. solar + wind)
  // get separate rows.
  const carrierEnergy = useMemo(() => {
    const ts = curtailTS as TSPayload | null
    if (!ts) return null
    const byCarrier = new Map<string, number>()
    const carrierByName = new Map<string, string>()
    for (const g of generators as Generator[]) carrierByName.set(g.name, (g.carrier ?? '').toLowerCase() || 'unspecified')
    for (const col of ts.columns) {
      if (!carrierByName.has(col)) continue
      const carrier = carrierByName.get(col)!
      const single = new Set([col])
      const e = weightedSum(ts, single, weightCtx, range, 'generators') ?? 0
      byCarrier.set(carrier, (byCarrier.get(carrier) ?? 0) + e)
    }
    return Array.from(byCarrier.entries())
      .filter(([, v]) => Math.abs(v) > 0)
      .sort(([, a], [, b]) => b - a)
  }, [curtailTS, generators, weightCtx, range])

  // Aggregated time series (sum across all renewable columns).
  const renewableNames = useMemo(() => {
    const out = new Set<string>()
    for (const g of generators as Generator[]) {
      if (isRenewableCarrier(g.carrier ?? '')) out.add(g.name)
    }
    return out
  }, [generators])
  const aggSeries = useMemo(() => aggregateTS(
    curtailTS as TSPayload | null, renewableNames, range,
  ), [curtailTS, renewableNames, range])

  // Totals: curtailment energy + percent vs (curtailed + delivered) using
  // the renewable generators' supply for the denominator. Comes from the
  // Dispatch tab's same formula so the two views stay reconciled.
  const { data: gensTS } = useQuery({ queryKey: ['results', 'generators'], queryFn: () => resultsApi.getGeneratorResults() })
  const totals = useMemo(() => {
    const curt = weightedSum(curtailTS as TSPayload | null, renewableNames, weightCtx, range, 'generators') ?? 0
    const delivered = weightedSum(gensTS as TSPayload | null, renewableNames, weightCtx, range, 'generators') ?? 0
    const potential = curt + delivered
    const pct = potential > 0 ? (curt / potential) * 100 : 0
    return { curt, delivered, pct }
  }, [curtailTS, gensTS, renewableNames, weightCtx, range])

  if (!curtailTS) {
    return (
      <div className="p-6 text-[12px] text-muted">
        No curtailment data available yet. Run a LOPF with renewable generators that have a
        time-varying <code className="font-mono">p_max_pu</code> profile to populate
        <code className="font-mono"> /api/results/curtailment</code>.
      </div>
    )
  }

  const periodLabel = filter.selectedPeriod != null ? `period ${filter.selectedPeriod}` : 'horizon'

  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-4">
      <header>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          Renewable curtailment
          <span className="ml-2 text-[10px] font-normal text-muted">{periodLabel}</span>
        </h3>
        <p className="text-[11px] text-muted mt-1">
          Σ max(p_max_pu × p_nom_opt − p, 0) — energy the LP rejected over the renewable
          generation envelope.
        </p>
      </header>

      {/* KPI strip */}
      <div className="grid grid-cols-3 gap-3">
        <Kpi label="Curtailed energy" value={fmtEnergy(totals.curt)}
             hint="Σ curtailed MW × snapshot_weighting × period_years over renewable generators." />
        <Kpi label="Delivered renewable" value={fmtEnergy(totals.delivered)}
             hint="Σ renewable dispatch over the same range, for context." />
        <Kpi label="Curtailment rate" value={`${totals.pct.toFixed(2)} %`}
             hint="curtailed ÷ (curtailed + delivered)." />
      </div>

      {/* Per-carrier table */}
      <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <header className="px-4 py-2.5 border-b border-border bg-bg-2">
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">By carrier</span>
        </header>
        <div className="p-3">
          {carrierEnergy && carrierEnergy.length > 0 ? (
            <table className="w-full text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="text-left  py-1 font-medium">Carrier</th>
                  <th className="text-right py-1 font-medium">Energy</th>
                  <th className="text-right py-1 font-medium">Share</th>
                </tr>
              </thead>
              <tbody>
                {carrierEnergy.map(([c, e]) => (
                  <tr key={c} className="border-b border-border/40">
                    <td className="py-1 text-text font-mono">{c}</td>
                    <td className="py-1 text-right font-mono text-text">{fmtEnergy(e)}</td>
                    <td className="py-1 text-right font-mono text-muted">
                      {totals.curt > 0 ? `${((e / totals.curt) * 100).toFixed(1)} %` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-[11px] text-muted py-2">
              No per-carrier curtailment in the current range — the LP dispatched every available MW.
            </p>
          )}
        </div>
      </section>

      {/* Time series — aggregated curtailment over renewables */}
      <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <header className="px-4 py-2.5 border-b border-border bg-bg-2">
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
            Curtailment over time
          </span>
          <span className="ml-2 text-[10px] text-muted">aggregated MW · {periodLabel}</span>
        </header>
        <div className="p-3">
          {aggSeries && aggSeries.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={aggSeries} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="curtGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={ACCENT} stopOpacity={0.4} />
                    <stop offset="95%" stopColor={ACCENT} stopOpacity={0.05} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} label={yAxisLabel('Curtailment (MW)')} />
                <Tooltip {...CHART_TOOLTIP}
                  labelFormatter={shortStamp}
                  formatter={(v: number) => [`${v.toFixed(1)} MW`, 'curtailed']} />
                <Area type="monotone" dataKey="value"
                  stroke={ACCENT} fill="url(#curtGrad)" strokeWidth={1.6} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-[11px] text-muted py-2">No curtailment time series for the selected range.</p>
          )}
        </div>
      </section>
    </div>
  )
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-[8px] border border-border bg-bg p-3" title={hint}>
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div className="text-[20px] font-mono tabular-nums text-text mt-0.5">{value}</div>
    </div>
  )
}

function fmtEnergy(mwh: number): string {
  if (!Number.isFinite(mwh)) return '—'
  const a = Math.abs(mwh)
  if (a >= 1e6) return `${(mwh / 1e6).toFixed(2)} TWh`
  if (a >= 1e3) return `${(mwh / 1e3).toFixed(2)} GWh`
  return `${mwh.toFixed(1)} MWh`
}
