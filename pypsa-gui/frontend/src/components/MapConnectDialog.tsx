import { useEffect, useState } from 'react'
import { X as XIcon } from 'lucide-react'

// The map's connect dialog (plan M4): two bus clicks → a Line or a
// Transformer between them, the same two mutations the schematic's connect
// mode issues (`createLine` with r: 0, `createTransformer` with the declared
// voltage step). The one difference from the schematic's dialog is the
// line's default length: the CHORD between the two buses (plan M2's
// `routeLengthKm`) rather than '1'. Kept map-side rather than lifted out of
// TopologyCanvas, which another increment owns; the field set is the same so
// the two read as one dialog.

export type ConnectionKind = 'line' | 'transformer'

export interface LineConnectionParams {
  kind: 'line'
  name: string; carrier: 'AC' | 'DC'; s_nom: number; x: number; length: number
}
export interface TransformerConnectionParams {
  kind: 'transformer'
  name: string; type: string
  v_nom_0: number; v_nom_1: number
  s_nom: number; x: number; tap_ratio: number
}
export type ConnectionParams = LineConnectionParams | TransformerConnectionParams

interface Props {
  source: string; target: string
  sourceVNom: number; targetVNom: number
  /** The chord between the buses, km; the Line's default length. */
  defaultLengthKm: number
  anchorX: number; anchorY: number
  onConfirm: (params: ConnectionParams) => void
  onClose: () => void
}

const VNOM_TOL_KV = 0.5

export default function MapConnectDialog({
  source, target, sourceVNom, targetVNom, defaultLengthKm, anchorX, anchorY, onConfirm, onClose,
}: Props) {
  const [kind, setKind] = useState<ConnectionKind>('line')
  const [name, setName] = useState('')
  const [carrier, setCarrier] = useState<'AC' | 'DC'>('AC')
  const [sNom, setSNom] = useState('100')
  const [reactance, setReactance] = useState('0.1')
  const [length, setLength] = useState(() => (Number.isFinite(defaultLengthKm) && defaultLengthKm > 0 ? String(Math.round(defaultLengthKm * 1000) / 1000) : '1'))
  const [trVNom0, setTrVNom0] = useState(sourceVNom > 0 ? String(sourceVNom) : '')
  const [trVNom1, setTrVNom1] = useState(targetVNom > 0 ? String(targetVNom) : '')
  const [trSNom, setTrSNom] = useState('100')
  const [trX, setTrX] = useState('0.1')
  const [trTapRatio, setTrTapRatio] = useState('1.0')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Either orientation is acceptable; the backend re-validates (_VNOM_TOL_KV).
  const v0 = parseFloat(trVNom0), v1 = parseFloat(trVNom1)
  const sameOrientation = Math.abs(sourceVNom - v0) <= VNOM_TOL_KV && Math.abs(targetVNom - v1) <= VNOM_TOL_KV
  const swapped = Math.abs(sourceVNom - v1) <= VNOM_TOL_KV && Math.abs(targetVNom - v0) <= VNOM_TOL_KV
  const voltageOK = !isFinite(v0) || !isFinite(v1) || v0 <= 0 || v1 <= 0 || sameOrientation || swapped
  const disableSubmit = kind === 'transformer' && !voltageOK

  const W = 260
  const left = Math.max(8, Math.min(anchorX + 8, window.innerWidth - W - 8))
  const top = Math.max(8, Math.min(anchorY - 20, window.innerHeight - 420))
  const inp = 'w-full rounded border border-border px-2 py-1 text-xs outline-none focus:border-accent focus:ring-1 focus:ring-accent/30'

  const submit = () => {
    if (kind === 'line') {
      onConfirm({
        kind: 'line', name: name.trim() || `${source}–${target}`, carrier,
        s_nom: parseFloat(sNom) || 100, x: parseFloat(reactance) || 0.1,
        length: parseFloat(length) || (defaultLengthKm > 0 ? defaultLengthKm : 1),
      })
    } else {
      onConfirm({
        kind: 'transformer', name: name.trim() || `${source}–${target}`, type: '',
        v_nom_0: v0 || 0, v_nom_1: v1 || 0,
        s_nom: parseFloat(trSNom) || 100, x: parseFloat(trX) || 0.1, tap_ratio: parseFloat(trTapRatio) || 1.0,
      })
    }
  }

  return (
    <div
      role="dialog"
      aria-label={kind === 'line' ? 'New Line' : 'New Transformer'}
      className="fixed z-[9999] bg-bg rounded-[10px] shadow-xl select-none border border-border"
      style={{ width: W, left, top, padding: 14 }}
      onMouseDown={e => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold text-text">{kind === 'line' ? 'New Line' : 'New Transformer'}</span>
        <button onClick={onClose} aria-label="Close new connection dialog" className="text-muted hover:text-text"><XIcon size={13} /></button>
      </div>
      <div className="text-[10px] text-muted mb-2.5 leading-snug">
        <span className="font-medium text-text">{source}</span>{sourceVNom > 0 && <span> ({sourceVNom} kV)</span>}
        {' → '}
        <span className="font-medium text-text">{target}</span>{targetVNom > 0 && <span> ({targetVNom} kV)</span>}
      </div>
      <div className="flex items-center gap-2 mb-2.5 p-0.5 bg-panel rounded">
        {(['line', 'transformer'] as const).map(k => (
          <button key={k} type="button" onClick={() => setKind(k)} aria-pressed={kind === k}
            className={`flex-1 py-1 text-[11px] font-medium rounded transition-colors ${kind === k ? 'bg-bg text-text shadow-sm' : 'text-muted hover:text-text'}`}>
            {k === 'line' ? 'Line' : 'Transformer'}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted">Name</span>
          <input className={inp} placeholder={`${source}–${target}`} value={name} onChange={e => setName(e.target.value)} />
        </label>
        {kind === 'line' ? (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Carrier</span>
              <select className={inp} value={carrier} onChange={e => setCarrier(e.target.value as 'AC' | 'DC')}>
                <option value="AC">AC</option><option value="DC">DC</option>
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">s_nom (MVA)</span>
              <input className={inp} type="number" step="any" value={sNom} onChange={e => setSNom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Reactance x (pu)</span>
              <input className={inp} type="number" step="any" value={reactance} onChange={e => setReactance(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Length (km)</span>
              <input className={inp} type="number" step="any" value={length} onChange={e => setLength(e.target.value)} title="Defaults to the great-circle chord between the two buses" />
            </label>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">v_nom₀ (kV)</span>
              <input className={inp} type="number" step="any" value={trVNom0} onChange={e => setTrVNom0(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">v_nom₁ (kV)</span>
              <input className={inp} type="number" step="any" value={trVNom1} onChange={e => setTrVNom1(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">s_nom (MVA)</span>
              <input className={inp} type="number" step="any" value={trSNom} onChange={e => setTrSNom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Reactance x (pu)</span>
              <input className={inp} type="number" step="any" value={trX} onChange={e => setTrX(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5 col-span-2">
              <span className="text-[10px] text-muted">Tap ratio</span>
              <input className={inp} type="number" step="any" value={trTapRatio} onChange={e => setTrTapRatio(e.target.value)} />
            </label>
            {!voltageOK && (
              <p className="col-span-2 text-[10px] text-warn">The voltage step must match the two buses ({sourceVNom} kV and {targetVNom} kV) in one orientation or the other.</p>
            )}
          </div>
        )}
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button type="button" onClick={onClose} className="px-3 py-1 text-xs rounded border border-border text-text hover:bg-border/30">Cancel</button>
        <button type="button" onClick={submit} disabled={disableSubmit} className="px-3 py-1 text-xs rounded bg-accent text-white hover:opacity-90 disabled:opacity-40">
          {kind === 'line' ? 'Add line' : 'Add transformer'}
        </button>
      </div>
    </div>
  )
}
