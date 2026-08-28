import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, ReferenceDot,
} from 'recharts'
import { AlertTriangle, Activity } from 'lucide-react'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { CHART_GRID, downloadCSV } from './shared'

// The cost-vs-availability frontier (spec §5.6, Phase 5) — the trade-off the
// feature is named for, as a CURVE. Phases 1–4 give one point on it.
//
// It lives here on the Lost load tab rather than behind a Scenarios action as
// spec §8.1 suggested. That recommendation predates Phase 4, which shipped
// exactly the multi-solve substrate the spec said did not exist: a worker
// thread with status polling driven from a Results tab. Reusing it keeps one
// pattern instead of two, and this tab already owns the study's two axes —
// the target and what was achieved against it. Easy to move if the curve ever
// becomes the primary workflow, which is the condition §8.1 set.
export interface FrontierPoint {
  cap_mwh: number
  achieved_ens_mwh: number
  achieved_shed_hours: number
  total_system_cost_eur: number
  engine: string
  fidelity: string
}
export interface FrontierRow {
  target_permyriad: number
  status: string
  point: FrontierPoint | null
  binding?: string
  period_basis?: string
}
export interface FrontierPayload {
  status: string
  points: FrontierRow[]
  error?: string | null
  warning?: string | null
  knee?: number | null
  voll_eur_per_mwh?: number
  targets_permyriad?: number[]
}

// Literal hex, not a CSS custom property: `var(--…)` does not resolve inside
// an SVG stroke here, which silently renders the curve as a bare scatter —
// the line simply never draws. Every other chart in this directory uses a
// literal for the same reason.
const CURVE = '#dc2626'   // matches the Lost load tab this panel lives on
const KNEE = '#f59e0b'    // amber, so the economic point reads as distinct

const eur = (v: number) =>
  v >= 1e9 ? `€${(v / 1e9).toFixed(2)}bn`
    : v >= 1e6 ? `€${(v / 1e6).toFixed(1)}m`
      : `€${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`

/**
 * The economic reading of the curve. `knee` is the index of the step where
 * marginal system cost first exceeds VoLL × marginal ENS avoided. Index 0
 * means even the loosest target swept is already past that point, which is a
 * different statement from "there is a knee in the middle" and is worth
 * saying differently — otherwise a user reads "knee at the first point" as
 * "tighten to here".
 */
export function kneeMessage(
  knee: number | null | undefined, rows: FrontierRow[], voll: number | undefined,
): string | null {
  const ok = rows.filter(r => r.status === 'ok' && r.point)
  if (knee == null || ok.length < 2 || !voll) {
    return ok.length >= 2
      ? 'No economic knee inside the swept range — every step still buys more '
        + 'avoided-shed value than it costs. Sweep tighter targets to find one.'
      : null
  }
  const a = ok[knee].point!, b = ok[knee + 1]?.point
  if (!b) return null
  const dCost = b.total_system_cost_eur - a.total_system_cost_eur
  const dEns = a.achieved_ens_mwh - b.achieved_ens_mwh
  const perMwh = dEns > 0 ? dCost / dEns : NaN
  const rate = isFinite(perMwh)
    ? `${eur(perMwh)}/MWh avoided against a VoLL of ${eur(voll)}/MWh`
    : ''
  return knee === 0
    ? `Every step in this range costs more than the energy it saves — ${rate}. `
      + 'The loosest target swept is already past the economic optimum; sweep '
      + 'looser targets to find it.'
    : `Economic knee at ${ok[knee].target_permyriad}‱: tightening beyond it costs ${rate}.`
}

export function FrontierPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)

  const { data } = useQuery({
    queryKey: nk(currentProject, 'results', 'frontier'),
    queryFn: () => resultsApi.getFrontier(),
    refetchInterval: (q) =>
      (q.state.data as FrontierPayload | null)?.status === 'running' ? 2000 : false,
  })
  const payload = (data ?? null) as FrontierPayload | null
  const running = payload?.status === 'running'

  const run = useMutation({
    mutationFn: () => resultsApi.startFrontier(),
    onSuccess: () => qc.invalidateQueries({
      queryKey: nk(currentProject, 'results', 'frontier') }),
  })

  const rows = payload?.points ?? []
  const chart = useMemo(
    () => rows.filter(r => r.status === 'ok' && r.point).map(r => ({
      ens: r.point!.achieved_ens_mwh,
      cost: r.point!.total_system_cost_eur,
      target: r.target_permyriad,
    })),
    [rows],
  )
  const okRows = rows.filter(r => r.status === 'ok' && r.point)
  const unreachable = rows.filter(r => r.status !== 'ok')
  const knee = payload?.knee
  const kneePt = knee != null && okRows[knee]?.point
    ? { ens: okRows[knee].point!.achieved_ens_mwh,
        cost: okRows[knee].point!.total_system_cost_eur }
    : null

  const exportCsv = () => downloadCSV(
    'cost-vs-availability.csv',
    ['target_permyriad', 'status', 'cap_mwh', 'achieved_ens_mwh',
     'achieved_shed_hours', 'total_system_cost_eur_excl_shed', 'binding',
     'engine', 'fidelity'],
    rows.map(r => [r.target_permyriad, r.status, r.point?.cap_mwh ?? '',
      r.point?.achieved_ens_mwh ?? '', r.point?.achieved_shed_hours ?? '',
      r.point?.total_system_cost_eur ?? '', r.binding ?? '',
      r.point?.engine ?? '', r.point?.fidelity ?? '']),
  )

  return (
    <section className="border border-border rounded" data-testid="frontier-panel">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent"
      >
        <Activity size={11} /> Cost vs availability {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          <p className="text-[11px] text-muted">
            Sweeps the reliability target and re-solves at each value, so capacity
            expansion re-optimises for every standard. The cost axis is total system
            cost <strong>excluding</strong> shed cost — including it would put the
            x-axis inside the y-axis. One full solve per point.
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => run.mutate()} disabled={running}
              className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {running ? 'Studying…' : 'Run study'}
            </button>
            {rows.length > 0 && (
              <button onClick={exportCsv}
                className="px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent">
                CSV
              </button>
            )}
            {payload?.error && (
              <span className="text-[10px] text-danger">{payload.error}</span>
            )}
          </div>

          {payload?.warning && (
            <p className="inline-flex items-start gap-1 text-[10px] text-warn border border-warn/40 rounded px-2 py-1">
              <AlertTriangle size={11} className="mt-[1px] shrink-0" />
              <span>{payload.warning}</span>
            </p>
          )}

          {chart.length > 1 && (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chart} margin={{ top: 8, right: 12, bottom: 24, left: 8 }}>
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="ens" type="number" tick={{ fontSize: 10 }}
                    label={{ value: 'Unserved energy (MWh)', position: 'insideBottom',
                             offset: -14, style: { fontSize: 10 } }} />
                  {/* Fitted domain, not zero-based. The interesting spread on a
                      frontier is small in relative terms — a few percent of total
                      system cost — so a zero-based axis renders the curve as a
                      flat row of dots and hides the very curvature and knee the
                      study exists to show. A truncated axis does exaggerate
                      differences, which is why the ticks carry absolute euro
                      values rather than an index. */}
                  <YAxis tick={{ fontSize: 10 }} tickFormatter={(v: number) => eur(v)}
                    domain={['auto', 'auto']} width={70} />
                  <RTooltip
                    formatter={(v: number, n: string) =>
                      n === 'cost' ? [eur(v), 'System cost (excl. shed)'] : [v, n]}
                    labelFormatter={(v: number) => `${v.toFixed(2)} MWh unserved`}
                    contentStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="cost" stroke={CURVE}
                    dot={{ r: 3 }} isAnimationActive={false} />
                  {kneePt && (
                    <ReferenceDot x={kneePt.ens} y={kneePt.cost} r={5}
                      fill={KNEE} stroke="none" />
                  )}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {okRows.length > 0 && (
            <p className="text-[10px] text-muted">
              {kneeMessage(knee, rows, payload?.voll_eur_per_mwh)}
            </p>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[10px]">
                <thead className="text-muted">
                  <tr>
                    <th className="text-left font-medium py-1">Target ‱</th>
                    <th className="text-right font-medium">ENS (MWh)</th>
                    <th className="text-right font-medium">Shed-hours</th>
                    <th className="text-right font-medium">Cost (excl. shed)</th>
                    <th className="text-left font-medium pl-3">Bound by</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {rows.map(r => (
                    <tr key={r.target_permyriad} className="border-t border-border/50">
                      <td className="py-0.5">{r.target_permyriad}</td>
                      {r.point ? (
                        <>
                          <td className="text-right">{r.point.achieved_ens_mwh.toFixed(2)}</td>
                          <td className="text-right">{r.point.achieved_shed_hours.toFixed(1)}</td>
                          <td className="text-right">{eur(r.point.total_system_cost_eur)}</td>
                          <td className="pl-3 font-sans">{r.binding ?? '—'}</td>
                        </>
                      ) : (
                        <td colSpan={4} className="pl-3 font-sans text-warn">
                          no plan meets this target ({r.status})
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {unreachable.length > 0 && okRows.length > 0 && (
            <p className="text-[10px] text-muted">
              {unreachable.length} target{unreachable.length > 1 ? 's are' : ' is'} unreachable
              and shown as such rather than interpolated over — a standard no plan can meet
              is a real answer.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
