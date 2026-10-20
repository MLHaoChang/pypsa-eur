// Profile Studio — generate renewable (wind/solar) capacity-factor profiles
// for sites and apply them onto generators' p_max_pu (profile_gen feature,
// backend Tasks 1–8).
//
// Two ways to drive it, one backend: this panel (expert mode: presets,
// parameter overrides, per-point apply) and the chat assistant's six
// profile tools, which call the SAME routes. Generation runs as a
// `profile_gen` job in the ordinary solve queue — one FIFO, so a generation
// queues behind a running solve; pending artifacts are kept last-10 per
// project by the backend.
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Play, Wand2 } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { Btn, PageSection, Seg, Sparkline, Tag } from '../components/PageKit'
import {
  errorText, profilesApi,
  type GenerateSpec, type PendingProfile, type ProfilePoint,
  type ProfileResolution, type ProfileSource, type ProfileTechnology,
} from '../api/profiles'

export const PRESETS_KEY = (tech: ProfileTechnology) => ['profiles', 'presets', tech] as const
export const PENDING_KEY = (name: string) => ['profiles', 'pending', name] as const

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'

/** Parse "id, lon, lat" lines (the map-free way to seed points). */
export function parsePoints(text: string): ProfilePoint[] {
  const out: ProfilePoint[] = []
  for (const [i, raw] of text.split('\n').entries()) {
    const line = raw.trim()
    if (!line) continue
    const [id, lon, lat] = line.split(',').map(s => s.trim())
    const lo = Number(lon), la = Number(lat)
    if (!id || !Number.isFinite(lo) || !Number.isFinite(la)) {
      throw new Error(`line ${i + 1}: expected "name, lon, lat" — got "${line}"`)
    }
    out.push({ id, lon: lo, lat: la })
  }
  return out
}

function SparkPreview({ series }: { series: Record<string, number[]> }) {
  return (
    <div className="space-y-1" data-testid="estimate-preview">
      {Object.entries(series).map(([id, vals]) => (
        <div key={id} className="flex items-center gap-2">
          <span className="text-[11px] text-muted w-28 truncate" title={id}>{id}</span>
          <div className="h-7 flex-1"><Sparkline data={vals} /></div>
          <span className="text-[11px] text-ink-500 tabular-nums w-14 text-right">
            {(vals.reduce((a, b) => a + b, 0) / Math.max(vals.length, 1) * 100).toFixed(1)}%
          </span>
        </div>
      ))}
    </div>
  )
}

function PendingCard({ name, record }: { name: string; record: PendingProfile }) {
  const qc = useQueryClient()
  const [component, setComponent] = useState(record.points[0] ?? '')
  const [message, setMessage] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)

  const apply = useMutation({
    mutationFn: () => profilesApi.apply(name, record.job_id, component),
    onMutate: () => { setMessage(null); setRefusal(null) },
    onSuccess: (r) => {
      setMessage(
        `applied to ${r.component}: ${r.hours} h, mean ${(r.mean * 100).toFixed(1)}%`
        + (r.tiled ? `, tiled over ${r.periods?.length ?? 0} periods` : '')
        + (r.time_basis ? `, time basis ${r.time_basis}` : ''),
      )
      for (const w of [...(r.warnings ?? []), ...(r.warning ? [r.warning] : [])]) {
        setMessage(m => `${m} — ⚠ ${w}`)
      }
      qc.invalidateQueries({ queryKey: PENDING_KEY(name) })
    },
    onError: e => setRefusal(errorText(e)),
  })

  return (
    <div className="border border-border rounded p-2.5 mb-2" data-testid={`pending-${record.job_id}`}>
      <div className="flex items-center gap-2 mb-1.5">
        <Tag tone="accent">{record.technology}</Tag>
        <span className="text-[12px]">
          {record.weather_year} · {record.source}{record.resolution && record.resolution !== 'hourly' ? ` · ${record.resolution}` : ''}
        </span>
        <span className="text-[11px] text-muted">
          {record.points.length} point{record.points.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <input
          className={`${INPUT} flex-1`}
          value={component}
          onChange={e => setComponent(e.target.value)}
          placeholder="generator to apply onto"
          data-testid="apply-component"
        />
        <Btn onClick={() => apply.mutate()} disabled={apply.isPending || !component}>
          Apply
        </Btn>
      </div>
      {message && <p className="text-[11px] text-green-600 mt-1.5" data-testid="apply-ok">{message}</p>}
      {refusal && <p className="text-[11px] text-red-600 mt-1.5" data-testid="apply-refusal">{refusal}</p>}
    </div>
  )
}

export function ProfileStudioView({ name }: { name: string }) {
  const [technology, setTechnology] = useState<ProfileTechnology>('wind')
  const [source, setSource] = useState<ProfileSource>('synthetic')
  const [resolution, setResolution] = useState<ProfileResolution>('hourly')
  const [preset, setPreset] = useState('default')
  const [year, setYear] = useState<number>(new Date().getUTCFullYear() - 1)
  const [pointsText, setPointsText] = useState('')
  const [expert, setExpert] = useState(false)
  const [overrides, setOverrides] = useState('{}')
  const [notice, setNotice] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<Record<string, number[]> | null>(null)

  const presets = useQuery({
    queryKey: PRESETS_KEY(technology),
    queryFn: () => profilesApi.presets(technology),
    retry: false,
  })
  const pending = useQuery({
    queryKey: PENDING_KEY(name),
    queryFn: () => profilesApi.pending(name),
    refetchInterval: (q) =>
      q.state.data?.jobs?.length ? 2000 : false,
    retry: false,
  })

  const presetNames = useMemo(
    () => (presets.data ?? []).map(p => p.name),
    [presets.data],
  )

  function spec(): GenerateSpec {
    const points = parsePoints(pointsText)
    if (!points.length) throw new Error('add at least one point ("name, lon, lat")')
    let params: Record<string, unknown> | undefined
    if (expert) {
      try {
        params = JSON.parse(overrides || '{}')
      } catch {
        throw new Error('expert overrides are not valid JSON')
      }
    }
    return { points, technology, preset, params, source, year, resolution }
  }

  const estimateMut = useMutation({
    mutationFn: () => profilesApi.estimate({ ...spec(), source: 'synthetic' }),
    onMutate: () => { setRefusal(null); setNotice(null) },
    onSuccess: (r) => setEstimate(r.series),
    onError: e => setRefusal(errorText(e)),
  })

  const generateMut = useMutation({
    mutationFn: () => profilesApi.generate(name, spec()),
    onMutate: () => { setRefusal(null); setNotice(null) },
    onSuccess: (job) => {
      setEstimate(null)
      setNotice(
        `queued as job ${job.id.slice(0, 8)}… — one queue serves solves and `
        + 'generations alike; it starts when whatever runs now finishes.',
      )
      pending.refetch()
    },
    onError: e => setRefusal(errorText(e)),
  })

  return (
    <div data-testid="profile-studio">
      <PageSection
        title="Generate"
        hint="A profile per site, from a synthetic or ERA5 (atlite) source"
      >
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <Seg
            value={technology}
            onChange={(t) => { setTechnology(t); setPreset('default'); setEstimate(null) }}
            options={[{ value: 'wind', label: 'Wind' }, { value: 'solar', label: 'Solar' }]}
          />
          <Seg
            value={source}
            onChange={setSource}
            options={[
              { value: 'synthetic', label: 'Synthetic' },
              { value: 'atlite', label: 'ERA5 (atlite)' },
            ]}
          />
          <Seg
            value={resolution}
            onChange={setResolution}
            options={[
              { value: 'hourly', label: 'Hourly' },
              { value: 'daily', label: 'Daily' },
              { value: 'weekly', label: 'Weekly' },
            ]}
          />
          <select
            className={INPUT}
            value={preset}
            onChange={e => setPreset(e.target.value)}
            data-testid="preset-select"
          >
            {presetNames.map(p => <option key={p} value={p}>{p}</option>)}
            {!presetNames.includes(preset) && <option value={preset}>{preset}</option>}
          </select>
          <input
            className={`${INPUT} w-24`}
            type="number"
            value={year}
            onChange={e => setYear(Number(e.target.value))}
            title="Weather year"
            data-testid="year-input"
          />
        </div>

        <textarea
          className={`${INPUT} w-full font-mono`}
          rows={3}
          value={pointsText}
          onChange={e => setPointsText(e.target.value)}
          placeholder={'one site per line: name, lon, lat\ng_wind, 8.0, 50.0'}
          data-testid="points-input"
        />
        <p className="text-[11px] text-muted mt-1 mb-2">
          Point ids double as the generator names at apply time.
          {source === 'atlite' && ' ERA5 downloads need a CDS API key (the backend 422 names the setup).'}
        </p>

        <button
          className="text-[12px] text-accent mb-2"
          onClick={() => setExpert(e => !e)}
          data-testid="expert-toggle"
        >
          {expert ? '▾' : '▸'} Expert mode — parameter overrides
        </button>
        {expert && (
          <textarea
            className={`${INPUT} w-full font-mono`}
            rows={3}
            value={overrides}
            onChange={e => setOverrides(e.target.value)}
            placeholder='{"turbine": {"hub_height": 120}}'
            data-testid="overrides-input"
          />
        )}

        <div className="flex items-center gap-2 mt-2">
          <Btn onClick={() => estimateMut.mutate()} disabled={estimateMut.isPending}>
            <Play size={13} className="mr-1" /> Preview estimate
          </Btn>
          <Btn variant="primary" onClick={() => generateMut.mutate()} disabled={generateMut.isPending}>
            <Download size={13} className="mr-1" /> Generate (queued)
          </Btn>
        </div>
        {estimate && (
          <div className="mt-2">
            <p className="text-[11px] text-muted mb-1">
              Estimate — synthetic source, {resolution === 'hourly' ? 'daily means (the queued generation keeps the full hourly year)' : `${resolution} means`}.
            </p>
            <SparkPreview series={estimate} />
          </div>
        )}
        {notice && <p className="text-[11px] text-green-600 mt-2" data-testid="generate-notice">{notice}</p>}
        {refusal && <p className="text-[11px] text-red-600 mt-2" data-testid="studio-refusal">{refusal}</p>}
      </PageSection>

      <PageSection
        title="Pending profiles"
        hint={`Generated, not yet applied — the backend keeps the last ${pending.data?.keep ?? 10}`}
      >
        {pending.data?.jobs?.length ? (
          <p className="text-[12px] text-muted mb-2" data-testid="jobs-live">
            {pending.data.jobs.length} generation job{pending.data.jobs.length === 1 ? '' : 's'} in the queue…
          </p>
        ) : null}
        {(pending.data?.pending ?? []).length === 0 && !pending.data?.jobs?.length && (
          <p className="text-[12px] text-muted">Nothing pending — generate a profile above.</p>
        )}
        {(pending.data?.pending ?? []).map(r => (
          <PendingCard key={r.job_id} name={name} record={r} />
        ))}
      </PageSection>
    </div>
  )
}

export default function ProfileStudioSection() {
  const currentProject = useUIStore(s => s.currentProject)
  if (!currentProject) {
    return (
      <div className="p-6 text-sm text-muted" data-testid="profile-studio-empty">
        <Wand2 size={16} className="mb-2" />
        Open a project to generate renewable profiles for its sites.
      </div>
    )
  }
  return <ProfileStudioView name={currentProject} />
}
