import { useEffect, useState } from 'react'
import { useUIStore } from '../store/uiStore'
import { useSitesStore, flushPendingSitesToServer } from '../site3d/sitesStore'
import { FOOTPRINT_CLASSES, assertFootprintValid } from '../site3d/footprints'
import type { AssetFootprint } from '../site3d/types'
import { evaluateMutation, readOnlyMessage } from '../utils/mutationGuard'

interface Props { componentClass: string; name: string }

/** The same optional editor in Guided/Expert properties and the Site view. */
export default function AssetFootprintEditor({ componentClass, name, inPanel }: Props & { inPanel?: boolean }) {
  const [open, setOpen] = useState(false)
  const project = useUIStore(s => s.currentProject)
  if (!FOOTPRINT_CLASSES.has(componentClass)) return null
  return <details className={`rounded border border-border bg-bg p-2.5 text-[11px] ${inPanel ? 'mx-3 block' : 'max-w-72'}`} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary className="cursor-pointer font-semibold text-text">Footprint (optional)</summary>
    {open && <FootprintFields key={`${project}:${componentClass}:${name}`} componentClass={componentClass} name={name} />}
  </details>
}

function FootprintFields({ componentClass, name }: Props) {
  const project = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const reason = useUIStore(s => s.readOnlyReason)
  const key = `${componentClass}:${name}`
  const doc = useSitesStore(s => s.docFor(project))
  const loaded = useSitesStore(s => s.loaded[project ?? '__local__'])
  const loadFailed = useSitesStore(s => s.loadFailures[project ?? '__local__'])
  const value = doc.asset_footprints?.[key]
  const [width, setWidth] = useState('')
  const [depth, setDepth] = useState('')
  const [height, setHeight] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { void useSitesStore.getState().ensureLoaded(project) }, [project])
  useEffect(() => {
    setWidth(value ? String(value.width_m) : '')
    setDepth(value ? String(value.depth_m) : '')
    setHeight(value?.height_m === undefined ? '' : String(value.height_m))
    setError(''); setMessage('')
  }, [project, key, value])
  useEffect(() => { if (readOnly) setMessage('') }, [readOnly])
  const blocked = readOnly || !loaded || !!loadFailed || busy
  async function save(reset = false) {
    const current = useUIStore.getState()
    if (blocked || !evaluateMutation(current.readOnly, current.readOnlyReason).allowed) return
    let next: AssetFootprint | null = null
    if (!reset) {
      next = { width_m: Number(width), depth_m: Number(depth), ...(height.trim() ? { height_m: Number(height) } : {}) }
      try { assertFootprintValid(key, next) }
      catch (e) { setError((e as Error).message); return }
    }
    setError(''); setBusy(true)
    useSitesStore.getState().setAssetFootprint(project, key, next)
    const result = await flushPendingSitesToServer(project)
    const afterSave = useUIStore.getState()
    setMessage(afterSave.readOnly || afterSave.currentProject !== project ? '' : result.status === 'server' ? 'Saved to project' : result.status === 'refused'
      ? 'Not saved: project is read-only' : 'Local draft: not saved to project')
    setBusy(false)
  }
  const area = Number(width) * Number(depth)
  return <div className="mt-2 space-y-2">
    <p className="text-muted">{value ? 'User-entered equipment envelope; simple 3D proxy.' : 'Library estimate: detailed footprint not provided; actual dimensions may differ.'}</p>
    <p className="text-muted">Total envelope for this asset, not per module. Geometry only; ratings and results stay unchanged. Review after capacity changes.</p>
    {loadFailed && <p role="alert">Could not load site metadata. <button type="button" className="underline" onClick={() => void useSitesStore.getState().ensureLoaded(project, true)}>Retry loading</button></p>}
    {readOnly && <p role="status">{readOnlyMessage(reason) ?? 'Read-only: editing is unavailable.'}</p>}
    {!loaded && <p role="status">Loading site metadata…</p>}
    {busy && <p role="status">Saving footprint…</p>}
    <fieldset disabled={blocked} title={readOnly ? readOnlyMessage(reason) ?? 'Read-only' : undefined} className="space-y-1">
      {([['Width (m)', width, setWidth], ['Depth (m)', depth, setDepth], ['Height (m, optional)', height, setHeight]] as const).map(([label, v, setter]) =>
        <label key={label} className="flex items-center justify-between gap-2">{label}
          <input aria-label={label} type="number" step="any" value={v} onChange={e => { setter(e.target.value); setMessage('') }}
            className="w-24 rounded border border-border bg-panel px-1 text-text disabled:opacity-50" />
        </label>)}
      {width.trim() && depth.trim() && Number(width) > 0 && Number(depth) > 0 && Number.isFinite(area) && <p>Envelope area: {area < 0.1 ? '<0.1' : area.toLocaleString('en-US', { maximumFractionDigits: 1 })} m²</p>}
      {!height.trim() && <p className="text-muted">Height omitted: library height estimate.</p>}
      <p className="text-muted">Clearances and safety compliance require separate checks.</p>
      <div className="flex gap-2">
        <button type="button" className="rounded border border-border px-1.5 py-0.5 disabled:opacity-50" onClick={() => void save()}>Save footprint</button>
        <button type="button" disabled={!value} className="rounded border border-border px-1.5 py-0.5 disabled:opacity-50" onClick={() => void save(true)}>Use library estimate</button>
      </div>
    </fieldset>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {message && <p role="status">{message}</p>}
  </div>
}
