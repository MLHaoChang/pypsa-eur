// The site connection (IC U1 follow-up b): which Links can be the commercial
// meter, most likely first. Mirrors `services/commercial/binding.py`
// `site_connection_candidates`: one-way Links only (the binding refuses a
// two-way meter), a Link tagged `eh_role=grid_import` / `grid_export`, or one
// leaving / entering a bus named like the grid. A hint, never a rule: the
// server checks the direction when the connection is saved.
import type { Link } from '../../api/types'

export const gridLike = (bus: string): boolean => /grid/i.test(bus)

export const oneWay = (l: Link): boolean => !(Number(l.p_min_pu) < 0)

function score(l: Link, intoSite: boolean): number {
  const [src, dst] = intoSite ? [l.bus0, l.bus1] : [l.bus1, l.bus0]
  const tag = intoSite ? 'grid_import' : 'grid_export'
  return (l.eh_role === tag ? 4 : 0) + (gridLike(src) && !gridLike(dst) ? 2 : 0)
}

function ranked(links: Link[], intoSite: boolean, bonus: (l: Link) => number = () => 0): string[] {
  return links.filter(oneWay)
    .map(l => ({ name: l.name, s: score(l, intoSite) + bonus(l) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s || a.name.localeCompare(b.name))
    .map(x => x.name)
}

/** Suggested PoC Links (grid → site), most likely first. */
export function pocCandidates(links: Link[]): string[] {
  return ranked(links, true)
}

/** Suggested export Links (site → grid); the exact reverse of `poc` ranks first. */
export function exportCandidates(links: Link[], poc: string | null): string[] {
  const p = links.find(l => l.name === poc)
  return ranked(links, false, l => (p && l.name !== p.name && l.bus0 === p.bus1 && l.bus1 === p.bus0
    ? 3 : 0)).filter(n => n !== poc)
}

/** The browser's IANA zone, or UTC. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

const FALLBACK_ZONES = ['UTC', 'Europe/London', 'Europe/Berlin', 'Europe/Paris', 'Europe/Madrid',
  'Europe/Amsterdam', 'America/New_York', 'America/Chicago', 'America/Denver',
  'America/Los_Angeles', 'Asia/Tokyo', 'Australia/Sydney']

/** IANA zones to offer, with `extra` (the stored and the browser's zone) included. */
export function timeZoneOptions(...extra: Array<string | null | undefined>): string[] {
  const supported = (Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf
  let zones: string[]
  try {
    zones = supported ? supported('timeZone') : FALLBACK_ZONES
  } catch {
    zones = FALLBACK_ZONES
  }
  const all = new Set(['UTC', ...zones])
  for (const z of extra) if (z) all.add(z)
  return [...all].sort((a, b) => (a === 'UTC' ? -1 : b === 'UTC' ? 1 : a.localeCompare(b)))
}
