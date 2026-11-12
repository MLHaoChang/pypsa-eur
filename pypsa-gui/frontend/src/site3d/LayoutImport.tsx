// SC-06-layout: import a layout drawing or GeoJSON as an `import_layout` proposal.
// The drawing stays in the browser (object URL): the server gets only its declared
// size, the scale bar, the anchor/rotation and the traced outlines. The result is
// the panel's ordinary preview: nothing is saved until Apply, and the rest of the
// scene stays (buildings merge by id).
import { useEffect, useRef, useState } from 'react'
import { Dialog } from '../components/Dialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { LayoutInputs, RasterCalibration, TracedFootprint } from '../api/siteScene'
import { readLayoutFile, LayoutFileRefused, type LayoutFile } from './layoutFile'
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from './buttonStyles'

type Pt = [number, number]
type Tool = 'scale' | 'anchor' | 'trace'
type Unit = RasterCalibration['scale']['unit']
type Raster = Extract<LayoutFile, { kind: 'raster' }>

const TOOL_WORDS: Record<Tool, string> = { scale: 'Scale: mark two points', anchor: 'Anchor: mark one point', trace: 'Trace: mark corners' }
const FIELD = 'w-16 rounded border border-border bg-bg px-1 py-0.5 text-[11px] text-text'

/** A traced building's name problem, or null: never empty, never shared (UX 19). */
export function nameError(footprints: TracedFootprint[], i: number): string | null {
  const name = footprints[i].id.trim()
  if (!name) return 'Name: enter a name'
  // Only the later of two equal names is flagged: the one just typed or traced.
  return footprints.some((f, j) => j < i && f.id.trim() === name) ? 'Name: another building already has this name' : null
}

/** Why Preview is off, in words, or null when the drawing can be previewed. */
export function previewReason(scale: Pt[], distance: number, anchor: Pt | null, footprints: TracedFootprint[], tracing: boolean): string | null {
  if (scale.length < 2) return 'Mark the two ends of a known distance with the Scale tool'
  if (!(distance > 0) || !Number.isFinite(distance)) return 'Enter the scale distance'
  if (!anchor) return 'Mark the anchor point with the Anchor tool'
  if (tracing) return 'Finish or undo the outline you are tracing'
  if (footprints.length === 0) return 'Trace at least one building'
  if (footprints.some((_, i) => nameError(footprints, i))) return 'Give every building its own name'
  return null
}

/** The traced footprints still valid to send: at least three corners each. */
export function layoutReady(scale: Pt[], distance: number, anchor: Pt | null, footprints: TracedFootprint[]): boolean {
  return previewReason(scale, distance, anchor, footprints, false) === null && footprints.every(f => f.ring_px.length >= 3)
}

export default function LayoutImport({ disabled, onPropose }: { disabled: boolean; onPropose: (inputs: LayoutInputs) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [geojson, setGeojson] = useState<{ name: string; document: Record<string, unknown>; count: number } | null>(null)
  const [raster, setRaster] = useState<(Raster & { url: string }) | null>(null)

  const choose = async (file: File | undefined) => {
    setError(null)
    if (!file) return
    try {
      const read = await readLayoutFile(file)
      if (read.kind === 'geojson') {
        const feats = (read.document as { features?: unknown }).features
        setGeojson({ name: file.name, document: read.document, count: Array.isArray(feats) ? feats.length : 1 })
      } else {
        setRaster({ ...read, url: URL.createObjectURL(file) })
      }
    } catch (e) {
      setError(e instanceof LayoutFileRefused ? e.message : 'The file could not be read')
    } finally {
      if (input.current) input.current.value = ''
    }
  }
  const closeRaster = () => { if (raster) URL.revokeObjectURL(raster.url); setRaster(null) }
  useEffect(() => () => { if (raster) URL.revokeObjectURL(raster.url) }, [raster])

  return (
    <div className="flex flex-col gap-1" data-testid="layout-import">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <button type="button" data-testid="layout-import-choose" disabled={disabled} onClick={() => input.current?.click()}
          className={`${SECONDARY_BUTTON} shrink-0 whitespace-nowrap`}>Import layout…</button>
        <span className="text-[11px] text-muted">GeoJSON, or a PNG/JPEG/WebP drawing with a scale</span>
        <input ref={input} type="file" className="hidden" data-testid="layout-import-file"
          accept=".geojson,.json,application/geo+json,image/png,image/jpeg,image/webp"
          onChange={e => void choose(e.target.files?.[0])} />
      </div>
      {error && <span role="alert" className="text-[11px] text-danger" data-testid="layout-import-error">{error}</span>}
      {geojson && (
        <div className="flex flex-col gap-1" data-testid="layout-import-geojson">
          <span className="text-[11px] text-text">{geojson.name} · {geojson.count} {geojson.count === 1 ? 'feature' : 'features'} · WGS84 longitude/latitude</span>
          <div className="flex gap-2">
            <button type="button" className={PRIMARY_BUTTON} disabled={disabled} data-testid="layout-import-geojson-preview"
              onClick={() => void onPropose({ layout: { kind: 'geojson', document: geojson.document } })
                .then(() => setGeojson(null), (e: Error) => setError(e.message))}>Preview</button>
            <button type="button" className={SECONDARY_BUTTON} onClick={() => setGeojson(null)}>Cancel</button>
          </div>
        </div>
      )}
      {raster && <TraceDialog raster={raster} disabled={disabled} onClose={closeRaster}
        onPropose={async inputs => { await onPropose(inputs); closeRaster() }} />}
    </div>
  )
}

function TraceDialog({ raster, disabled, onClose, onPropose }: {
  raster: Raster & { url: string }; disabled: boolean; onClose: () => void; onPropose: (inputs: LayoutInputs) => Promise<void>
}) {
  const { width_px: w, height_px: h } = raster
  const [tool, setTool] = useState<Tool>('scale')
  const [scale, setScale] = useState<Pt[]>([])
  const [distance, setDistance] = useState(10)
  const [unit, setUnit] = useState<Unit>('m')
  const [anchor, setAnchor] = useState<Pt | null>(null)
  const [east, setEast] = useState(0)
  const [north, setNorth] = useState(0)
  const [rotation, setRotation] = useState(0)
  const [traced, setTraced] = useState<TracedFootprint[]>([])
  const [ring, setRing] = useState<Pt[]>([])
  const [cursor, setCursor] = useState<Pt>([Math.round(w / 2), Math.round(h / 2)])
  const [error, setError] = useState<string | null>(null)
  const svg = useRef<SVGSVGElement>(null)
  // A refusal is about the inputs it was given: any change clears it until the next Preview.
  useEffect(() => { setError(null) }, [scale, distance, unit, anchor, east, north, rotation, traced])

  const place = (p: Pt) => {
    setCursor(p)
    if (tool === 'scale') setScale(s => (s.length >= 2 ? [p] : [...s, p]))
    else if (tool === 'anchor') setAnchor(p)
    else setRing(r => [...r, p])
  }
  const finish = () => {
    if (ring.length < 3) return
    setTraced(t => {
      let k = t.length + 1
      while (t.some(f => f.id === `building ${k}`)) k++
      return [...t, { id: `building ${k}`, ring_px: ring }]
    })
    setRing([])
  }
  const [confirmClose, setConfirmClose] = useState(false)
  const requestClose = () => (traced.length || ring.length ? setConfirmClose(true) : onClose())
  const onClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = svg.current!.getBoundingClientRect()
    const x = Math.round(((e.clientX - box.left) / box.width) * w), y = Math.round(((e.clientY - box.top) / box.height) * h)
    place([Math.min(Math.max(x, 0), w), Math.min(Math.max(y, 0), h)])
  }
  // Keyboard: arrows move the crosshair (Shift: 10×), Enter or Space places a point.
  const onKey = (e: React.KeyboardEvent) => {
    const step = (e.shiftKey ? 10 : 1) * Math.max(1, Math.round(Math.max(w, h) / 500))
    const moves: Record<string, Pt> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    if (moves[e.key]) {
      e.preventDefault()
      setCursor(([x, y]) => [Math.min(Math.max(x + moves[e.key][0], 0), w), Math.min(Math.max(y + moves[e.key][1], 0), h)])
    } else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); place(cursor) }
  }
  const reason = previewReason(scale, distance, anchor, traced, ring.length > 0)
  const ready = reason === null
  const preview = async () => {
    setError(null)
    try {
      await onPropose({ layout: { kind: 'raster', image: { width_px: w, height_px: h, media_type: raster.media_type },
        calibration: { scale: { a_px: scale[0], b_px: scale[1], distance, unit }, anchor_px: anchor!, anchor_local: [east, north], rotation_deg: rotation },
        footprints: traced.map(f => ({ ...f, id: f.id.trim() })) } })
    } catch (e) { setError(e instanceof Error ? e.message : 'The preview was refused') }
  }
  const r = Math.max(w, h) / 200          // marker radius in image pixels
  const crosshair = `M${cursor[0] - 3 * r},${cursor[1]}H${cursor[0] + 3 * r}M${cursor[0]},${cursor[1] - 3 * r}V${cursor[1] + 3 * r}`
  const poly = (pts: Pt[]) => pts.map(p => p.join(',')).join(' ')
  const num = (set: (v: number) => void) => (e: React.ChangeEvent<HTMLInputElement>) => set(e.target.value === '' ? NaN : Number(e.target.value))

  return (
    <>
    <Dialog open onClose={requestClose} title="Trace a layout drawing" z={950}
      panelClassName="bg-bg rounded-lg shadow-2xl w-[64rem] max-w-[96vw] max-h-[90vh] flex flex-col overflow-hidden border border-border">
      <div className="flex min-h-0 flex-1 gap-3 p-3 text-[12px]" data-testid="layout-trace">
        <div className="relative min-w-0 flex-1 overflow-auto">
          <svg ref={svg} viewBox={`0 0 ${w} ${h}`} className="block w-full h-auto cursor-crosshair focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            tabIndex={0} role="application" aria-label={`Drawing, ${TOOL_WORDS[tool]}. Arrow keys move the crosshair, Enter places a point`}
            data-testid="layout-trace-canvas" onClick={onClick} onKeyDown={onKey}>
            <image href={raster.url} width={w} height={h} />
            {traced.map(f => <polygon key={f.id} points={poly(f.ring_px)} className="fill-accent/20 stroke-accent" strokeWidth={r / 3} />)}
            {ring.length > 0 && <polyline points={poly(ring)} fill="none" className="stroke-warn" strokeWidth={r / 3} />}
            {scale.length === 2 && <line x1={scale[0][0]} y1={scale[0][1]} x2={scale[1][0]} y2={scale[1][1]} className="stroke-danger" strokeWidth={r / 2} />}
            {scale.map((p, i) => <circle key={`s${i}`} cx={p[0]} cy={p[1]} r={r} className="fill-danger" />)}
            {/* Markers draw a background-colour halo under a text-colour stroke, so they read on any drawing in both themes. */}
            {anchor && <>
              <circle cx={anchor[0]} cy={anchor[1]} r={r * 1.4} className="fill-none stroke-bg" strokeWidth={r} />
              <circle cx={anchor[0]} cy={anchor[1]} r={r * 1.4} className="fill-none stroke-text" strokeWidth={r / 2} />
            </>}
            <path d={crosshair} className="stroke-bg" strokeWidth={r / 1.5} />
            <path d={crosshair} className="stroke-text" strokeWidth={r / 4} />
          </svg>
        </div>
        <div className="flex w-64 shrink-0 flex-col gap-2 overflow-y-auto">
          <span className="text-[11px] text-muted">The drawing stays on this computer: only the scale, the placement and the traced outlines are sent.</span>
          <div role="radiogroup" aria-label="Tool" className="flex flex-col gap-0.5">
            {(Object.keys(TOOL_WORDS) as Tool[]).map(t => (
              <label key={t} className="flex items-center gap-1 text-[11px]">
                <input type="radio" name="layout-tool" checked={tool === t} onChange={() => setTool(t)} data-testid={`layout-tool-${t}`} />
                {TOOL_WORDS[t]}
              </label>
            ))}
          </div>
          <fieldset className="flex flex-col gap-1">
            <legend className="text-[11px] font-medium text-text">Scale {scale.length < 2 && <span className="font-normal text-warn">· mark 2 points ({scale.length}/2)</span>}</legend>
            <label className="flex items-center gap-1 text-[11px]">Distance ({unit})
              <input type="number" min={0} step="any" className={FIELD} value={Number.isNaN(distance) ? '' : distance} onChange={num(setDistance)} data-testid="layout-scale-distance" />
              <select className={FIELD} value={unit} onChange={e => setUnit(e.target.value as Unit)} data-testid="layout-scale-unit" aria-label="Unit">
                <option value="m">m</option><option value="mm">mm</option><option value="ft">ft</option>
              </select>
            </label>
            {!(distance > 0) && <span className="text-[11px] text-danger">Enter a distance above 0</span>}
          </fieldset>
          <fieldset className="flex flex-col gap-1">
            <legend className="text-[11px] font-medium text-text">Placement {!anchor && <span className="font-normal text-warn">· mark the anchor</span>}</legend>
            <span className="text-[11px] text-muted">The anchor sits this far from the site origin:</span>
            <div className="flex gap-2">
              <label className="flex items-center gap-1 text-[11px]">East <input type="number" step="any" className={FIELD} value={Number.isNaN(east) ? '' : east} onChange={num(setEast)} data-testid="layout-anchor-east" /> m</label>
              <label className="flex items-center gap-1 text-[11px]">North <input type="number" step="any" className={FIELD} value={Number.isNaN(north) ? '' : north} onChange={num(setNorth)} data-testid="layout-anchor-north" /> m</label>
            </div>
            <label className="flex items-center gap-1 text-[11px]">Drawing up points <input type="number" step="any" className={FIELD} value={Number.isNaN(rotation) ? '' : rotation} onChange={num(setRotation)} data-testid="layout-rotation" />° from north</label>
          </fieldset>
          <fieldset className="flex flex-col gap-1">
            <legend className="text-[11px] font-medium text-text">Buildings ({traced.length})</legend>
            {ring.length > 0 && (
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-warn">Outline: {ring.length} corners</span>
                <button type="button" className="text-accent hover:underline disabled:text-muted" disabled={ring.length < 3} onClick={finish} data-testid="layout-trace-finish">Finish outline</button>
                <button type="button" className="text-muted hover:text-text" onClick={() => setRing(corners => corners.slice(0, -1))}>Undo corner</button>
              </div>
            )}
            {traced.map((f, i) => (
              <div key={i} className="flex flex-col gap-0.5" data-testid={`layout-traced-${i}`}>
              <div className="flex items-center gap-1 text-[11px]">
                <input aria-label="Name" className="w-24 rounded border border-border bg-bg px-1 py-0.5 text-[11px] text-text" value={f.id}
                  onChange={e => setTraced(t => t.map((x, j) => (j === i ? { ...x, id: e.target.value } : x)))} />
                <input aria-label="Height in metres" type="number" min={0} step="any" placeholder="6" className={FIELD}
                  value={f.height_m ?? ''} onChange={e => setTraced(t => t.map((x, j) => (j === i ? { ...x, height_m: e.target.value === '' ? undefined : Number(e.target.value) } : x)))} /> m
                <button type="button" className="text-muted hover:text-danger" onClick={() => setTraced(t => t.filter((_, j) => j !== i))}>Remove</button>
              </div>
              {nameError(traced, i) && <span className="text-[11px] text-danger" data-testid={`layout-traced-${i}-error`}>{nameError(traced, i)}</span>}
              </div>
            ))}
            {traced.length > 0 && <span className="text-[11px] text-muted">No height: 6 m, shown as estimated.</span>}
          </fieldset>
          {error && <span role="alert" className="text-[11px] text-danger">{error}</span>}
          <div className="mt-auto flex gap-2">
            <button type="button" className={PRIMARY_BUTTON} disabled={disabled || !ready} onClick={() => void preview()} data-testid="layout-trace-preview">Preview</button>
            <button type="button" className={SECONDARY_BUTTON} onClick={requestClose}>Cancel</button>
          </div>
          {reason && <span className="text-[11px] text-muted" data-testid="layout-trace-reason">{reason}</span>}
        </div>
      </div>
    </Dialog>
    <ConfirmDialog open={confirmClose} title="Discard the traced buildings?" confirmLabel="Discard" danger
      message="The outlines you traced are not saved. The drawing stays on this computer."
      onConfirm={() => { setConfirmClose(false); onClose() }} onCancel={() => setConfirmClose(false)} />
    </>
  )
}
