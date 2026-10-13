// The site connection (IC U1 follow-up b): which Links can be the commercial
// meter, most likely first. Mirrors `services/commercial/binding.py`
// (`_grid_like`, `site_connection_candidates`): one-way Links only (the binding
// refuses a two-way meter). An explicit `eh_role` tag beats the name rule: a
// Link tagged `grid_import` is a PoC candidate, one tagged `grid_export` an
// export candidate, and a Link tagged for the OTHER side is never offered for
// this one. An untagged Link is ranked by the grid-like rule: a bus named or
// carried like the grid, or the bus of an `eh_role=grid_supply` Generator. A
// hint, never a rule: the server checks the direction on save.
import type { Bus, Generator, Link } from '../../api/types'

export interface GridHints {
  carrier: Map<string, string>
  supplyBuses: Set<string>
}

const NO_HINTS: GridHints = { carrier: new Map(), supplyBuses: new Set() }

/** The bus facts the grid-like rule reads (the backend's `_grid_like`). */
export function gridHints(buses: Array<Pick<Bus, 'name' | 'carrier'>> = [],
                          generators: Array<Pick<Generator, 'bus'> & { eh_role?: string | null }> = []
): GridHints {
  return {
    carrier: new Map(buses.map(b => [b.name, String(b.carrier ?? '')])),
    supplyBuses: new Set(generators.filter(g => g.eh_role === 'grid_supply').map(g => g.bus)),
  }
}

export const gridLike = (bus: string, hints: GridHints = NO_HINTS): boolean =>
  /grid/i.test(bus) || /grid/i.test(hints.carrier.get(bus) ?? '') || hints.supplyBuses.has(bus)

export const oneWay = (l: Link): boolean => !(Number(l.p_min_pu) < 0)

/** The Link's `eh_role` tag, '' when untagged. */
function role(l: Link): string {
  const r = String(l.eh_role ?? '').trim()
  return ['', 'nan', 'none', 'null'].includes(r.toLowerCase()) ? '' : r
}

/** How likely `l` is the PoC (`intoSite`) or the export Link; 0 = not
 *  suggested, null = tagged for the other side (never offered as this one). */
function score(l: Link, intoSite: boolean, hints: GridHints): number | null {
  const [src, dst] = intoSite ? [l.bus0, l.bus1] : [l.bus1, l.bus0]
  const r = role(l)
  if (r === (intoSite ? 'grid_export' : 'grid_import')) return null
  const named = gridLike(src, hints) && !gridLike(dst, hints) ? 2 : 0
  return (r === (intoSite ? 'grid_import' : 'grid_export') ? 4 : 0) + named
}

function ranked(links: Link[], intoSite: boolean, hints: GridHints,
                bonus: (l: Link) => number = () => 0): string[] {
  const scored: Array<{ name: string; s: number }> = []
  for (const l of links.filter(oneWay)) {
    const base = score(l, intoSite, hints)
    if (base === null) continue
    const s = base + bonus(l)
    if (s > 0) scored.push({ name: l.name, s })
  }
  return scored.sort((a, b) => b.s - a.s || a.name.localeCompare(b.name)).map(x => x.name)
}

/** Suggested PoC Links (grid → site), most likely first. */
export function pocCandidates(links: Link[], hints: GridHints = NO_HINTS): string[] {
  return ranked(links, true, hints)
}

/** Suggested export Links (site → grid); the exact reverse of `poc` ranks first. */
export function exportCandidates(links: Link[], poc: string | null,
                                 hints: GridHints = NO_HINTS): string[] {
  const p = links.find(l => l.name === poc)
  return ranked(links, false, hints, l => (p && l.name !== p.name && l.bus0 === p.bus1
    && l.bus1 === p.bus0 ? 3 : 0)).filter(n => n !== poc)
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
