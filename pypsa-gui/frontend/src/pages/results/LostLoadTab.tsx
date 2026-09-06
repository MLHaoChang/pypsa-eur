import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { type AdequacyReportPayload } from './adequacy'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import {
  type TSPayload, type WeightCtx, weightedSumSplit, aggregateTS,
  shortStamp, useWeightCtx,
  CHART_GRID, CHART_AXIS, CHART_TOOLTIP, yAxisLabel,
} from './shared'
import { resolveRange, useResultsFilter } from './filterContext'
import { useResultsWindow } from '../../hooks/useResultsWindow'
import { useFilterableTable, TableSearchBox, SortHeader } from './useFilterableTable'

// ── Lost-load result tab ────────────────────────────────────────────────────
// Unserved demand via the LP's VOLL slack generators. The backend already
// adds slack generators on EVERY bus (not just AC), so this tab can split
// lost-load by bus carrier (electrical / hydrogen / heat / …). All
// computation reuses /api/results/lost_load which now ships a `bus_carriers`
// map exposing each column's bus carrier.
//
// PHASE 7 IA SPLIT: the adequacy surfaces that used to ride here — the
// achieved-vs-target chips, the COPT screening chips, FrontierPanel and
// McPanel — moved to Results → Adequacy when the coupling loop landed and
// tipped the tab (the revisit condition recorded in McPanel.tsx). What stays
// is the lost-load EVIDENCE: what was shed, where, at what price. The ★ mount
// invariant those panels carried moved with them to AdequacyTab.test.tsx,
// where it is stronger (that tab has no early return at all) — it was not
// dropped, because "a reliable system is exactly where the adequacy surfaces
// must still render" is the lesson this tab learned the hard way.

// One line, both branches: a user who reaches this tab looking for the
// reliability readout must be told where it went. Without it the no-lost-load
// copy would still point "above" at chips that are now on another tab.
const ADEQUACY_CROSSLINK =
  'Adequacy studies — targets, screening, the frontier, Monte Carlo and the '
  + 'coupling loop — moved to the Adequacy tab.'

const ACCENT = '#dc2626'   // danger red — VOLL slack = LP failure to serve demand

const LOAD_ELECTRICAL_ALIASES = new Set(['', 'ac', 'electricity', 'electric', 'electrical', 'el'])
const LOAD_HYDROGEN_ALIASES   = new Set(['h2', 'hydrogen'])
const LOAD_HEAT_ALIASES       = new Set(['heat', 'thermal', 'urban heat', 'district heat'])
function carrierKey(raw: string | undefined | null): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (LOAD_ELECTRICAL_ALIASES.has(c)) return 'electrical'
  if (LOAD_HYDROGEN_ALIASES.has(c))   return 'hydrogen'
  if (LOAD_HEAT_ALIASES.has(c))       return 'heat'
  return c || 'unspecified'
}
function carrierLabel(k: string): string {
  if (k === 'electrical')  return 'Electrical'
  if (k === 'hydrogen')    return 'Hydrogen'
  if (k === 'heat')        return 'Heat'
  if (k === 'unspecified') return 'Unspecified'
  return k.charAt(0).toUpperCase() + k.slice(1)
}

export default function LostLoadTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const filter = useResultsFilter()
  // Positional bounds for the active Horizon filter, resolved against the
  // SNAPSHOT INDEX (not a results payload) — same hook Dispatch uses so the
  // lost-load query below can be windowed before any payload exists.
  const { win, winValid } = useResultsWindow(currentProject)
  const { data: lostLoad } = useQuery({
    queryKey: nk(currentProject, 'results', 'lost_load', win.from, win.to),
    queryFn: () => resultsApi.getLostLoad(win),
    enabled: winValid,
  })
  // The achieved-vs-target READOUT moved to the Adequacy tab, but this tab
  // still asks whether a target existed at all: "no unserved energy under a
  // stated standard" and "no unserved energy because nothing could ever be
  // shed" are different answers to an empty table, and only the second one
  // means "set a VoLL". 204 → null → the untargeted copy. Horizon-scope, so
  // no window params; same query key the Adequacy tab uses, so this is a
  // cache read rather than a second round-trip.
  const { data: adequacy } = useQuery({
    queryKey: nk(currentProject, 'results', 'adequacy'),
    queryFn: () => resultsApi.getAdequacy(),
  })
  // WeightCtx + timeline from the shared hook (fetches /snapshots +
  // /investmentPeriods); refTs = the lost-load series.
  const { weightCtx, refIndex, refPeriods } = useWeightCtx(lostLoad as TSPayload | null)
  const range = resolveRange(refIndex, filter, refPeriods)

  const llMeta = lostLoad as unknown as {
    total_mwh?: number
    total_cost_eur?: number
    voll_eur_per_mwh?: number
    bus_carriers?: Record<string, string>
  } | null

  // Per-carrier breakdown — groups bus columns by their canonical carrier
  // and sums VOLL dispatch weighted by snapshot×period.
  const byCarrier = useMemo(() => {
    const ts = lostLoad as TSPayload | null
    if (!ts || ts.data.length === 0) return null
    const busCarriers = llMeta?.bus_carriers ?? {}
    const groups = new Map<string, Set<string>>()
    for (const col of ts.columns) {
      const k = carrierKey(busCarriers[col] ?? '')
      const bucket = groups.get(k) ?? new Set<string>()
      bucket.add(col)
      groups.set(k, bucket)
    }
    const voll = llMeta?.voll_eur_per_mwh ?? 0
    const out: Array<{ carrier: string; mwh: number; cost: number }> = []
    for (const [c, cols] of groups) {
      const split = weightedSumSplit(ts, cols, weightCtx, range, 'generators')
      const mwh = split.positive ?? 0
      if (mwh > 0) out.push({ carrier: c, mwh, cost: mwh * voll })
    }
    out.sort((a, b) => b.mwh - a.mwh)
    return out
  }, [lostLoad, llMeta, weightCtx, range])

  // Top-N affected buses across all carriers — same `bus_carriers` keys are
  // used as the column index in the payload.
  const byBus = useMemo(() => {
    const ts = lostLoad as TSPayload | null
    if (!ts || ts.data.length === 0) return null
    const voll = llMeta?.voll_eur_per_mwh ?? 0
    const busCarriers = llMeta?.bus_carriers ?? {}
    const rows: Array<{ bus: string; carrier: string; mwh: number; cost: number }> = []
    for (const col of ts.columns) {
      const split = weightedSumSplit(ts, new Set([col]), weightCtx, range, 'generators')
      const mwh = split.positive ?? 0
      if (mwh <= 0) continue
      rows.push({
        bus: col,
        carrier: carrierLabel(carrierKey(busCarriers[col] ?? '')),
        mwh,
        cost: mwh * voll,
      })
    }
    rows.sort((a, b) => b.mwh - a.mwh)
    return rows.slice(0, 20)
  }, [lostLoad, llMeta, weightCtx, range])

  // Aggregated time series — sum across every bus.
  const agg = useMemo(() => {
    const ts = lostLoad as TSPayload | null
    if (!ts) return null
    return aggregateTS(ts, new Set(ts.columns), range)
  }, [lostLoad, range])

  const totals = useMemo(() => {
    const ts = lostLoad as TSPayload | null
    if (!ts) return null
    const split = weightedSumSplit(ts, new Set(ts.columns), weightCtx, range, 'generators')
    const mwh = split.positive ?? 0
    const voll = llMeta?.voll_eur_per_mwh ?? 0
    return { mwh, cost: mwh * voll, voll }
  }, [lostLoad, llMeta, weightCtx, range])

  // No lost load is not the same as nothing to report. This early return used
  // to conflate them AND to fire before the adequacy surfaces, so the success
  // case — a reliability target set and MET, with zero unserved energy —
  // rendered as an empty tab telling the user to set a VoLL they had already
  // set. The surfaces now live on the Adequacy tab (they render there
  // unconditionally, which is the same lesson enforced more strongly), so what
  // this branch owes the user is an honest statement of WHICH empty case this
  // is, plus the cross-link.
  //
  // The `targeted` branch's old copy said "the reliability target ABOVE
  // reports what actually bound" — there is no longer anything above, so it
  // points at the tab that now carries it.
  if (!lostLoad || !totals || totals.mwh <= 0) {
    const targeted = (adequacy as AdequacyReportPayload | null | undefined)?.target != null
    return (
      <div className="flex flex-col h-full overflow-auto p-4 gap-4">
        <header>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Lost load</h3>
        </header>
        <p className="text-[11px] text-muted" data-testid="lostload-adequacy-crosslink">
          {ADEQUACY_CROSSLINK}
        </p>
        <div className="text-[12px] text-muted space-y-2" data-testid="lostload-empty-copy">
          {targeted ? (
            <p>
              No unserved energy in this solve — the plan served all demand, so there is no
              lost-load breakdown to chart. A reliability target WAS applied: what it bound,
              and how the plan scores under the screening and sampling engines, is on the
              Adequacy tab.
            </p>
          ) : (
            <>
              <p>No lost-load data available.</p>
              <p>
                The LP only writes lost-load when run with <code className="font-mono">VOLL &gt; 0</code> in
                solver settings AND the optimisation can't meet demand at any price ≤ VOLL. Set a VOLL
                (e.g. 3000–10000 €/MWh) and re-run to populate this tab.
              </p>
            </>
          )}
        </div>
      </div>
    )
  }

  const periodLabel = filter.selectedPeriod != null ? `period ${filter.selectedPeriod}` : 'horizon'

  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-4">
      <header>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          Lost load
          <span className="ml-2 text-[10px] font-normal text-muted">{periodLabel}</span>
        </h3>
        <p className="text-[11px] text-muted mt-1">
          Demand the LP couldn't meet across all energy carriers — absorbed by VOLL slack
          generators on every bus.
        </p>
      </header>

      <p className="text-[11px] text-muted" data-testid="lostload-adequacy-crosslink">
        {ADEQUACY_CROSSLINK}
      </p>

      <div className="grid grid-cols-3 gap-3">
        <Kpi label="Total lost load" value={fmtEnergy(totals.mwh)}
             hint="Σ slack-generator dispatch × weights across every carrier" />
        <Kpi label="Lost-load cost" value={fmtCurrency(totals.cost)}
             hint={`MWh × VOLL (${totals.voll.toFixed(0)} €/MWh)`} />
        <Kpi label="VOLL price" value={`${totals.voll.toFixed(0)} €/MWh`}
             hint="The Value-Of-Lost-Load price the LP paid for unserved demand" />
      </div>

      <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <header className="px-4 py-2.5 border-b border-border bg-bg-2">
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">By carrier</span>
        </header>
        <div className="p-3">
          {byCarrier && byCarrier.length > 0 ? (
            <table className="w-full text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="text-left  py-1 font-medium">Carrier</th>
                  <th className="text-right py-1 font-medium">Lost (MWh)</th>
                  <th className="text-right py-1 font-medium">Cost (€)</th>
                </tr>
              </thead>
              <tbody>
                {byCarrier.map(b => (
                  <tr key={b.carrier} className="border-b border-border/40">
                    <td className="py-1 text-text font-mono">{carrierLabel(b.carrier)}</td>
                    <td className="py-1 text-right font-mono text-text">{fmtEnergy(b.mwh)}</td>
                    <td className="py-1 text-right font-mono text-muted">{fmtCurrency(b.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="text-[11px] text-muted">No carrier breakdown available.</p>}
        </div>
      </section>

      <PerBusLostLoadSection rows={byBus ?? null} />


      <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <header className="px-4 py-2.5 border-b border-border bg-bg-2">
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
            Lost load over time
          </span>
          <span className="ml-2 text-[10px] text-muted">aggregated MW · {periodLabel}</span>
        </header>
        <div className="p-3">
          {agg && agg.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={agg} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="llGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={ACCENT} stopOpacity={0.4} />
                    <stop offset="95%" stopColor={ACCENT} stopOpacity={0.05} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} label={yAxisLabel('Lost load (MW)')} />
                <Tooltip {...CHART_TOOLTIP}
                  labelFormatter={shortStamp}
                  formatter={(v: number) => [`${v.toFixed(1)} MW`, 'unserved']} />
                <Area type="monotone" dataKey="value"
                  stroke={ACCENT} fill="url(#llGrad)" strokeWidth={1.6} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : <p className="text-[11px] text-muted">No time series for the selected range.</p>}
        </div>
      </section>
    </div>
  )
}

// ── Per-bus section with sort + search ─────────────────────────────────────
// Extracted into its own component so the table-level hook state (sort key,
// search query) doesn't share scope with the parent — keeps re-renders local
// to this section.
function PerBusLostLoadSection({
  rows,
}: { rows: Array<{ bus: string; carrier: string; mwh: number; cost: number }> | null }) {
  type K = 'bus' | 'carrier' | 'mwh' | 'cost'
  const ft = useFilterableTable<{ bus: string; carrier: string; mwh: number; cost: number }, K>({
    rows: rows ?? [],
    getSearchText: r => [r.bus, r.carrier].join(' ').toLowerCase(),
    getValue: (r, k) => (k === 'bus' || k === 'carrier') ? r[k] : r[k],
  })
  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
      <header className="px-4 py-2.5 border-b border-border bg-bg-2 flex items-center gap-2 flex-wrap">
        <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          Per-bus lost load
        </span>
        <span className="text-[10px] text-muted">top 20, sortable</span>
        <span className="flex-1" />
        <TableSearchBox value={ft.search} onChange={ft.setSearch} placeholder="Filter…" />
      </header>
      <div className="p-3">
        {rows && rows.length > 0 ? (
          <table className="w-full text-[11.5px]">
            <thead>
              <tr className="border-b border-border text-muted">
                <SortHeader columnKey="bus"     label="Bus"        sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('bus')} />
                <SortHeader columnKey="carrier" label="Carrier"    sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('carrier')} align="right" />
                <SortHeader columnKey="mwh"     label="Lost (MWh)" sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('mwh')}     align="right" />
                <SortHeader columnKey="cost"    label="Cost (€)"   sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('cost')}    align="right" />
              </tr>
            </thead>
            <tbody>
              {ft.rows.map(r => (
                <tr key={r.bus} className="border-b border-border/40">
                  <td className="py-1 text-text font-mono">{r.bus}</td>
                  <td className="py-1 text-right font-mono text-muted">{r.carrier}</td>
                  <td className="py-1 text-right font-mono text-text">{fmtEnergy(r.mwh)}</td>
                  <td className="py-1 text-right font-mono text-muted">{fmtCurrency(r.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="text-[11px] text-muted">No per-bus breakdown available.</p>}
      </div>
    </section>
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

function fmtCurrency(eur: number): string {
  if (!Number.isFinite(eur)) return '—'
  const a = Math.abs(eur)
  if (a >= 1e9) return `€${(eur / 1e9).toFixed(2)} B`
  if (a >= 1e6) return `€${(eur / 1e6).toFixed(2)} M`
  if (a >= 1e3) return `€${(eur / 1e3).toFixed(1)} k`
  return `€${eur.toFixed(0)}`
}
