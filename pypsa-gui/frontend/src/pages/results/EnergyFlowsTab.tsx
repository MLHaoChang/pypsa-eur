// Results → Energy flow (site commercial UX A1): where the site's energy came
// from, what it passed through and where it went — Source → Via → Destination,
// in MWh — with a period / date-range slider, a hover breakdown, a CSV export
// and the supply / demand series underneath. Computed server-side from the
// solved dispatch (services/results/energy_flows.py); a flow the server cannot
// prove to conserve energy is shown as unavailable, never as zero (ADR-0001).
import { useEffect, useRef, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, Sankey, Tooltip, Layer, Rectangle,
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
} from 'recharts'
import ResultState from '../../components/ResultState'
import { energyFlowsApi, type EnergyFlowsPayload } from '../../api/energyFlows'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { useResultsFilter } from './filterContext'
import {
  CHART_AXIS, CHART_GRID, CHART_TOOLTIP, downloadCSV, downloadSVG, shortStamp, yAxisLabel,
} from './shared'
import {
  CSV_HEADER, csvRows, fmtMwh, periodLabel, sankeyData, share,
  type FlowNode, type FlowSankey,
} from './energyFlows'

const STACK_COLORS = ['#f59e0b', '#0ea5e9', '#6b7280', '#16a34a', '#9333ea', '#db2777', '#0d9488', '#a16207']

function FlowNodeShape(props: {
  x: number; y: number; width: number; height: number; payload: FlowNode
}) {
  const { x, y, width, height, payload } = props
  const anchor = payload.column === 'source' ? 'end' : 'start'
  const tx = payload.column === 'source' ? x - 4 : payload.column === 'via' ? x + width / 2 : x + width + 4
  return (
    <Layer>
      <Rectangle x={x} y={y} width={width} height={height} fill={payload.color} fillOpacity={0.92} />
      <text x={tx} y={payload.column === 'via' ? y - 4 : y + height / 2}
            dy={payload.column === 'via' ? 0 : '0.35em'}
            textAnchor={payload.column === 'via' ? 'middle' : anchor}
            fontSize={10} fill="currentColor">
        {payload.name}
      </text>
    </Layer>
  )
}

function FlowLinkShape(props: {
  sourceX: number; targetX: number; sourceY: number; targetY: number
  sourceControlX: number; targetControlX: number; linkWidth: number
  payload: { color: string }
}) {
  const { sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload } = props
  return (
    <path
      d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`}
      fill="none" stroke={payload.color} strokeWidth={Math.max(1, linkWidth)} strokeOpacity={0.4} />
  )
}

type TipLink = {
  value?: number; rows?: Array<{ source: string; via: string; destination: string; mwh: number }>
}

function FlowTip({ active, payload }: {
  active?: boolean
  payload?: Array<{ payload?: { payload?: TipLink } }>
}) {
  const link = payload?.[0]?.payload?.payload
  if (!active || !link?.rows) return null
  const shown = link.rows.slice(0, 6)
  return (
    <div className="rounded border border-border bg-panel px-2 py-1 text-[11px]" data-testid="ef-tip">
      <table>
        <thead>
          <tr className="text-muted"><th className="text-left pr-2">Source</th><th className="text-left pr-2">Via</th>
            <th className="text-left pr-2">Destination</th><th className="text-right">MWh</th></tr>
        </thead>
        <tbody>
          {shown.map(r => (
            <tr key={`${r.source}|${r.via}|${r.destination}`}>
              <td className="pr-2">{r.source}</td><td className="pr-2">{r.via}</td>
              <td className="pr-2">{r.destination}</td>
              <td className="text-right">{r.mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {link.rows.length > shown.length && (
        <div className="text-muted">+{link.rows.length - shown.length} more</div>
      )}
    </div>
  )
}

export function FlowSankeyChart({ data, width }: {
  data: FlowSankey
  /** A fixed width (tests: ResponsiveContainer renders 0 in jsdom). */
  width?: number
}) {
  const perColumn = (c: FlowNode['column']) => data.nodes.filter(n => n.column === c).length
  const height = Math.max(240, 30 * Math.max(perColumn('source'), perColumn('via'), perColumn('destination')))
  const chart = (w?: number) => (
    <Sankey width={w} height={height} data={data} nodePadding={16} nodeWidth={10}
            margin={{ left: 110, right: 130, top: 16, bottom: 8 }}
            node={FlowNodeShape as never} link={FlowLinkShape as never}>
      <Tooltip content={<FlowTip />} />
    </Sankey>
  )
  return width ? chart(width) : (
    <ResponsiveContainer width="100%" height={height}>{chart()}</ResponsiveContainer>
  )
}

function ConservationChip({ c }: { c: NonNullable<EnergyFlowsPayload['conservation']> }) {
  const ok = c.status === 'ok'
  return (
    <span role="status" data-testid="ef-conservation" data-status={c.status}
          title={`generation ${fmtMwh(c.generation_mwh)} = destinations ${fmtMwh(c.destinations_mwh)}`
            + ` + stored ${fmtMwh(c.storage_delta_mwh)}; residual ${c.residual_mwh.toExponential(1)} MWh`}
          className={`inline-block rounded border px-2 py-0.5 text-[11px] ${
            ok ? 'text-success border-success/40' : 'text-danger border-danger/40'}`}>
      {ok ? 'Energy conserved' : `Conservation failed: ${c.checks.join(', ')}`}
    </span>
  )
}

function SeriesChart({ title, series, index }: {
  title: string; series: Record<string, number[]>; index: string[]
}) {
  const keys = Object.keys(series)
  if (!keys.length || !index.length) return null
  const data = index.map((t, i) => ({ t: shortStamp(t), ...Object.fromEntries(keys.map(k => [k, series[k][i]])) }))
  return (
    <div className="space-y-1" data-testid={`ef-series-${title.toLowerCase()}`}>
      <h3 className="text-[11px] font-semibold">{title}</h3>
      <ResponsiveContainer width="100%" height={150}>
        <AreaChart data={data} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid {...CHART_GRID} />
          <XAxis dataKey="t" {...CHART_AXIS} minTickGap={40} />
          <YAxis {...CHART_AXIS} label={yAxisLabel('MW (average)')} />
          <Tooltip {...CHART_TOOLTIP} />
          {keys.map((k, i) => (
            <Area key={k} type="monotone" dataKey={k} stackId="s" stroke={STACK_COLORS[i % STACK_COLORS.length]}
                  fill={STACK_COLORS[i % STACK_COLORS.length]} fillOpacity={0.6} isAnimationActive={false} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function EnergyFlowsTab({ sankeyWidth }: { sankeyWidth?: number }) {
  const currentProject = useUIStore(s => s.currentProject)
  const requestResultsTab = useUIStore(s => s.requestResultsTab)
  const filter = useResultsFilter()
  const shellPeriod = typeof filter.selectedPeriod === 'number' ? filter.selectedPeriod
    : filter.selectedPeriod != null && filter.selectedPeriod !== 'all' && !Number.isNaN(Number(filter.selectedPeriod))
      ? Number(filter.selectedPeriod) : null
  const [localPeriod, setLocalPeriod] = useState<number | null>(null)
  const period = shellPeriod ?? localPeriod

  // The slider edits `drag`; the request follows it after a pause.
  const [drag, setDrag] = useState<[number, number] | null>(null)
  const [range, setRange] = useState<[number, number] | null>(null)
  useEffect(() => {
    if (drag === null) return
    const id = setTimeout(() => setRange(drag), 250)
    return () => clearTimeout(id)
  }, [drag])

  const { data, isFetched } = useQuery({
    queryKey: nk(currentProject, 'results', 'energy_flows', period, range?.[0], range?.[1]),
    queryFn: () => energyFlowsApi.get({ period, from: range?.[0], to: range?.[1] }),
    placeholderData: keepPreviousData,
  })
  const chartRef = useRef<HTMLDivElement>(null)

  if (!data) {
    if (!isFetched) return null
    return (
      <div className="p-6">
        <ResultState state="not_run"
          reason="Energy flows are read from the solved dispatch. Run a solve to see where the energy went." />
      </div>
    )
  }
  if (data.status !== 'ok') {
    return (
      <div className="p-6 space-y-2" data-testid="ef-unavailable">
        <ResultState state="unavailable"
          reason={data.reason ?? 'The flows could not be shown to conserve energy for this run.'}>
          {data.flags.length > 0 && (
            <ul className="text-[11px] text-muted pl-3 list-disc">
              {data.flags.slice(0, 8).map(f => <li key={f}>{f}</li>)}
            </ul>
          )}
        </ResultState>
      </div>
    )
  }

  const win = data.window!
  const lo = drag?.[0] ?? win.from
  const hi = drag?.[1] ?? win.to
  const graph = sankeyData(data.rows)
  const total = data.rows.reduce((s, r) => s + r.mwh, 0)
  const lossShare = share(data.rows.filter(r => r.destination === 'Losses').reduce((s, r) => s + r.mwh, 0), total)
  const periods = data.periods.filter((p): p is number => p != null)

  return (
    <div className="space-y-3 p-3" data-testid="ef-view">
      <div className="flex flex-wrap items-center gap-3 text-[11px]">
        {data.conservation && <ConservationChip c={data.conservation} />}
        {periods.length > 1 && shellPeriod == null && (
          <label>
            Period{' '}
            <select value={data.period ?? ''} onChange={e => { setLocalPeriod(Number(e.target.value)); setDrag(null); setRange(null) }}>
              {periods.map(p => <option key={p} value={p}>{periodLabel(p)}</option>)}
            </select>
          </label>
        )}
        <span className="text-muted">
          {fmtMwh(total)} delivered or lost{lossShare != null ? ` · ${(lossShare * 100).toFixed(1)} % losses` : ''}
        </span>
        {Object.entries(data.unserved_product_mwh ?? {}).filter(([, v]) => v > 0).map(([load, v]) => (
          <span key={load} className="text-danger" data-testid={`ef-undelivered-${load}`}>
            Not delivered to {load}: {fmtMwh(v)}
          </span>
        ))}
        {data.unserved_mwh != null && data.unserved_mwh > 0 && (
          <span className="text-danger" data-testid="ef-unserved">Unserved: {fmtMwh(data.unserved_mwh)}</span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px]" data-testid="ef-window">
        <span className="text-muted">Window</span>
        <input aria-label="Window start" type="range" min={0} max={win.snapshots - 1} value={lo}
               onChange={e => setDrag([Math.min(Number(e.target.value), hi), hi])} />
        <input aria-label="Window end" type="range" min={0} max={win.snapshots - 1} value={hi}
               onChange={e => setDrag([lo, Math.max(Number(e.target.value), lo)])} />
        <span data-testid="ef-window-label">
          {win.start && win.end ? `${shortStamp(win.start)} → ${shortStamp(win.end)}` : `${lo} → ${hi}`}
          {' '}({hi - lo + 1} of {win.snapshots})
        </span>
        {(lo !== 0 || hi !== win.snapshots - 1) && (
          <button type="button" className="underline" onClick={() => { setDrag([0, win.snapshots - 1]); setRange(null) }}>
            Whole period
          </button>
        )}
      </div>

      <div className="space-y-1">
        <div className="flex justify-between">
          <h3 className="text-[11px] font-semibold">Energy flow, {periodLabel(data.period)} (MWh)</h3>
          <span className="flex gap-3 text-[11px]">
            <button type="button" className="underline"
                    onClick={() => downloadCSV(`energy_flows_${data.period ?? 'year'}.csv`, CSV_HEADER, csvRows(data.rows))}>
              Export CSV
            </button>
            <button type="button" className="underline"
                    onClick={() => downloadSVG(chartRef.current, `energy_flows_${data.period ?? 'year'}.svg`)}>
              Export SVG
            </button>
          </span>
        </div>
        {graph.links.length ? (
          <div ref={chartRef} data-testid="ef-sankey"><FlowSankeyChart data={graph} width={sankeyWidth} /></div>
        ) : (
          <p className="text-[11px] text-muted py-2" data-testid="ef-empty">
            No energy moved in this window.</p>
        )}
        <p className="text-[11px] text-muted">
          Source → Via → Destination. “Via” is Direct, or the storage the energy was last held in;
          a storage hands back the mix it was charged with.{' '}
          Money flows:{' '}
          <button type="button" className="underline" data-testid="ef-to-value-flows"
                  onClick={() => requestResultsTab('investment')}>
            Investment → Value flows
          </button>
        </p>
      </div>

      {data.series && (
        <div className="grid gap-3 md:grid-cols-2">
          <SeriesChart title="Supply" series={data.series.supply} index={data.series.index} />
          <SeriesChart title="Demand" series={data.series.demand} index={data.series.index} />
        </div>
      )}
    </div>
  )
}
