// The site entity: how one is made, which one a bus belongs to, and the
// frames the layout needs (site origin ↔ each member bus).
//
// Pure; main bundle; no `three`.

import { toLocal, type LngLat } from './geo'
import { busLatLng } from '../utils/geo'
import { boundaryBounds, centroid, type BoundsXY } from './boundary'
import type { LngLatTuple, Site, SitesDocument } from './types'

/** 22 base64url characters from 16 random bytes — matches SITE_ID_RE. */
export function newSiteId(random: (n: number) => Uint8Array = defaultRandom): string {
  const bytes = random(16)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function defaultRandom(n: number): Uint8Array {
  const out = new Uint8Array(n)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) crypto.getRandomValues(out)
  else for (let i = 0; i < n; i++) out[i] = Math.floor(Math.random() * 256)
  return out
}

export function newSite(input: { name: string; boundary: LngLatTuple[]; buses: string[]; id?: string }): Site {
  const origin = centroid(input.boundary)
  return {
    id: input.id ?? newSiteId(),
    name: input.name,
    buses: [...input.buses],
    boundary: input.boundary.map(v => [v[0], v[1]] as LngLatTuple),
    origin: { lng: origin.lng, lat: origin.lat },
    placements: {},
  }
}

/** The first site whose membership lists the bus, or null. */
export function siteForBus(doc: SitesDocument, busName: string): Site | null {
  return doc.sites.find(s => s.buses.includes(busName)) ?? null
}

/** The bus the site view packs around and the creation form defaults to. */
export function primaryBus(site: Site): string | null {
  return site.buses[0] ?? null
}

/** The boundary's bounds in metres from the site origin — what the camera fits. */
export function siteBounds(site: Site): BoundsXY {
  return boundaryBounds(site.boundary, site.origin)
}

/**
 * Each member bus's position in the site frame (metres from the origin).
 * Buses that are not placed, or not in the list, are omitted: the layout
 * cannot draw a switchyard it cannot locate.
 */
export function busOffsets<T extends { name: string; x: number | null | undefined; y: number | null | undefined }>(
  site: Site, buses: T[],
): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {}
  for (const name of site.buses) {
    const b = buses.find(x => x.name === name)
    const ll = b ? busLatLng(b) : null
    if (!ll) continue
    const p = toLocal(site.origin, { lng: ll[1], lat: ll[0] } as LngLat)
    out[name] = [p.x, p.y]
  }
  return out
}

/** `Site n` where n is one more than the sites that exist. */
export function defaultSiteName(doc: SitesDocument): string {
  return `Site ${doc.sites.length + 1}`
}
