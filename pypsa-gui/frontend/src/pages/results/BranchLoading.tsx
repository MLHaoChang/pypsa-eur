import { useQuery } from '@tanstack/react-query'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, Legend } from 'recharts'
import { resultsEvidenceApi } from '../../api/resultsEvidence'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { downloadCSV, KPI, fmtPower } from './shared'
import ResultState, { type NonValueState } from '../../components/ResultState'

const number = (value: number | null | undefined, unit = '') =>
  value == null ? 'Unavailable' : `${value.toFixed(2)}${unit}`

/** Tables, chart points, exports and chat use one server calculation. */
export default function BranchLoading({ from, to, enabled }: { from: number; to: number; enabled: boolean }) {
  const project = useUIStore(s => s.currentProject)
  const source = useUIStore(s => s.resultSource)
  const query = useQuery({
    queryKey: nk(project, 'results', 'loadflow-view', source, from, to),
    queryFn: () => resultsEvidenceApi.loadflow(source, from, to),
    enabled: enabled && !!project,
    staleTime: 0, refetchOnMount: 'always',
  })
  const data = query.data
  if (!enabled || query.isPending || query.isFetching) return <p role="status">Loading matching load-flow evidence…</p>
  if (query.isError) return <p role="status">Load-flow evidence unavailable. Try again after checking the project and solve.</p>
  if (data?.status !== 'ok') {
    const states: Record<string, NonValueState> = { no_ac_branches: 'not_applicable',
      not_solved: 'not_run', solve_in_progress: 'running', solve_inputs_changed: 'stale', stale_results: 'stale' }
    return <div role="status"><ResultState state={states[data?.unavailable_kind ?? ''] ?? 'unavailable'} reason={data?.message ?? 'Load-flow evidence unavailable.'} /></div>
  }
  if (!data.binding || data.binding.project_name !== project) return <p role="status">Matching project evidence unavailable.</p>
  if (data.binding.scope !== 'current' || data.binding.source !== source
    || data.binding.window_from !== from || data.binding.window_to !== to
    || !/^[a-f0-9]{64}$/.test(data.binding.input_sha256 ?? '')
    || !/^[a-f0-9]{64}$/.test(data.binding.data_sha256)) return <p role="status">Matching source and window evidence unavailable.</p>
  const rows = data.rows ?? []
  const flow = rows.filter(r => r.peak_flow != null).map(r => r.peak_flow!)
  const chartRows = (data.series_index ?? []).map((stamp, i) => Object.fromEntries([
    ['snapshot', stamp], ...(data.series ?? []).map(s => [s.asset_key ?? `${s.class}: ${s.name}`, s.util_pct[i]]),
  ]))
  return <section data-testid="branch-loading" data-result-tab="loadflow"
    data-result-binding={data.binding ? JSON.stringify(data.binding) : undefined}>
    <p className="text-xs text-muted mb-2">{source === 'lopf' ? 'Active-power proxy (MW)' : 'Apparent power where reactive evidence exists (MVA); other branches use the active-power proxy'}.
      {' '}Larger terminal magnitude; configured rating, or optimized rating for extendable branches; snapshot-weighted means and hours; time-varying s_max_pu limits.</p>
    <p className="text-xs text-muted mb-2">Window {data.window?.from}–{data.window?.to} · {data.window?.snapshots} snapshots · {number(data.window?.weighted_hours, ' weighted h')}. {data.claims?.reason}</p>
    <div className="grid grid-cols-2 gap-2 mb-3">
      <KPI label={data.rows_truncated ? 'Peak flow (shown branches)' : 'Peak flow'} value={source === 'lopf' ? fmtPower(flow.length ? Math.max(...flow) : null) : 'See each branch basis below'} hint="Larger terminal magnitude over the shown ranked lines and transformers in this window. MW proxies and MVA apparent powers are not combined." />
      <KPI label="Ranked branches" value={String(rows.length)} hint="Missing ratings or result evidence are listed separately, never counted as zero loading." />
    </div>
    <button type="button" className="text-xs underline mb-2" onClick={() => downloadCSV('loadflow_loading.csv',
      ['project_id', 'source', 'window_from', 'window_to', 'evidence_sha256', 'asset_key', 'class', 'name', 'basis', 'rating_MVA', 'rating_basis', 'peak_flow', 'weighted_mean_flow', 'peak_limit_pct', 'peak_rating_pct', 'weighted_mean_limit_pct', 'hours_at_limit', 'hours_over_limit'],
      rows.map(r => [data.binding!.project_id, source, from, to, data.binding!.data_sha256,
        r.asset_key ?? '', r.class, r.name, r.basis, r.rating.value ?? '', r.rating.basis,
        r.peak_flow ?? '', r.mean_flow ?? '', r.util_pct ?? '', r.rating_pct ?? '',
        r.mean_util_pct ?? '', r.h_at_limit ?? '', r.h_over_limit ?? '']))}>Export shown loading CSV</button>
    {data.rows_truncated && <p role="status">Showing the first 500 ranked branches.</p>}
    <div className="overflow-auto max-h-80 border border-border rounded">
      <table className="w-full text-xs"><thead><tr>{['Branch', 'Basis', 'Rating MVA', 'Peak flow', 'Weighted mean flow', 'Peak limit %', 'Peak rating %', 'Weighted mean limit %', 'At limit h', 'Over limit h', 'Coverage'].map(t => <th key={t} className="p-2 text-left">{t}</th>)}</tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.asset_key ?? `${r.class}:${r.name}`}>
            <td className="p-2">{r.class}: {r.name}{r.name_truncated ? ' (name truncated)' : ''}</td>
            <td>{r.basis === 'active_power_proxy' ? 'Active power (MW)' : 'Apparent power (MVA)'}</td>
            <td>{number(r.rating.value)} ({r.rating.basis})</td>
            <td>{number(r.peak_flow)}</td><td>{number(r.mean_flow)}</td>
            <td>{number(r.util_pct)}</td><td>{number(r.rating_pct)}</td><td>{number(r.mean_util_pct)}</td>
            <td>{number(r.h_at_limit)}</td><td>{number(r.h_over_limit)}</td>
            <td>{r.snapshots_with_data ?? data.window?.snapshots} snapshots{r.terminals ? ' · bus 0 only' : ''}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
    {(data.skipped ?? []).map(r => <p key={`${r.class}:${r.name}`} className="text-xs text-muted mt-1">{r.class}: {r.name} — unavailable: {r.reason}</p>)}
    {data.skipped_truncated && <p>Additional unavailable branches omitted from this bounded view.</p>}
    {(data.missing_evidence ?? []).map(reason => <p key={reason} className="text-xs text-muted mt-1">{reason}</p>)}
    {!!chartRows.length && <><h3 className="mt-4 mb-2">Loading against effective limit (%) · ranked branches</h3>
      {data.series_truncated && <p className="text-xs">Chart bounded to ten branches and the first 800 selected snapshots. Table statistics cover the entire selected window.</p>}
      <ResponsiveContainer width="100%" height={220}><LineChart data={chartRows}><XAxis dataKey="snapshot" hide /><YAxis unit="%" /><Tooltip /><Legend />
        {(data.series ?? []).map((s, i) => <Line key={s.asset_key ?? `${s.class}:${s.name}`}
          dataKey={s.asset_key ?? `${s.class}: ${s.name}`}
          name={`${s.class}: ${s.name}${s.name_truncated ? `… [${s.asset_key?.slice(0, 8)}]` : ''}`}
          stroke={['#0ea5e9', '#16a34a', '#dc2626', '#d97706', '#7c3aed'][i % 5]} dot={false} connectNulls={false} />)}
      </LineChart></ResponsiveContainer></>}
  </section>
}
