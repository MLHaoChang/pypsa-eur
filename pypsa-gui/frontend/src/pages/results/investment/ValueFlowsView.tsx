// Results → Investment → Participants (IC P3 WP3.6): who pays whom. The
// conservation chip, the per-participant table (paid / received / net and the
// net per stream, CSV export) and a Sankey of the ledger for one period. Money
// is per period-year, unweighted, in the tariff's currency. A total the server
// does not establish is shown as unavailable — never as a zero (ADR-0001).
import { useRef, useState } from 'react'
import { useUIStore } from '../../../store/uiStore'
import { ResponsiveContainer, Sankey, Tooltip, Layer, Rectangle } from 'recharts'
import type { ValueFlowsPayload } from '../../../api/commercial'
import { useResultsFilter } from '../filterContext'
import { displayName } from '../../../utils/displayName'
import { downloadCSV, ChartFrame, ChartActions, ChartZero, UnavailableBlock, UnavailableCell } from '../shared'
import {
  conservationState, csvRows, CSV_HEADER, fmtAmount, participantRows, periodKeys, periodLabel,
  sankeyData, streamLabel, type ConservationState, type SankeyNodeDatum,
} from './valueFlows'

// Theme tokens, so the diagram follows light and dark (ui-modern 02 slice 6).
const INTERNAL = 'var(--color-info)'
const EXTERNAL = 'var(--color-axis)'

function Money({ v }: { v: number | null | undefined }) {
  return v == null ? <UnavailableCell /> : <>{fmtAmount(v)}</>
}

export function ConservationChip({ state }: { state: ConservationState }) {
  const text = state.status === 'ok' ? 'Conservation: ok'
    : state.status === 'failed' ? `Conservation failed: ${state.checks.join(', ') || 'a check'}`
      : `Conservation not established${state.checks.length ? ` (${state.checks.join(', ')})` : ''}`
  const tone = state.status === 'ok' ? 'text-success border-success/40'
    : state.status === 'failed' ? 'text-danger border-danger/40' : 'text-muted border-border'
  return (
    <span role="status" data-testid="vf-conservation" data-status={state.status}
          className={`inline-block rounded border px-2 py-0.5 text-[11px] ${tone}`}>
      {text}
    </span>
  )
}

function SankeyNode(props: {
  x: number; y: number; width: number; height: number; payload: SankeyNodeDatum
}) {
  const { x, y, width, height, payload } = props
  const left = payload.side === 'payer'
  return (
    <Layer>
      <Rectangle x={x} y={y} width={width} height={height}
                 fill={payload.internal ? INTERNAL : EXTERNAL} fillOpacity={0.9} />
      <text x={left ? x - 4 : x + width + 4} y={y + height / 2} dy="0.35em"
            textAnchor={left ? 'end' : 'start'} fontSize={10} fill="currentColor">
        {displayName(payload.name)}
      </text>
    </Layer>
  )
}

function SankeyTip({ active, payload }: {
  active?: boolean
  payload?: Array<{ payload?: { payload?: { stream?: string; value?: number;
                                           source?: SankeyNodeDatum; target?: SankeyNodeDatum } } }>
}) {
  const d = payload?.[0]?.payload?.payload
  if (!active || !d || d.stream === undefined) return null
  return (
    <div className="rounded border border-border bg-panel px-2 py-1 text-[11px]">
      <div>{displayName(d.source?.name ?? '')} → {displayName(d.target?.name ?? '')}</div>
      <div>{streamLabel(d.stream)}: {fmtAmount(d.value)}</div>
    </div>
  )
}

export function ValueFlowSankey({ period, width }: {
  period: NonNullable<ValueFlowsPayload['periods']>[string]
  /** A fixed width (tests: ResponsiveContainer renders 0 in jsdom). */
  width?: number
}) {
  const data = sankeyData(period.sankey)
  if (!data.links.length) {
    return <ChartZero>No money flows in this period.</ChartZero>
  }
  const height = Math.max(160, 28 * Math.max(
    data.nodes.filter(n => n.side === 'payer').length,
    data.nodes.filter(n => n.side === 'payee').length))
  const chart = (w?: number) => (
    <Sankey width={w} height={height} data={data} nodePadding={14} nodeWidth={10}
            margin={{ left: 120, right: 120, top: 8, bottom: 8 }}
            node={SankeyNode as never} link={{ stroke: 'var(--color-axis)', strokeOpacity: 0.35 }}>
      <Tooltip content={<SankeyTip />} />
    </Sankey>
  )
  return width ? chart(width) : (
    <ResponsiveContainer width="100%" height={height}>{chart()}</ResponsiveContainer>
  )
}

export function ParticipantTable({ payload, periodKey }: {
  payload: ValueFlowsPayload; periodKey: string
}) {
  const period = payload.periods?.[periodKey]
  if (!period) return <UnavailableBlock />
  const rows = participantRows(payload, period)
  return (
    <div className="space-y-1">
      <div className="flex justify-end">
        <button type="button" className="text-[11px] underline"
                onClick={() => downloadCSV(`value_flows_${periodKey}.csv`, CSV_HEADER,
                                           csvRows(periodKey, rows))}>
          Export CSV
        </button>
      </div>
      <table className="text-[11px] w-full" data-testid="vf-table">
        <caption className="text-left text-muted">
          Who pays whom, {periodLabel(periodKey)} (per period-year; + received, − paid)
        </caption>
        <thead>
          <tr><th scope="col" className="text-left">Party</th>
            <th scope="col" className="text-right">Paid</th>
            <th scope="col" className="text-right">Received</th>
            <th scope="col" className="text-right">Net</th></tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id} data-testid={`vf-row-${r.id}`}>
              <th scope="row" className="text-left font-normal align-top">
                <details>
                  <summary>
                    {r.label}{r.internal ? '' : <span className="text-muted"> (external)</span>}
                  </summary>
                  <ul className="pl-3 text-muted">
                    {r.streams.map(s => (
                      <li key={s.stream} data-testid={`vf-stream-${r.id}-${s.stream}`}>
                        {streamLabel(s.stream)}: <Money v={s.net} />
                      </li>
                    ))}
                  </ul>
                </details>
              </th>
              <td className="text-right align-top"><Money v={r.paid} /></td>
              <td className="text-right align-top"><Money v={r.received} /></td>
              <td className="text-right align-top"><Money v={r.net} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function ValueFlowsView({ payload, sankeyWidth }: {
  payload: ValueFlowsPayload
  sankeyWidth?: number
}) {
  const filter = useResultsFilter()
  const requestResultsTab = useUIStore(s => s.requestResultsTab)
  const keys = periodKeys(payload)
  const [local, setLocal] = useState(keys[0] ?? '_')
  const fromShell = filter.selectedPeriod != null ? String(filter.selectedPeriod) : null
  const periodKey = fromShell && keys.includes(fromShell) ? fromShell : local
  const period = payload.periods?.[periodKey]
  const chart = useRef<HTMLDivElement>(null)
  const state = conservationState(period?.conservation)
  const flags = payload.flags ?? []

  return (
    <div className="space-y-3" data-testid="vf-view">
      {fromShell && !keys.includes(fromShell) && (
        <p className="text-[11px] text-muted" data-testid="vf-period-missing">
          No value flows for {fromShell}; showing {periodLabel(periodKey)}.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <ConservationChip state={state} />
        {keys.length > 1 && !(fromShell && keys.includes(fromShell)) && (
          <label className="text-[11px]">
            Period{' '}
            <select value={periodKey} onChange={e => setLocal(e.target.value)}>
              {keys.map(k => <option key={k} value={k}>{periodLabel(k)}</option>)}
            </select>
          </label>
        )}
        {payload.template && (
          <span className="text-[11px] text-muted">Template: {payload.template}</span>
        )}
        <span className="text-[11px] text-muted">
          Energy flows:{' '}
          <button type="button" className="underline" data-testid="vf-to-energy-flows"
                  onClick={() => requestResultsTab('flows')}>
            Operation → Energy flow
          </button>
        </span>
      </div>
      <ParticipantTable payload={payload} periodKey={periodKey} />
      <ChartFrame testId="vf-flow" title={`Money flows · ${periodLabel(periodKey)}`} hint="payer → payee · per year, in the tariff's currency"
        right={state.status === 'ok' && period
          ? <ChartActions chartRef={chart} filename={`value_flows_${periodKey}`}
            report={{
              view: 'value-flows', figure: `value_flows_${periodKey}`, unit: null,
              columns: CSV_HEADER, rows: csvRows(periodKey, participantRows(payload, period)),
            }} /> : undefined}>
        {state.status === 'ok' && period ? (
          <><div ref={chart} data-testid="vf-sankey">
            <ValueFlowSankey period={period} width={sankeyWidth} />
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted" data-testid="vf-key">
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block w-2.5 h-2.5 rounded-[2px]" style={{ background: INTERNAL }} />
              Blue: a participant of this project
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block w-2.5 h-2.5 rounded-[2px]" style={{ background: EXTERNAL }} />
              Grey: external
            </span>
          </p></>
        ) : (
          // An incomplete or failed ledger is not drawn: a partial picture of
          // who pays whom reads as the whole one.
          <div data-testid="vf-sankey-unavailable">
            <UnavailableBlock />
            {flags.length > 0 && (
              <ul className="text-[11px] text-muted pl-3">
                {flags.slice(0, 12).map(f => <li key={f}>{f}</li>)}
                {flags.length > 12 && <li>+{flags.length - 12} more</li>}
              </ul>
            )}
          </div>
        )}
      </ChartFrame>
    </div>
  )
}
