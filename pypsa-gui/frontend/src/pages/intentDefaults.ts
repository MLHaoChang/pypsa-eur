// Prefills for the intent comparison inputs (AS-10 follow-up). Read from the network and the grid-code
// profile; a missing source leaves the field blank with a one-line reason, never a guess. All stay editable.
// A reason shows only once its source has loaded: nothing is claimed while loading, and an error says what failed.
import type { Bus, Link, Load } from '../api/types'

export interface Prefill { value: string; reason?: string }
export interface IntentDefaults { critical: Prefill; sk: Prefill; pst: Prefill; link: Prefill; links: string[] }
export type SourceState = 'loading' | 'ok' | 'error'

/** The Link `eh_role` tags that mark the grid import (mirrors the backend's `EH_IMPORT_ROLES`). */
const IMPORT_ROLES = ['grid_import', 'eh_import', 'import']

const pos = (v: unknown): number | null => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : null }
const text = (n: number) => String(Number(n.toPrecision(12)))

/** `want` blank-with-reason, unless a source is still loading (no reason yet) or failed (say so). */
function missing(states: SourceState[], what: string, reason: string): Prefill {
  if (states.includes('error')) return { value: '', reason: `${what} could not be read.` }
  if (states.includes('loading')) return { value: '' }
  return { value: '', reason }
}

export function intentDefaults(src: {
  buses: Bus[]; loads: Load[]; links: Link[]; pstLimit: number | null; gridCode: string | null
  status: { buses: SourceState; loads: SourceState; links: SourceState; grid: SourceState }
}): IntentDefaults {
  const { status: st } = src
  const critBuses = new Set(src.buses.filter(b => b.eh_critical).map(b => b.name))
  const crit = src.loads.filter(l => critBuses.has(l.bus)).reduce((s, l) => s + Math.abs(pos(l.p_set_peak) ?? pos(l.p_set) ?? 0), 0)
  const critStates = [st.buses, st.loads]
  const critical: Prefill = critStates.every(s => s === 'ok') && crit > 0 ? { value: text(crit) }
    : missing(critStates, 'The critical load', critBuses.size === 0
      ? 'No bus is marked critical, so the critical load is not known.'
      : 'The critical buses carry no load, so the critical load is not known.')

  const sks = src.buses.filter(b => b.eh_poc).map(b => pos(b.eh_sk_mva)).filter((v): v is number => v != null)
  const sk: Prefill = st.buses === 'ok' && sks.length ? { value: text(Math.min(...sks)) }
    : missing([st.buses], 'The short-circuit power', 'No point of connection with a short-circuit power is set.')

  const pst: Prefill = src.pstLimit != null && src.pstLimit > 0 ? { value: text(src.pstLimit) }
    : src.gridCode == null ? { value: '', reason: 'No grid code is set.' }
      : missing([st.grid], 'The grid code', 'The grid code sets no Pst limit.')

  const imp = src.links.find(l => IMPORT_ROLES.includes(String(l.eh_role ?? '').trim()))
  const link: Prefill = st.links === 'ok' && imp ? { value: imp.name }
    : missing([st.links], 'The links', 'No link is marked as the grid import, so choose the campus link.')

  return { critical, sk, pst, link, links: src.links.map(l => l.name).sort((a, b) => a.localeCompare(b)) }
}
