// Results → Load shape (site commercial UX A2): how the site's load and grid
// exchange are shaped in time — weekday × hour import and export heatmaps, a
// one-week profile with flagged intervals, monthly site energy and annual site
// energy per period. Computed server-side from the solved dispatch
// (services/results/load_shape.py). A group the network does not model, or the
// run cannot establish, is named and left out, never drawn as zero (ADR-0001).
import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, ComposedChart, AreaChart, Area, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine,
} from 'recharts'
import ResultState from '../../components/ResultState'
import { loadShapeApi, type HeatmapState, type LoadShapePayload } from '../../api/loadShape'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { useResultsFilter } from './filterContext'
import WindowSlider, { useWindowRange } from './WindowSlider'
import { CHART_AXIS, CHART_GRID, CHART_TOOLTIP, shortStamp, yAxisLabel } from './shared'
import {
  GROUP_COLOR, GROUP_LABEL, MONTH_STACK, WEEKDAYS, annualStack, cellTitle, fmtMw, fmtMwh, heatBackground,
  heatMax, missingGroups, monthRows, periodLabel, socRows, weekRows,
} from './loadShape'

function Heatmap({ title, testId, heat, rgb }: { title: string; testId: string; heat: HeatmapState; rgb: string }) {
  if (heat.status !== 'ok') {
    return (
      <div data-testid={testId} className="space-y-1">
        <h4 className="text-[11px] font-semibold">{title}</h4>
        <ResultState state={heat.status === 'not_modelled' ? 'not_modelled' : 'unavailable'} reason={heat.reason} />
      </div>
    )
  }
  const max = heatMax(heat)
  return (
    <div data-testid={testId} className="space-y-1">
      <h4 className="text-[11px] font-semibold">{title}</h4>
      <div className="grid gap-px text-[9px]" style={{ gridTemplateColumns: '28px repeat(24, minmax(0, 1fr))' }}>
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="text-center text-muted">{h % 3 === 0 ? h : ''}</span>
        ))}
        {WEEKDAYS.map((d, di) => (
          <div key={d} className="contents">
            <span className="text-muted pr-1 text-right leading-4">{d}</span>
            {heat.mean_mw[di].map((v, h) => (
              <span key={h} className="h-4 border border-border/40" data-cell={`${di}-${h}`}
                    data-value={v ?? ''} title={cellTitle(di, h, v, heat.snapshots[di][h])}
                    style={{ background: heatBackground(v, max, rgb) }} />
            ))}
          </div>
        ))}
      </div>
      <p className="text-[10px] text-muted">Mean MW per weekday and hour; a hatched cell has no snapshot. Peak cell {fmtMw(max)}.</p>
    </div>
  )
}

function Missing({ status }: { status: LoadShapePayload['series_status'] }) {
  const gone = missingGroups(status)
  if (!gone.length) return null
  return (
    <ul className="text-[11px] text-muted list-disc pl-4" data-testid="ls-missing">
      {gone.map(g => (
        <li key={g.key} data-testid={`ls-missing-${g.key}`}>
          {g.label}: {g.kind === 'not_modelled' ? 'not modelled' : 'not available for this run'}
          {g.reason ? ` — ${g.reason}` : ''}
        </li>
      ))}
    </ul>
  )
}

export default function LoadShapeTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const filter = useResultsFilter()
  const shellPeriod = typeof filter.selectedPeriod === 'number' ? filter.selectedPeriod
    : filter.selectedPeriod != null && filter.selectedPeriod !== 'all' && !Number.isNaN(Number(filter.selectedPeriod))
      ? Number(filter.selectedPeriod) : null
  const [localPeriod, setLocalPeriod] = useState<number | null>(null)
  const period = shellPeriod ?? localPeriod
  const [week, setWeek] = useState(0)
  const [annualPeriod, setAnnualPeriod] = useState<number | null | undefined>(undefined)
  const { drag, range, setDrag, clear, whole } = useWindowRange()

  const { data, isFetched } = useQuery({
    queryKey: nk(currentProject, 'results', 'load_shape', period, range?.[0], range?.[1], week),
    queryFn: () => loadShapeApi.get({ period, from: range?.[0], to: range?.[1], week }),
    placeholderData: keepPreviousData,
  })

  if (!data) {
    if (!isFetched) return null
    return (
      <div className="p-6">
        <ResultState state="not_run"
          reason="The load shape is read from the solved dispatch. Run a solve to see it." />
      </div>
    )
  }
  if (data.status !== 'ok') {
    return (
      <div className="p-6" data-testid="ls-unavailable">
        <ResultState state="unavailable" reason={data.reason ?? 'The load shape could not be established for this run.'} />
      </div>
    )
  }

  const win = data.window!
  const periods = data.periods.filter((p): p is number => p != null)
  const weekView = data.week
  const wk = weekView?.status === 'ok' ? weekRows(weekView) : null
  const soc = weekView?.status === 'ok' ? socRows(weekView) : null
  const annual = (data.annual ?? [])
  const annualRow = annual.find(r => r.period === (annualPeriod === undefined ? data.period : annualPeriod)) ?? annual[0]

  return (
    <div className="space-y-4 p-3" data-testid="ls-view">
      <div className="flex flex-wrap items-center gap-3 text-[11px]">
        {periods.length > 1 && shellPeriod == null && (
          <label>
            Period{' '}
            <select value={data.period ?? ''} onChange={e => { setLocalPeriod(Number(e.target.value)); setWeek(0); clear() }}>
              {periods.map(p => <option key={p} value={p}>{periodLabel(p)}</option>)}
            </select>
          </label>
        )}
        <span className="text-muted">{periodLabel(data.period)}</span>
      </div>
      <Missing status={data.series_status} />

      <section className="space-y-2" data-testid="ls-heatmaps">
        <h3 className="text-[12px] font-semibold">Import and export by weekday and hour</h3>
        <WindowSlider testId="ls-window" snapshots={win.snapshots} from={win.from} to={win.to}
                      start={win.start} end={win.end} drag={drag} setDrag={setDrag} whole={whole} />
        <div className="grid gap-4 md:grid-cols-2">
          <Heatmap title="Grid import" testId="ls-heat-import" heat={data.heatmaps!.import} rgb="59, 130, 246" />
          <Heatmap title="Export" testId="ls-heat-export" heat={data.heatmaps!.export} rgb="8, 145, 178" />
        </div>
      </section>

      <section className="space-y-2" data-testid="ls-week">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[12px] font-semibold">Profile snapshot</h3>
          {weekView?.status === 'ok' && (
            <span className="flex items-center gap-1 text-[11px]">
              <button type="button" aria-label="Previous week" disabled={weekView.selected <= 0}
                      onClick={() => setWeek(weekView.selected - 1)}>‹</button>
              <select aria-label="Week" data-testid="ls-week-select" value={weekView.selected}
                      onChange={e => setWeek(Number(e.target.value))}>
                {weekView.weeks.map((w, i) => <option key={w.key} value={i}>{w.key} · {shortStamp(w.start)}</option>)}
              </select>
              <button type="button" aria-label="Next week" disabled={weekView.selected >= weekView.weeks.length - 1}
                      onClick={() => setWeek(weekView.selected + 1)}>›</button>
            </span>
          )}
        </div>
        {weekView?.status === 'ok' && wk ? (
          <>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={wk.rows} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="t" {...CHART_AXIS} minTickGap={40} tickFormatter={shortStamp} />
                <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} />
                <Tooltip {...CHART_TOOLTIP} />
                <Bar dataKey="flag" name="Flagged interval" fill="#dc2626" fillOpacity={0.12} barSize={6} isAnimationActive={false} />
                {wk.keys.map(k => (
                  <Line key={k} type="monotone" dataKey={k} name={GROUP_LABEL[k]} dot={false} stroke={GROUP_COLOR[k]}
                        strokeWidth={1.5} isAnimationActive={false} />
                ))}
              </ComposedChart>
            </ResponsiveContainer>
            <p className="text-[11px] text-muted" data-testid="ls-flags">
              {weekView.flag_basis.length
                ? `Flagged: ${weekView.flag_basis.join('; ')} — ${weekView.flagged.length} of ${weekView.index.length} intervals this week.`
                : 'No flag can be established for this run (no connection capacity and no lost-load record).'}
            </p>
            {soc ? (
              <ResponsiveContainer width="100%" height={110}>
                <AreaChart data={soc} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="t" {...CHART_AXIS} minTickGap={40} tickFormatter={shortStamp} />
                  <YAxis {...CHART_AXIS} label={yAxisLabel('Battery SoC, MWh')} />
                  <Tooltip {...CHART_TOOLTIP} />
                  <Area type="monotone" dataKey="soc" name={GROUP_LABEL.battery_soc} stroke="#16a34a" fill="#16a34a"
                        fillOpacity={0.25} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : null}
          </>
        ) : (
          <ResultState state="unavailable" reason={weekView?.status === 'unavailable' ? weekView.reason : 'No week to show.'} />
        )}
      </section>

      {data.monthly?.status === 'ok' ? (
        <section className="space-y-2" data-testid="ls-monthly">
          <h3 className="text-[12px] font-semibold">Monthly site energy</h3>
          <>
            <ResponsiveContainer width="100%" height={240}>
              <ComposedChart data={monthRows(data.monthly.months)} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="month" {...CHART_AXIS} />
                <YAxis yAxisId="e" {...CHART_AXIS} label={yAxisLabel('MWh per month')} />
                <YAxis yAxisId="d" orientation="right" {...CHART_AXIS} label={yAxisLabel('MWh per day')} />
                <Tooltip {...CHART_TOOLTIP} />
                {MONTH_STACK.map(k => (
                  <Bar key={k} yAxisId="e" stackId="m" dataKey={k} name={GROUP_LABEL[k]} fill={GROUP_COLOR[k]}
                       isAnimationActive={false} />
                ))}
                <Line yAxisId="d" type="monotone" dataKey="daily" name="Daily generation" stroke="#111827"
                      dot={false} strokeWidth={1.5} isAnimationActive={false} />
                {data.monthly.average_daily_generation_mwh != null && (
                  <ReferenceLine yAxisId="d" y={data.monthly.average_daily_generation_mwh} stroke="#111827"
                                 strokeDasharray="4 3" />
                )}
              </ComposedChart>
            </ResponsiveContainer>
            <p className="text-[11px] text-muted" data-testid="ls-daily-average">
              Average daily generation {fmtMwh(data.monthly.average_daily_generation_mwh)} per day.
            </p>
          </>
        </section>
      ) : (
        // One line, not an empty chart frame: on a representative-week run this is always the answer.
        <p className="text-[11px] text-muted" data-testid="ls-monthly">
          <span className="font-semibold">Monthly site energy:</span> not available for this run —{' '}
          {data.monthly?.status === 'unavailable' ? data.monthly.reason : 'not established'}.
        </p>
      )}

      <section className="space-y-2" data-testid="ls-annual">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[12px] font-semibold">Annual site energy</h3>
          {annual.length > 1 && (
            <label className="text-[11px]">
              Year{' '}
              <select aria-label="Year" data-testid="ls-year" value={annualRow?.period ?? ''}
                      onChange={e => setAnnualPeriod(Number(e.target.value))}>
                {annual.map(r => <option key={r.period ?? 'y'} value={r.period ?? ''}>{periodLabel(r.period)}</option>)}
              </select>
            </label>
          )}
        </div>
        {annualRow?.status === 'ok' ? (() => {
          const st = annualStack(annualRow)
          return (
            <div className="space-y-1" data-testid="ls-annual-stack">
              <div className="flex h-6 w-full overflow-hidden rounded border border-border">
                {st.segments.map(s => (
                  <span key={s.key} title={`${s.label}: ${fmtMwh(s.mwh)}`} data-segment={s.key}
                        style={{ width: `${st.total > 0 ? (100 * s.mwh) / st.total : 0}%`, background: s.color }} />
                ))}
              </div>
              <ul className="flex flex-wrap gap-x-4 text-[11px]">
                {st.segments.map(s => <li key={s.key} data-testid={`ls-annual-${s.key}`}>{s.label}: {fmtMwh(s.mwh)}</li>)}
                <li data-testid="ls-annual-export">Export: {st.export == null ? 'not modelled' : fmtMwh(st.export)}</li>
                <li data-testid="ls-annual-excess">Excess: {st.excess == null ? 'not modelled' : fmtMwh(st.excess)}</li>
                <li data-testid="ls-annual-load">Load served: {fmtMwh(st.load)}</li>
              </ul>
              {st.notModelled.length > 0 && (
                <p className="text-[11px] text-muted">Not modelled here: {st.notModelled.join(', ')}.</p>
              )}
            </div>
          )
        })() : (
          <ResultState state="unavailable" reason={annualRow?.status === 'unavailable' ? annualRow.reason : 'Not available.'} />
        )}
      </section>
    </div>
  )
}
