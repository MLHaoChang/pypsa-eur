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
const periodWords = (p: string) => (p === 'all' ? 'All periods' : p)

/** The shortfall of each period, one sentence each (never colour alone). */
export function shortfallSentences(t: Timeline): string[] {
  return t.periods.filter(p => p.shortfall_mw > 0).map(p =>
    `${periodWords(p.period)}: ${mw(p.shortfall_mw)} of the peak demand has no firm supply `
    + `(demand ${mw(p.demand_mw)}, grid ${mw(p.grid_mw)}, on-site ${mw(p.onsite_mw)}).`)
}

export function EnergizationTimelineView({ data }: { data: Timeline }) {
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
    <div data-testid="energization-timeline" className="flex flex-col gap-2 px-3 py-2 text-[11px] text-text">
      <div className="font-semibold">Demand against energization</div>
      <div data-testid="energization-timeline-basis" className="text-text-muted">
        {data.basis === 'run' ? 'Showing the last run: ' : 'Showing the inputs, no run: '}{data.basis_note}
      </div>
      <div className="h-[180px]" data-testid="energization-timeline-chart">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="period" tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} unit=" MW" width={56} />
            <Tooltip formatter={(v: number) => mw(v)} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar stackId="s" dataKey="grid" name="Energized grid import" fill="#2563eb" />
            {kinds.map(k => (
              <Bar key={k} stackId="s" dataKey={k} name={k === 'genset' ? 'On-site gensets' : 'On-site batteries'}
                fill={k === 'genset' ? '#16a34a' : '#0d9488'} />
            ))}
            <Bar stackId="s" dataKey="shortfall" name="Shortfall (no firm supply)" fill="#dc2626" fillOpacity={0.35}
              stroke="#dc2626" strokeDasharray="3 2" />
            <Line type="monotone" dataKey="demand" name="Peak facility demand" stroke="#111827" strokeWidth={2} />
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
          {ran.map(p => `${periodWords(p.period)}: served ${Math.round(p.run!.served_mwh)} MWh · shed ${Math.round(p.run!.shed_mwh)} MWh · curtailed ${Math.round(p.run!.curtailed_mwh)} MWh`).join(' · ')}
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
  const { data } = useQuery({
    queryKey: nk(project, 'energizationTimeline', revision),
    queryFn: () => getEnergizationTimeline(project ?? ''),
    enabled: !!project && revision !== null,
    retry: false,
    staleTime: 0,
  })
  if (!data) return null
  return <EnergizationTimelineView data={data} />
}
