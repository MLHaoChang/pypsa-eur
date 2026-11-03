// Prefills for the intent comparison inputs (AS-10 follow-up). Read from the network and the grid-code
// profile; a missing source leaves the field blank with a one-line reason, never a guess. All stay editable.
import type { Bus, Link, Load } from '../api/types'

export interface Prefill { value: string; reason?: string }
export interface IntentDefaults { critical: Prefill; sk: Prefill; pst: Prefill; link: Prefill; links: string[] }

const pos = (v: unknown): number | null => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : null }
const text = (n: number) => String(Number(n.toPrecision(12)))

export function intentDefaults(src: { buses: Bus[]; loads: Load[]; links: Link[]; pstLimit: number | null }): IntentDefaults {
  const critBuses = new Set(src.buses.filter(b => b.eh_critical).map(b => b.name))
  const crit = src.loads.filter(l => critBuses.has(l.bus))
    .reduce((s, l) => s + Math.abs(pos(l.p_set_peak) ?? Number(l.p_set) ?? 0), 0)
  const critical: Prefill = critBuses.size === 0
    ? { value: '', reason: 'No bus is marked critical, so the critical load is not known.' }
    : crit > 0 ? { value: text(crit) } : { value: '', reason: 'The critical buses carry no load, so the critical load is not known.' }

  const sks = src.buses.filter(b => b.eh_poc).map(b => pos(b.eh_sk_mva)).filter((v): v is number => v != null)
  const sk: Prefill = sks.length ? { value: text(Math.min(...sks)) }
    : { value: '', reason: 'No point of connection with a short-circuit power is set.' }

  const pst: Prefill = src.pstLimit != null && src.pstLimit > 0 ? { value: text(src.pstLimit) }
    : { value: '', reason: 'The grid code sets no Pst limit, or no grid code is set.' }

  const imp = src.links.find(l => String(l.eh_role ?? '') === 'grid_import')
  const link: Prefill = imp ? { value: imp.name }
    : { value: '', reason: 'No link is tagged grid_import, so pick the campus link.' }

  return { critical, sk, pst, link, links: src.links.map(l => l.name).sort((a, b) => a.localeCompare(b)) }
}
