import { useQuery } from '@tanstack/react-query'
import { resultsEvidenceApi } from '../../api/resultsEvidence'
import { useUIStore, type RunResultRequest } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

/** Native retained evidence; no live result component is mounted in this view. */
export default function RunBoundView({ run, tab }: { run: RunResultRequest; tab: string }) {
  const project = useUIStore(s => s.currentProject)
  const section = tab === 'summary' ? undefined
    : run.resultsTab === tab && run.section ? run.section : tab
  const query = useQuery({
    queryKey: nk(project, 'run-view', run.runId, section, run.baselineRunId),
    queryFn: () => resultsEvidenceApi.run(run.runId, section, run.baselineRunId),
    enabled: !!project, staleTime: 0, refetchOnMount: 'always',
  })
  if (query.isFetching || query.isPending) return <p className="p-4" role="status">Loading retained run {run.runId}…</p>
  // LC-T08: access lost since the run is refused on read; say so plainly.
  const detail = (query.error as { response?: { data?: { detail?: { error_kind?: string; message?: string } } } } | null)
    ?.response?.data?.detail
  if (query.isError && detail?.error_kind === 'project_unavailable' && detail.message) {
    return <p className="p-4" role="status">{detail.message}</p>
  }
  if (query.isError || !query.data) return <p className="p-4" role="status">Retained {section ?? 'summary'} evidence unavailable for run {run.runId}. Current results have not been substituted.</p>
  const { data, binding } = query.data
  if (binding.project_name !== project || binding.run_id !== run.runId
    || (binding.baseline_run_id ?? undefined) !== run.baselineRunId) {
    return <p className="p-4" role="status">Matching retained source unavailable. Current results were not substituted.</p>
  }
  return <section className="p-4 overflow-auto h-full" data-result-tab={tab} data-result-binding={JSON.stringify(binding)}>
    <h3>Retained run {data.run_id} · {data.status}</h3>
    <p className="text-xs text-muted">{project} · {data.fingerprint ? `input fingerprint ${data.fingerprint}` : 'Input fingerprint unavailable'}
      {run.baselineRunId ? ` · baseline ${run.baselineRunId} (retained reference; no comparison implied)` : ''}</p>
    <p className="text-xs text-muted">Historical scope and window come from the recorded artifact; they are unavailable when not recorded. The current horizon filter does not slice historical evidence.</p>
    {data.section ? <><h4 className="mt-3">{data.section} · native retained evidence</h4><pre className="whitespace-pre-wrap break-words text-xs mt-2">{JSON.stringify(data.data, null, 2)}</pre></>
      : <><table className="text-xs mt-3"><tbody>{Object.entries(data.metrics ?? {}).map(([name, metric]) => <tr key={name}><th className="pr-4 text-left">{name}</th><td>{metric.value == null ? 'Unavailable' : metric.value} {metric.unit}</td></tr>)}</tbody></table>
        <p className="mt-3 text-xs">Recorded sections: {(data.sections ?? []).map(s => `${s.name} (${s.available ? 'available' : 'unavailable'})`).join(', ') || 'none'}</p></>}
    {(data.warnings ?? []).map(w => <p key={w} role="status">{w}</p>)}
    {data.error != null && <pre>{JSON.stringify(data.error, null, 2)}</pre>}
  </section>
}
