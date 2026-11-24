// Demand against energization, per model period (DC template review 06). One component, two mounts
// (Guided Site card, Expert Issues): per period the energized grid import and the committed on-site
// firm capacity, stacked, against the peak facility demand, and the shortfall in words. It reads the
// open network (`GET /flow/energization_timeline`), so it redraws after any edit with no solve; after
// a run it adds what the run served, shed and curtailed, and says which basis it shows.
import { useQuery } from '@tanstack/react-query'
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { getEnergizationTimeline, type EnergizationTimeline as Timeline } from '../../api/flow'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

const mw = (v: number) => `${Number(v.toPrecision(4))} MW`
const mwh = (v: number) => `${Math.round(v).toLocaleString('en-GB')}\u00A0MWh`
const periodWords = (p: string) => (p === 'all' ? 'All periods' : p)

/** The shortfall of each period, one sentence each (never colour alone). */
export function shortfallSentences(t: Timeline): string[] {
  return t.periods.filter(p => p.shortfall_mw > 0).map(p =>
    `${periodWords(p.period)}: ${mw(p.shortfall_mw)} of the peak demand has no firm supply `
    + `(demand ${mw(p.demand_mw)}, grid ${mw(p.grid_mw)}, on-site ${mw(p.onsite_mw)}).`)
}

export function EnergizationTimelineView({ data, busy = false }: { data: Timeline; busy?: boolean }) {
  const kinds = Array.from(new Set(data.periods.flatMap(p => p.onsite.map(o => o.kind))))
  const rows = data.periods.map(p => ({
    period: periodWords(p.period),
    grid: p.grid_mw,
    ...Object.fromEntries(kinds.map(k => [k, p.onsite.find(o => o.kind === k)?.mw ?? 0])),
    shortfall: p.shortfall_mw,
    demand: p.demand_mw,
  }))
  const gaps = shortfallSentences(data)
  const ttp = data.time_to_power
  const ran = data.periods.filter(p => p.run)
  return (
    <div data-testid="energization-timeline" aria-busy={busy} className="flex flex-col gap-2 px-3 py-2 text-[11px] text-text">
      <div className="font-semibold">Demand against energization</div>
      <div data-testid="energization-timeline-basis" className="text-text-muted">
        {data.basis === 'run'
          ? `Last run${data.run_state === 'out_of_date' ? ' (out of date: inputs changed since)' : ''}: `
          : 'From inputs, no run: '}{data.basis_note}
      </div>
      <div data-testid="energization-timeline-key" className="text-text-muted">
        Key: bars are firm power in MW (grid import, then on-site gensets and batteries); the hatched bar is the shortfall;
        the line is the peak facility demand, an assumed profile (not a measurement).
      </div>
      <div className="h-[230px]" data-testid="energization-timeline-chart">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 16, left: 0 }}>
            <defs>
              <pattern id="et-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="6" height="6" fill="#fee2e2" /><line x1="0" y1="0" x2="0" y2="6" stroke="#dc2626" strokeWidth="2" />
              </pattern>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" strokeOpacity={0.2} />
            <XAxis dataKey="period" tick={{ fontSize: 10 }} label={{ value: 'Model period', position: 'insideBottom', offset: -2, fontSize: 10 }} height={32} />
            <YAxis tick={{ fontSize: 10 }} width={56} label={{ value: 'Power (MW)', angle: -90, position: 'insideLeft', fontSize: 10 }} />
            <Tooltip formatter={(v: number) => mw(v)} />
            <Legend wrapperStyle={{ fontSize: 10, paddingTop: 12 }} />
            <Bar stackId="s" dataKey="grid" name="Energized grid import" fill="#2563eb" isAnimationActive={false} />
            {kinds.map(k => (
              <Bar key={k} stackId="s" dataKey={k} name={k === 'genset' ? 'On-site gensets' : 'On-site batteries'}
                fill={k === 'genset' ? '#16a34a' : '#0d9488'} isAnimationActive={false} />
            ))}
            <Bar stackId="s" dataKey="shortfall" name="Shortfall (no firm supply)" fill="url(#et-hatch)"
              stroke="#dc2626" isAnimationActive={false} />
            <Line type="linear" dataKey="demand" name="Peak facility demand (assumed profile)" stroke="currentColor" strokeWidth={2} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div data-testid="energization-timeline-shortfall" role="status">
        {gaps.length ? gaps.map(g => <div key={g}>{g}</div>) : 'Every period has firm supply for its peak demand.'}
      </div>
      <div data-testid="energization-timeline-ttp">
        {ttp.status === 'available'
          ? `First grid power: ${ttp.first_power} (${ttp.months} months from the project start). Grid-delivered power only.`
          : `Time to power not available: ${ttp.words}.`}
      </div>
      {ran.length > 0 && (
        <div data-testid="energization-timeline-run" className="text-text-muted">
          Energy of the last run, in MWh (not power): {ran.map(p => `${periodWords(p.period)}: served ${mwh(p.run!.served_mwh)} · shed ${mwh(p.run!.shed_mwh)} · curtailed ${mwh(p.run!.curtailed_mwh)}`).join(' · ')}
        </div>
      )}
    </div>
  )
}

/** Self-hiding where the project has no data-centre load (the route refuses). */
export function EnergizationTimeline() {
  const project = useUIStore(s => s.currentProject)
  const { data: undo } = useQuery({
    queryKey: nk(project, 'undoInfo'),
    queryFn: () => networkApi.undoInfo(),
    refetchInterval: 3000,
    enabled: !!project,
  })
  const revision = typeof undo?.network_revision === 'number' ? undo.network_revision : null
  const { data, isFetching, isError, error: err } = useQuery({
    queryKey: nk(project, 'energizationTimeline', revision),
    queryFn: () => getEnergizationTimeline(project ?? ''),
    enabled: !!project && revision !== null,
    retry: false,
    staleTime: 0,
  })
  if (!data) {
    // A project with no data-centre load has no timeline: say nothing. Any other failure says why.
    const kind = (err as { response?: { data?: { detail?: { error_kind?: string } } } } | null)?.response?.data?.detail?.error_kind
    return isError && kind !== 'energization_timeline_unavailable'
      ? <div role="status" className="px-3 py-2 text-[11px] text-text-muted">Demand against energization could not be read: reload to try again.</div>
      : null
  }
  return <EnergizationTimelineView data={data} busy={isFetching} />
}
