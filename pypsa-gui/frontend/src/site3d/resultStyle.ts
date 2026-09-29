// How a site object looks as a function of its state (Phase 2 spec E10,
// §6.2, §6.4). Pure, no three: the renderer asks, this module decides —
// glow precedence, and what a result looks like (gauge, glow, spin, flow)
// with the label that says it in numbers (never colour only).
import { DEFAULT_LIBRARY, type AssetType } from './assetLibrary'
import { loadingColor, socColor } from '../components/CanvasResultsContext'

export const OUTSIDE_COLOR = '#dc2626'
export const HOVER_COLOR = '#fde68a'

export interface ObjectState { selected: boolean; hovered: boolean; outside: boolean }

/**
 * The emissive glow, with a fixed precedence: selected > hovered > outside
 * the boundary > a result. Hover beats the warning because it is transient
 * and the hover label already says "outside the boundary" (WP2 review). `result` is a 0..1 share (output, load);
 * it glows at most half as bright as a selection.
 */
export function emissiveFor(s: ObjectState, color: string, result?: number): { color: string; intensity: number } {
  if (s.selected) return { color, intensity: 0.6 }
  if (s.hovered) return { color: HOVER_COLOR, intensity: 0.25 }
  if (s.outside) return { color: OUTSIDE_COLOR, intensity: 0.45 }
  if (result != null && Number.isFinite(result)) return { color, intensity: Math.min(1, Math.max(0, result)) * 0.5 }
  return { color: '#000000', intensity: 0 }
}

// ── Results ─────────────────────────────────────────────────────────────────

/**
 * One asset at one snapshot (useSiteResults builds these). Powers in MW;
 * capacities are the share denominators (capacity.ts), null = no share.
 */
export type AssetState =
  /** Generators: output. */
  | { kind: 'output'; mw: number; cap: number | null }
  /** StorageUnits and Stores: + discharging / − charging; energy in MWh. */
  | { kind: 'storage'; mw: number; energy: number | null; energyCap: number | null }
  /** Loads: consumption, against the load's peak. */
  | { kind: 'load'; mw: number; peak: number | null }
  /** Conversion Links (electrolyser, heat pump, CHP, …): MW in at bus0. */
  | { kind: 'link'; mw: number; cap: number | null }
  /** Lines, Transformers and feeder Links: p0 at bus0 (+ = bus0 → bus1). */
  | { kind: 'branch'; p0: number; cap: number | null; unit: 'MVA' | 'MW'; bus0: string; bus1: string }

export interface Visual {
  /** Gauge share 0..1 and its band colour. */
  fill?: number
  fillColor?: string
  /** Glow share 0..1 (emissiveFor scales it). */
  emissive?: number
  /** Rotor speed, rpm. */
  spin?: number
  /** Chevrons: +1 from the object's own bus to the far one, −1 back, 0 idle; `pct` = loading. */
  flow?: { dir: 1 | -1 | 0; pct: number | null }
  /** Loading band colour. */
  color?: string
  /** Hover label and readout line: every coloured state names its number and capacity. */
  label: string
}

const EPS = 1e-3
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
const mw = (v: number) => `${v.toFixed(1)} MW`
const num = (v: number) => (Number.isInteger(v) ? `${v}` : v.toFixed(1))
const pct = (share: number) => `${Math.round(share * 100)} %`
/** Tanks read "fill"; batteries and other storage "SoC". */
const FILL_WORD: Record<string, string> = { h2store: 'fill', thermalStore: 'fill', store: 'fill' }

export function visualFor(type: AssetType, state: AssetState, obj: { name: string; bus: string }): Visual {
  const name = obj.name
  switch (state.kind) {
    case 'storage': {
      const verb = state.mw > EPS ? `discharging ${mw(state.mw)}` : state.mw < -EPS ? `charging ${mw(-state.mw)}` : 'idle'
      if (state.energyCap && state.energy != null) {
        const share = clamp01(state.energy / state.energyCap)
        return { fill: share, fillColor: socColor(share * 100), label: `${name} · ${verb} · ${FILL_WORD[type.id] ?? 'SoC'} ${pct(share)} of ${num(state.energyCap)} MWh` }
      }
      return { label: `${name} · ${verb}${state.energy != null ? ` · ${state.energy.toFixed(1)} MWh stored` : ''}` }
    }
    case 'output': {
      const share = state.cap ? clamp01(state.mw / state.cap) : null
      const label = `${name} · ${mw(state.mw)}${state.cap ? ` · ${pct(state.mw / state.cap)} of ${num(state.cap)} MW` : ''}`
      if (type.geometry.template === 'turbineArray') {
        const rated = Number(type.geometry.params.ratedRpm) || 0
        return { spin: share != null ? rated * share : 0, label }
      }
      return share != null ? { emissive: share, label } : { label }
    }
    case 'link': {
      const share = state.cap ? clamp01(state.mw / state.cap) : null
      const label = `${name} · ${mw(state.mw)} in${state.cap ? ` · ${pct(state.mw / state.cap)} of ${num(state.cap)} MW` : ''}`
      return share != null ? { emissive: share, label } : { label }
    }
    case 'load': {
      const label = `${name} · ${mw(state.mw)}${state.peak ? ` · ${pct(state.mw / state.peak)} of ${num(state.peak)} MW peak` : ''}`
      return state.peak ? { emissive: clamp01(state.mw / state.peak), label } : { label }
    }
    case 'branch': {
      const loading = state.cap ? (Math.abs(state.p0) / state.cap) * 100 : null
      const dir = Math.abs(state.p0) <= EPS ? 0 : (Math.sign(state.p0) * (obj.bus === state.bus0 ? 1 : -1)) as 1 | -1
      const dest = state.p0 >= 0 ? state.bus1 : state.bus0
      const label = `${name} · ${mw(Math.abs(state.p0))} → ${dest}${loading != null ? ` · ${Math.round(loading)} % of ${num(state.cap!)} ${state.unit} rating` : ''}`
      return { flow: { dir, pct: loading }, ...(loading != null ? { color: loadingColor(loading) } : {}), label }
    }
  }
}

/** Every site object's visual at this snapshot (objects without a state have none), in site order. */
export function siteVisuals(states: ReadonlyMap<string, AssetState>, objects: readonly { type: string; name: string; kind: string; bus: string }[], lib: readonly AssetType[] = DEFAULT_LIBRARY): Map<string, Visual> {
  const out = new Map<string, Visual>()
  for (const o of objects) {
    const key = `${o.type}:${o.name}`
    const st = states.get(key)
    const type = lib.find(t => t.id === o.kind)
    if (st && type) out.set(key, visualFor(type, st, o))
  }
  return out
}
