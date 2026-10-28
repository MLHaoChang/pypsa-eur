import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { errorText, profilesApi, type ApplyResult, type ReadinessRow } from '../../api/profiles'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import ProfileCompare, { profileText } from './ProfileCompare'
import { generatePending } from './renewableGeneration'
import { weatherKey } from './WeatherBar'

function Refusal({ label, error, retry, disabled = false }: { label: string; error: unknown; retry: () => void; disabled?: boolean }) {
  const raw = errorText(error)
  const plain = profileText(raw)
  return <div role="alert" className="text-warn space-y-1"><p>{label}: {plain}</p>
    <button type="button" onClick={retry} disabled={disabled} className="text-accent hover:underline disabled:opacity-50">› Retry</button>
    <details><summary>Details</summary><pre className="whitespace-pre-wrap">{raw}</pre></details>
  </div>
}

export default function RenewableTuner({ project, row, onApplied }: {
  project: string; row: ReadinessRow; onApplied: (result: ApplyResult) => void
}) {
  const id = useId()
  const qc = useQueryClient()
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const weather = useQuery({ queryKey: weatherKey(project), queryFn: () => profilesApi.weather(project) })
  const presets = useQuery({ queryKey: nk(project, 'profile_presets', row.technology), queryFn: () => profilesApi.presets(row.technology) })
  const source = weather.data?.source
  const available = (presets.data ?? []).filter(p => !p.source_type || p.source_type === source)
  const [choice, setChoice] = useState('')
  const preset = available.find(p => p.name === choice)?.name ?? available[0]?.name ?? ''
  const schema = useQuery({ queryKey: nk(project, 'profile_schema', row.technology, preset),
    queryFn: () => profilesApi.schema(row.technology, preset), enabled: Boolean(preset) })
  const [edits, setEdits] = useState<Record<string, string | number>>({})
  const [advanced, setAdvanced] = useState('')
  const [formError, setFormError] = useState('')
  const readOnly = useUIStore(s => s.readOnly)
  const activeProject = useUIStore(s => s.currentProject)
  const mode = useUIStore(s => s.uiMode)
  const [job, setJob] = useState<{ id: string; stamp: string } | null>(null)
  const stamp = JSON.stringify([project, row, preset, edits, advanced, weather.data?.source, weather.data?.year])
  const latest = useRef({ stamp, project })
  latest.current = { stamp, project }
  const outdated = Boolean(job && job.stamp !== stamp)
  const blocked = readOnly || activeProject !== project || mode !== 'expert'
  const generation = useMutation({ mutationFn: async (params: Record<string, unknown>) => {
    const submitted = stamp
    const jobId = await generatePending(project, row.technology, [row.generator], { preset, params })
    return { id: jobId, stamp: submitted }
  }, onSuccess: setJob })
  const compare = useQuery({ queryKey: nk(project, 'profile_compare', job?.id, row.generator, row.profile),
    queryFn: () => profilesApi.compare(project, row.profile === 'none' ? undefined : `current:${row.generator}`,
      `pending:${job!.id}:${row.generator}`, row.generator), enabled: Boolean(job) && !outdated && !blocked, retry: false })
  const apply = useMutation({ mutationFn: async () => {
    const state = useUIStore.getState()
    if (!job || job.stamp !== latest.current.stamp || generation.isPending || !compare.isSuccess || compare.isFetching
      || state.currentProject !== project || state.readOnly || state.uiMode !== 'expert') throw new Error('This preview is out of date: generate again')
    return profilesApi.apply(project, job.id, row.generator)
  }, onSuccess: result => {
    void qc.invalidateQueries({ predicate: query => query.queryKey[1] === project })
    if (mounted.current && useUIStore.getState().currentProject === project && latest.current.stamp === job?.stamp) onApplied(result)
  } })
  const fields = schema.data?.fields ?? []
  const changed = fields.filter(f => f.key in edits && edits[f.key] !== f.preset_value)
  function generate() {
    if (blocked || generation.isPending || apply.isPending) return
    setFormError('')
    let extra: unknown
    try { extra = JSON.parse(advanced || '{}') } catch { setFormError('Advanced settings must be a JSON object.'); return }
    if (!extra || typeof extra !== 'object' || Array.isArray(extra)) { setFormError('Advanced settings must be a JSON object.'); return }
    const params: Record<string, unknown> = { ...extra }
    for (const f of fields) {
      const supported = f.sources.includes(source!)
      if (!supported && f.key in params) { setFormError(`${f.label} needs ERA5 weather: switch the weather source to use it.`); return }
      if (!supported || !changed.includes(f)) continue
      const value = edits[f.key]
      if (f.type === 'number' && (value === '' || !Number.isFinite(Number(value)) || Number(value) < f.min! || Number(value) > f.max!)) {
        setFormError(`${f.label} must be between ${f.min} and ${f.max} ${f.unit ?? ''}.`); return
      }
      params[f.key] = value
    }
    generation.mutate(params)
  }
  if (weather.isError || presets.isError || schema.isError) return <Refusal label="Couldn't load settings" error={weather.error ?? presets.error ?? schema.error}
    retry={() => { void weather.refetch(); void presets.refetch(); void schema.refetch() }} />
  if (weather.isPending || presets.isPending || (preset && schema.isPending)) return <p role="status">Loading profile settings…</p>
  return <div className="space-y-2 text-[11px]">
    {blocked && <p>This project is read-only or no longer selected.</p>}
    <label className="block">Preset <select aria-label="Preset" className="bg-bg border border-border rounded" disabled={blocked || apply.isPending}
      value={preset} onChange={e => { setChoice(e.target.value); setEdits({}); setAdvanced('') }}>
      {available.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
    </select></label>
    {!available.length && <p>No preset is available for this weather source.</p>}
    {fields.map(f => <div key={f.key} className="border-b border-border/40 pb-1">
      <label htmlFor={`${id}-${f.key}`}>{f.label}{f.unit ? ` (${f.unit})` : ''}</label>{' '}
      {f.options ? <select id={`${id}-${f.key}`} value={edits[f.key] ?? f.preset_value ?? ''} disabled={blocked || apply.isPending || !f.sources.includes(source!)}
        className="bg-bg border border-border rounded" onChange={e => setEdits(v => ({ ...v, [f.key]: e.target.value }))}>
        {f.options.map(o => <option key={o}>{o}</option>)}
      </select> : <input id={`${id}-${f.key}`} type="number" min={f.min} max={f.max} step="any" value={edits[f.key] ?? f.preset_value ?? ''}
        disabled={blocked || apply.isPending || !f.sources.includes(source!)} className="w-24 bg-bg border border-border rounded px-1 disabled:opacity-50"
        onChange={e => setEdits(v => ({ ...v, [f.key]: e.target.value === '' ? '' : Number(e.target.value) }))} />}
      {changed.includes(f) && <span className="ml-2" data-testid={`tune-changed-${f.key}`}>Changed</span>}
      <p className="text-muted">Preset: {f.preset_value ?? 'Not set'} · {f.provenance}{f.min != null ? ` · ${f.min}–${f.max} ${f.unit ?? ''}` : ''}</p>
      {!f.sources.includes(source!) && <p>Needs ERA5 weather: switch the weather source to use it</p>}
    </div>)}
    <p>These settings apply to this run only.</p>
    <button type="button" disabled={blocked || apply.isPending} className="text-accent hover:underline disabled:opacity-50"
      onClick={() => { setEdits({}); setAdvanced(''); setFormError('') }}>Reset to preset</button>
    <details><summary>Advanced</summary><label className="block">Advanced settings
      <textarea aria-label="Advanced settings" value={advanced} disabled={blocked || apply.isPending} onChange={e => setAdvanced(e.target.value)} className="w-full bg-bg border border-border rounded font-mono" />
    </label><p>Changed fields override Advanced settings{changed.length ? `: ${changed.map(f => f.label).join(', ')}` : ''}.</p></details>
    {formError && <p role="alert" className="text-warn">{formError}</p>}
    <button type="button" disabled={blocked || !preset || !schema.data || generation.isPending || apply.isPending} className="text-accent hover:underline disabled:opacity-50" onClick={generate}>Generate</button>
    {generation.isPending && <p role="status">Generating this profile: wait before generating again.</p>}
    {generation.isError && <Refusal label="Couldn't generate" error={generation.error} retry={generate} disabled={blocked || generation.isPending || apply.isPending} />}
    {outdated && <p role="status">This preview is out of date: generate again</p>}
    {job && !outdated && compare.isFetching && <p role="status">Loading comparison…</p>}
    {job && !outdated && compare.isError && <Refusal label="Couldn't compare" error={compare.error} retry={() => { void compare.refetch() }} disabled={blocked || compare.isFetching || apply.isPending} />}
    {job && !outdated && compare.data && !compare.isError && !compare.isFetching && <ProfileCompare data={compare.data} />}
    <div><button type="button" disabled={blocked || !job || outdated || !compare.isSuccess || compare.isFetching || apply.isPending || generation.isPending}
      className="text-accent hover:underline disabled:opacity-50" onClick={() => apply.mutate()}>{apply.isPending ? 'Applying…' : 'Apply'}</button>
      {!job || compare.isFetching ? <p>Compare first</p> : compare.isError ? <p>Comparison failed: retry or close</p> : null}
    </div>
    {apply.isError && <Refusal label="Couldn't apply" error={apply.error} retry={() => apply.mutate()} disabled={blocked || generation.isPending || apply.isPending || outdated || !compare.isSuccess || compare.isFetching} />}
  </div>
}
