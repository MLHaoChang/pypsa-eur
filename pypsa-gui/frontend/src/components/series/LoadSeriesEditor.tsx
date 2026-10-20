// The load series editor (TS-01): an archetype with its scale, or a measured
// upload, previewed with its numbers before Apply; then optional refinement
// (TS-04). One component, host-agnostic: the load's Properties panel (Expert)
// and the Time Series drawer (Guided and Expert) mount the same thing, and
// the section-4 load row will (UX review 2026-10-20).
//
// Guided shows the key parameters — archetype or preset, peak or annual
// energy, the preview, Apply. The archetype's own parameters and the
// refinement ops sit under Advanced, open by default in Expert.
import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Play, Upload } from 'lucide-react'
import { Btn, Seg } from '../PageKit'
import { errorText, seriesApi, type PendingSeries } from '../../api/profiles'
import { CurrentSeriesLine, INPUT, PendingCard, RefinePanel, type EditorMode } from './SeriesKit'

const SCALE_KEYS = new Set(['peak_mw', 'annual_mwh', 'preset'])
const UNITS = ['kW', 'MW', 'kWh', 'MWh']

export interface LoadSeriesEditorProps {
  project: string
  component: string
  mode: EditorMode
  /** Start on the upload tab (the Time Series row's upload button). */
  initialTab?: 'archetype' | 'upload'
}

function coerce(raw: string, like: unknown): unknown {
  if (raw === '') return like
  if (typeof like === 'number') return Number(raw)
  if (typeof like === 'boolean') return raw === 'true'
  if (Array.isArray(like) || like === null) {
    try { return JSON.parse(raw) } catch { return raw }
  }
  return raw
}

export default function LoadSeriesEditor({ project, component, mode, initialTab = 'archetype' }: LoadSeriesEditorProps) {
  const [tab, setTab] = useState<'archetype' | 'upload'>(initialTab)
  const [choice, setChoice] = useState('industrial')
  const [scaleKind, setScaleKind] = useState<'peak' | 'energy'>('peak')
  const [scale, setScale] = useState('')
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [advanced, setAdvanced] = useState(mode === 'expert')
  const [pending, setPending] = useState<PendingSeries | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [unit, setUnit] = useState('kW')
  const [timezone, setTimezone] = useState('')
  const [annual, setAnnual] = useState('')

  const { data: catalogue } = useQuery({ queryKey: ['load-archetypes'], queryFn: seriesApi.loadArchetypes })
  const preset = catalogue?.presets.find(p => p.name === choice)
  const archetypeName = preset ? preset.archetype : choice
  const defaults = useMemo(
    () => catalogue?.archetypes.find(a => a.name === archetypeName)?.params ?? {},
    [catalogue, archetypeName],
  )

  const generate = useMutation({
    mutationFn: () => {
      const params: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(overrides)) params[k] = coerce(v, defaults[k])
      params[scaleKind === 'peak' ? 'peak_mw' : 'annual_mwh'] = Number(scale)
      if (preset) params.preset = preset.name
      return seriesApi.generateLoad(project, { archetype: archetypeName, loads: [component], params })
    },
    onSuccess: setPending,
  })
  const upload = useMutation({
    mutationFn: () => seriesApi.uploadLoad(project, file as File, {
      unit, timezone: timezone || undefined, annual_mwh: annual ? Number(annual) : undefined,
    }),
    onSuccess: setPending,
  })

  const busy = generate.isPending || upload.isPending
  const error = generate.error ?? upload.error

  return (
    <div className="text-xs" data-testid="load-series-editor">
      <CurrentSeriesLine project={project} componentType="loads" attribute="p_set" component={component}
        onPending={p => { setPending(p); setAdvanced(true) }} />
      <Seg value={tab} onChange={v => { setTab(v); setPending(null) }}
        options={[{ value: 'archetype', label: 'Archetype' }, { value: 'upload', label: 'Measured upload' }]} />

      {tab === 'archetype' ? (
        <div className="mt-2 space-y-1">
          <label className="flex items-center gap-2">
            <span className="w-20 text-muted">Shape</span>
            <select aria-label="Archetype" className={INPUT} value={choice} onChange={e => { setChoice(e.target.value); setOverrides({}) }}>
              <optgroup label="Archetypes">
                {(catalogue?.archetypes ?? []).map(a => <option key={a.name} value={a.name}>{a.name.replace(/_/g, ' ')}</option>)}
              </optgroup>
              <optgroup label="Presets">
                {(catalogue?.presets ?? []).map(p => <option key={p.name} value={p.name}>{p.name.replace(/_/g, ' ')}</option>)}
              </optgroup>
            </select>
          </label>
          <div className="flex items-center gap-2">
            <span className="w-20 text-muted">Scale</span>
            <Seg value={scaleKind} onChange={setScaleKind}
              options={[{ value: 'peak', label: 'Peak MW' }, { value: 'energy', label: 'Annual MWh' }]} />
            <input aria-label={scaleKind === 'peak' ? 'Peak MW' : 'Annual MWh'} className={`${INPUT} w-24`}
              inputMode="decimal" value={scale} onChange={e => setScale(e.target.value)} />
          </div>
          <button type="button" className="block text-[11px] text-accent" onClick={() => setAdvanced(!advanced)}>
            {advanced ? 'Hide advanced' : 'Advanced'}
          </button>
          {advanced && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-1" data-testid="archetype-params">
              {Object.entries(defaults).filter(([k]) => !SCALE_KEYS.has(k)).map(([k, v]) => (
                <label key={k} className="flex items-center gap-1">
                  <span className="text-muted flex-1 min-w-0 break-words" title={k}>{k.replace(/_/g, ' ')}</span>
                  <input aria-label={k} className={`${INPUT} w-24`}
                    placeholder={v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)}
                    value={overrides[k] ?? ''} onChange={e => setOverrides({ ...overrides, [k]: e.target.value })} />
                </label>
              ))}
            </div>
          )}
          <Btn variant="primary" onClick={() => generate.mutate()} disabled={!scale || busy}>
            <Play size={12} /> Preview
          </Btn>
        </div>
      ) : (
        <div className="mt-2 space-y-1">
          <input aria-label="Meter file" type="file" accept=".csv,.xlsx,.xls"
            onChange={e => setFile(e.target.files?.[0] ?? null)} />
          <label className="flex items-center gap-2">
            <span className="w-20 text-muted">Unit</span>
            <select aria-label="Unit" className={INPUT} value={unit} onChange={e => setUnit(e.target.value)}>
              {UNITS.map(u => <option key={u}>{u}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="w-20 text-muted">Time zone</span>
            <input aria-label="Time zone" className={`${INPUT} w-40`} placeholder="project's, else UTC"
              value={timezone} onChange={e => setTimezone(e.target.value)} />
          </label>
          <label className="flex items-center gap-2">
            <span className="w-20 text-muted">Rescale</span>
            <input aria-label="Rescale to annual MWh" className={`${INPUT} w-24`} placeholder="MWh, optional"
              value={annual} onChange={e => setAnnual(e.target.value)} />
          </label>
          <Btn variant="primary" onClick={() => upload.mutate()} disabled={!file || busy}>
            <Upload size={12} /> Read file
          </Btn>
        </div>
      )}

      {error && <p className="text-[11px] text-red-600 mt-1" data-testid="editor-error">{errorText(error)}</p>}

      {pending && (
        <>
          {pending.unmatched?.length ? (
            <p className="text-[11px] text-muted mt-1">Not loads here: {pending.unmatched.join(', ')}</p>
          ) : null}
          <PendingCard project={project} component={component} pending={pending} power />
          {advanced && <RefinePanel project={project} pending={pending} power onRefined={setPending} />}
        </>
      )}
    </div>
  )
}
