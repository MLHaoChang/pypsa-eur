import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ProfileCompare as Comparison, ProfileStats } from '../../api/profiles'
import { WarningList } from './SeriesKit'

const METRICS: Array<[keyof Omit<ProfileStats, 'duration' | 'monthly'>, string, string]> = [
  ['flh', 'Full-load hours', 'h/year'], ['cf', 'Capacity factor', 'p.u.'],
  ['peak', 'Peak', 'p.u.'], ['peak_hour', 'Mean peak hour', 'UTC hour'],
  ['p10', 'P10', 'p.u.'], ['p50', 'P50', 'p.u.'], ['p90', 'P90', 'p.u.'],
]
const fmt = (v: number | null | undefined) => v == null ? 'Not available'
  : v.toLocaleString('en-GB', { useGrouping: false, maximumFractionDigits: 4 })

function Curves({ rows, current, duration }: {
  rows: Array<{ x: number | string; new: number; current?: number }>; current: boolean; duration?: boolean
}) {
  return <div className="h-36" aria-hidden="true"><ResponsiveContainer width="100%" height="100%">
    <LineChart data={rows} margin={{ top: 5, right: 12, left: 0, bottom: 14 }}>
      <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
      <XAxis dataKey="x" tick={{ fontSize: 9 }} label={{ value: duration ? 'Share of hours (%)' : 'Model time (UTC)', position: 'insideBottom', offset: -10 }}
        tickFormatter={v => duration ? String(v) : String(v).replace('T', ' ').slice(5, 16)} />
      <YAxis width={40} tick={{ fontSize: 9 }} label={{ value: 'p.u.', angle: -90, position: 'insideLeft' }} />
      <Tooltip /><Legend verticalAlign="top" />
      {current && <Line name="Current" dataKey="current" stroke="currentColor" strokeOpacity={0.3} strokeDasharray="5 3" dot={false} isAnimationActive={false} />}
      <Line name="New" dataKey="new" stroke="var(--color-accent)" dot={false} isAnimationActive={false} />
    </LineChart>
  </ResponsiveContainer></div>
}

/** Displays native comparison facts; never manufactures a missing baseline. */
export default function ProfileCompare({ data }: { data: Comparison }) {
  const current = data.a !== null
  const week = data.week.snapshots.map((x, i) => ({ x, new: data.week.b[i], ...(current ? { current: data.week.a?.[i] } : {}) }))
  const duration = data.b.duration.map((v, i, values) => ({ x: values.length < 2 ? 0 : i * 100 / (values.length - 1), new: v,
    ...(current ? { current: data.a?.duration[i] } : {}) }))
  return <div className="space-y-2 text-[11px]">
    {!current && <p>No current profile: showing the new profile only</p>}
    <figure data-testid="compare-week">
      <figcaption>Peak week{data.week.period != null ? ` · planning period ${data.week.period}` : ''} · per-unit availability</figcaption>
      <Curves rows={week} current={current} />
    </figure>
    <figure data-testid="compare-duration">
      <figcaption>Duration curve · per-unit availability</figcaption>
      <Curves rows={duration} current={current} duration />
    </figure>
    <p className="sr-only" data-testid="compare-text">
      {current ? `Current full-load hours ${fmt(data.a?.flh)} h/year. ` : ''}New full-load hours {fmt(data.b.flh)} h/year.
      {current ? ' Current is dashed; New is solid.' : ' Only the New profile is shown.'}
    </p>
    <table className="w-full text-left"><caption className="sr-only">Profile statistics computed by the server</caption>
      <thead><tr><th scope="col">Statistic</th>{current && <th scope="col">Current</th>}<th scope="col">New</th>{current && <th scope="col">Change</th>}</tr></thead>
      <tbody>{METRICS.map(([key, label, unit]) => <tr key={key} className="border-t border-border/40">
        <th scope="row" className="font-normal py-1">{label} ({unit})</th>
        {current && <td>{fmt(data.a?.[key])}</td>}<td>{fmt(data.b[key])}</td>
        {current && <td>{data.delta?.[key] != null && data.delta[key]! > 0 ? '+' : ''}{fmt(data.delta?.[key])}</td>}
      </tr>)}</tbody>
    </table>
    <WarningList warnings={data.warnings ?? []} />
  </div>
}
