// Readiness of the wind/solar generators in the Time Series list (RP-04a):
// a badge per row and the missing-coordinates banner. The badge is read-only.
// The banner sets a bus's longitude and latitude inline through the normal bus
// edit (RP-04), or, without a save handler, opens the bus's own coordinates.
import { useState } from 'react'
import { errorText, type ReadinessRow } from '../../api/profiles'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'

const SOURCE_LABEL: Record<string, string> = { synthetic: 'Synthetic', atlite: 'ERA5' }

const STALE_WORDS: Record<NonNullable<ReadinessRow['stale_reason']>, string> = {
  horizon_changed: 'the model’s hours changed',
  bus_moved: 'the bus moved',
}

export function readinessBadge(row: ReadinessRow): { text: string; tone: 'ok' | 'warn' | 'muted' } {
  if (row.coord_source === 'missing' && row.profile !== 'uploaded' && row.profile !== 'unrecorded' && row.profile !== 'network_native') {
    return { text: 'No location', tone: 'warn' }
  }
  switch (row.profile) {
    case 'uploaded': return { text: 'Measured file', tone: 'ok' }
    case 'unrecorded': return { text: 'Source not recorded · kept', tone: 'muted' }
    case 'network_native': return { text: 'From the network', tone: 'ok' }
    case 'generated': {
      const year = row.provenance?.weather_year
      const base = `${SOURCE_LABEL[row.provenance?.source ?? ''] ?? row.provenance?.source ?? 'Generated'}${year != null ? ` · ${year}` : ''}`
      return row.stale_reason
        ? { text: `${base} · out of date: ${STALE_WORDS[row.stale_reason]}`, tone: 'warn' }
        : { text: base, tone: 'ok' }
    }
    default: return { text: 'Ready to generate', tone: 'muted' }
  }
}

export function ReadinessBadge({ row }: { row: ReadinessRow }) {
  const b = readinessBadge(row)
  const color = b.tone === 'ok' ? 'text-success' : b.tone === 'warn' ? 'text-amber-600' : 'text-muted'
  return (
    <span data-testid={`readiness-${row.generator}`} className={`text-[10px] ${color}`}>{b.text}</span>
  )
}

/** A bus's coordinates live on the bus (Properties); Guided opens them in
 *  place over the card, Expert in the section. */
function openBusCoordinates(bus: string) {
  const target = { section: 'connection' as const, panel: null, component: 'Bus', entity: bus, field: 'x' }
  if (useUIStore.getState().uiMode === 'guided') openFlowTargetInPlace(target)
  else openFlowTarget(target)
}

/** Inline longitude/latitude of one bus, saved through the normal bus edit. */
function InlineCoordinates({ bus, onSave, onDone }: {
  bus: string; onSave: (bus: string, x: number, y: number) => Promise<void>; onDone: () => void
}) {
  const [x, setX] = useState('')
  const [y, setY] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const lon = Number(x), lat = Number(y)
  const valid = x.trim() !== '' && y.trim() !== '' && Math.abs(lon) <= 180 && Math.abs(lat) <= 90
    && !(lon === 0 && lat === 0)
  const save = async () => {
    setErr(null)
    setSaving(true)
    try { await onSave(bus, lon, lat); onDone() } catch (e) { setErr(errorText(e)); setSaving(false) }
  }
  const field = 'w-20 border border-border rounded px-1 py-0.5 bg-bg'
  return (
    <span className="flex flex-wrap items-center gap-1" data-testid={`coord-form-${bus}`}>
      <label>Longitude (°E) <input type="number" step="any" value={x} onChange={e => setX(e.target.value)}
        data-testid={`coord-x-${bus}`} className={field} /></label>
      <label>Latitude (°N) <input type="number" step="any" value={y} onChange={e => setY(e.target.value)}
        data-testid={`coord-y-${bus}`} className={field} /></label>
      <button type="button" className="text-accent hover:underline disabled:opacity-40"
        disabled={!valid || saving} data-testid={`coord-save-${bus}`} onClick={() => { void save() }}>
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button type="button" className="text-muted hover:underline" onClick={onDone}>Cancel</button>
      {!valid && x.trim() !== '' && y.trim() !== '' && (
        <span className="text-warn">Longitude runs from −180 to 180 and latitude from −90 to 90; 0, 0 is not a location.</span>
      )}
      {err && <span className="text-warn" data-testid={`coord-error-${bus}`}>{err}</span>}
    </span>
  )
}

/** RP-18: series with no record of where they came from (a project saved before the
 *  import stamped them, or a raw upload). A default never replaces them. This lists
 *  every one, with no cut-off, and one button says they are the network's own; the
 *  write sends the fingerprint shown here, so a series changed since is refused. */
export function UnrecordedSourceBanner({ rows, onConfirm }: {
  rows: ReadinessRow[]; onConfirm: (items: { component: string; digest: string }[]) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const shown = rows.filter(r => r.profile === 'unrecorded')
  if (shown.length === 0) return null
  // Never stamp a series the list cannot show in full (#1079): no digest, no button.
  const items = shown.flatMap(r => (r.digest ? [{ component: r.generator, digest: r.digest }] : []))
  const complete = items.length === shown.length
  const go = async () => {
    setErr(null); setBusy(true)
    try { await onConfirm(items) } catch (e) { setErr(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <div data-testid="unrecorded-source-banner"
      className="px-3 py-2 border-b border-border/40 bg-amber-500/10 text-[11px]">
      <p className="font-medium text-text">
        {shown.length === 1 ? 'One series has' : `${shown.length} series have`} no record of where {shown.length === 1 ? 'it' : 'they'} came from.
        They are kept as they are: a default never replaces them.
      </p>
      <ul className="mt-1 space-y-0.5">
        {shown.map(r => (
          <li key={r.generator} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-text">{r.generator}</span>
            <span className="text-muted">wind/solar availability · fingerprint {r.digest ?? 'unavailable'}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-muted">
        If they came with the network (not a file you uploaded), say so and a default can replace them. This changes only the record, one undo reverts it.
      </p>
      <button type="button" className="mt-1 text-accent hover:underline disabled:opacity-40"
        disabled={!complete || busy} data-testid="confirm-unrecorded" onClick={() => { void go() }}>
        {busy ? 'Saving…' : shown.length === 1 ? 'This one is the network’s own' : `These ${shown.length} are the network’s own`}
      </button>
      {err && <span className="ml-2 text-warn" data-testid="confirm-unrecorded-error">{err}</span>}
    </div>
  )
}

export function MissingCoordinatesBanner({ rows, onUploadInstead, onSetCoordinates }: {
  rows: ReadinessRow[]; onUploadInstead?: () => void
  onSetCoordinates?: (bus: string, x: number, y: number) => Promise<void>
}) {
  const [editing, setEditing] = useState<string | null>(null)
  const missing = rows.filter(r => r.coord_source === 'missing')
  if (missing.length === 0) return null
  return (
    <div data-testid="missing-coordinates-banner"
      className="px-3 py-2 border-b border-border/40 bg-amber-500/10 text-[11px]">
      <p className="font-medium text-text">
        {missing.length === 1 ? 'One generator has' : `${missing.length} generators have`} no location: a weather profile needs to know where it is.
      </p>
      <ul className="mt-1 space-y-0.5">
        {missing.map(r => (
          <li key={r.generator} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-text">{r.generator}</span>
            <span className="text-muted">on bus {r.bus}</span>
            {onSetCoordinates && editing === r.generator
              ? <InlineCoordinates bus={r.bus} onSave={onSetCoordinates} onDone={() => setEditing(null)} />
              : (
                <button type="button" className="text-accent hover:underline"
                  data-testid={`set-coordinates-${r.generator}`}
                  onClick={() => onSetCoordinates ? setEditing(r.generator) : openBusCoordinates(r.bus)}>
                  {onSetCoordinates ? 'Set coordinates' : 'Set the bus coordinates ›'}
                </button>
              )}
          </li>
        ))}
      </ul>
      {onUploadInstead && (
        <button type="button" className="mt-1 text-accent hover:underline"
          data-testid="upload-instead" onClick={onUploadInstead}>
          Upload a measured file instead ›
        </button>
      )}
    </div>
  )
}
