// Results → Energy flow (site commercial UX A1): where the site's energy came
// from, what it passed through and where it went — Source → Via → Destination,
// in MWh — with a period / date-range slider, a hover breakdown, a CSV export
// and the supply / demand series underneath. Computed server-side from the
// solved dispatch (services/results/energy_flows.py); a flow the server cannot
// prove to conserve energy is shown as unavailable, never as zero (ADR-0001).
import { useRef, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, Sankey, Tooltip, Layer, Rectangle,
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Legend,
} from 'recharts'
import toast from 'react-hot-toast'
import ResultState from '../../components/ResultState'
import { energyFlowsApi, type EnergyFlowsPayload } from '../../api/energyFlows'
import { useUIStore } from '../../store/uiStore'
import { showResultsTab } from '../../layout/flowNavigation'
import { nk } from '../../utils/queryKeys'
import { useResultsFilter } from './filterContext'
import WindowSlider, { useWindowRange } from './WindowSlider'
import {
  CHART_AXIS, CHART_GRID, CHART_TOOLTIP, downloadCSV, shortStamp, yAxisLabel,
  ChartFrame, ChartSourceProvider, useChartSource, type ChartSource, ChartActions, ChartZero, chartFigure, LABEL_PASS, CHART_LEGEND, CHART_HEIGHT, carrierColour, directLabel, bandLabelIndices, legendName, fmtPower,
} from './shared'
import {
  CSV_HEADER, csvRows, fmtMwh, periodLabel, sankeyData, share,
  type FlowNode, type FlowSankey,
} from './energyFlows'


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
        {legendName(payload.name)}
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
            margin={{ left: 160, right: 130, top: 16, bottom: 8 }}
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
  const peak = data.reduce((a, r) => Math.max(a, keys.reduce((b, k) => b + Math.max(Number(r[k as keyof typeof r]) || 0, 0), 0)), 0)
  // A band is named on itself (not hue alone); the legend alone when names could collide.
  const labelAt = bandLabelIndices(data, keys, peak)
  const bandLabel = (k: string) => {
    const i = labelAt.get(k)
    return i == null ? undefined : directLabel(legendName(k), i)
  }
  return (
    <ChartFrame testId={`ef-series-${title.toLowerCase()}`}
      title={`${title} by carrier`} figure={chartFigure(fmtPower(peak), 'peak')} hint="hourly average">
      <ResponsiveContainer width="100%" height={CHART_HEIGHT.md}>
        <AreaChart data={data} margin={{ top: 18, right: 40, left: 0, bottom: 0 }}>
          <CartesianGrid {...CHART_GRID} />
          <XAxis dataKey="t" {...CHART_AXIS} minTickGap={40} />
          <YAxis {...CHART_AXIS} label={yAxisLabel('MW (average)')} />
          <Tooltip {...CHART_TOOLTIP} />
          <Legend {...CHART_LEGEND} />
          {keys.map((k, i) => (
            <Area key={k} type="monotone" dataKey={k} stackId="s" stroke={carrierColour(k, i)}
                  fill={carrierColour(k, i)} fillOpacity={0.6} isAnimationActive={false} />
          ))}
          {keys.map(k => (
            <Area key={`label:${k}`} type="monotone" dataKey={k} stackId="labels" {...LABEL_PASS} label={bandLabel(k)} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

export default function EnergyFlowsTab({ sankeyWidth }: { sankeyWidth?: number }) {
  const currentProject = useUIStore(s => s.currentProject)
  const filter = useResultsFilter()
  const shellPeriod = typeof filter.selectedPeriod === 'number' ? filter.selectedPeriod
    : filter.selectedPeriod != null && filter.selectedPeriod !== 'all' && !Number.isNaN(Number(filter.selectedPeriod))
      ? Number(filter.selectedPeriod) : null
  const [localPeriod, setLocalPeriod] = useState<number | null>(null)
  const period = shellPeriod ?? localPeriod

  // The slider edits `drag`; the request follows it after a pause.
  const { drag, range, setDrag, clear, whole } = useWindowRange()

  const { data, isFetched } = useQuery({
    queryKey: nk(currentProject, 'results', 'energy_flows', period, range?.[0], range?.[1]),
    queryFn: () => energyFlowsApi.get({ period, from: range?.[0], to: range?.[1] }),
    placeholderData: keepPreviousData,
  })
  const chartRef = useRef<HTMLDivElement>(null)
  const source = useChartSource()

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
  const graph = sankeyData(data.rows)
  const total = data.rows.reduce((s, r) => s + r.mwh, 0)
  // With "all periods" chosen above, this tab still draws ONE period (its own
  // picker, the first by default): its charts' source line names that period
  // and drops the dates, which are the whole horizon's and not that period's.
  const frameSource: ChartSource = shellPeriod == null && data.period != null && source.line
    ? { ...source, line: source.line.replace(/(?: · [^·]*→[^·]*)? · all periods$/, ` · period ${data.period}`) }
    : source
  const lossShare = share(data.rows.filter(r => r.destination === 'Losses').reduce((s, r) => s + r.mwh, 0), total)
  const periods = data.periods.filter((p): p is number => p != null)
  const exportName = `energy_flows_${data.period ?? 'year'}`

  return (
    <div className="space-y-3 p-3" data-testid="ef-view">
      <div className="flex flex-wrap items-center gap-3 text-[11px]">
        {data.conservation && <ConservationChip c={data.conservation} />}
        {periods.length > 1 && shellPeriod == null && (
          <label>
            Period{' '}
            <select value={data.period ?? ''} onChange={e => { setLocalPeriod(Number(e.target.value)); clear() }}>
              {periods.map(p => <option key={p} value={p}>{periodLabel(p)}</option>)}
            </select>
          </label>
        )}
        <span className="text-muted">
          {lossShare != null ? `${(lossShare * 100).toFixed(1)} % losses` : ''}
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

      <WindowSlider testId="ef-window" snapshots={win.snapshots} from={win.from} to={win.to}
                    start={win.start} end={win.end} drag={drag} setDrag={setDrag} whole={whole} />

      <ChartSourceProvider value={frameSource}>
      <ChartFrame testId="ef-flow"
        title={`Energy flow · ${periodLabel(data.period)}`}
        figure={graph.links.length ? chartFigure(fmtMwh(total), 'delivered or lost') : undefined}
        hint="source → via → destination"
        right={
          <ChartActions chartRef={graph.links.length ? chartRef : undefined} filename={exportName}
            onExportCSV={() => {
              downloadCSV(`${exportName}.csv`, CSV_HEADER, csvRows(data.rows))
              toast.success('Exported')
            }} />
        }
        bodyClassName="p-3">
        {frameSource !== source && (
          <p className="mb-2 text-[11px] text-muted" data-testid="ef-one-period">
            All periods are selected above. The diagram draws one period: {periodLabel(data.period)}.
          </p>
        )}
        {graph.links.length ? (
          <div ref={chartRef} data-testid="ef-sankey"><FlowSankeyChart data={graph} width={sankeyWidth} /></div>
        ) : (
          <div data-testid="ef-empty"><ChartZero>No energy moved in this window.</ChartZero></div>
        )}
        <p className="mt-1 text-[11px] text-muted">
          “Via” is Direct, or the storage the energy was last held in;
          a storage hands back the mix it was charged with.{' '}
          Money flows:{' '}
          <button type="button" className="underline" data-testid="ef-to-value-flows"
                  onClick={() => showResultsTab('investment', { from: 'Energy flow' })}>
            Investment: value flows ›
          </button>
        </p>
      </ChartFrame>

      {data.series && (
        <div className="grid gap-3 md:grid-cols-2">
          <SeriesChart title="Supply" series={data.series.supply} index={data.series.index} />
          <SeriesChart title="Demand" series={data.series.demand} index={data.series.index} />
        </div>
      )}
      </ChartSourceProvider>
    </div>
  )
}
