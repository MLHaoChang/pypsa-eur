// What the model must have before a 3D site can be generated from it
// (visual-layers plan 3 S4; owner decision O3: the site is generated on
// demand from the assets, never at project or template creation). The 3D
// view's empty state renders these lines as a checklist, each unmet one a
// button to where it is fixed. Pure: no React, no three; main-bundle-safe.
import { busLatLng, type BusCoords } from '../utils/geo'

export type ReadinessId = 'coordinates' | 'branch' | 'boundary'

/** Where an unmet line is fixed: the Satellite map (place a bus), the connect tool, or Generate itself. */
export type ReadinessFix = 'placeBus' | 'connect' | 'generate'

export interface ReadinessLine {
  id: ReadinessId
  met: boolean
  /** The line as the user reads it. */
  text: string
  /** The fix for an unmet line, and the button's label. */
  fix: ReadinessFix
  fixLabel: string
}

export interface ReadinessInput {
  buses: readonly (BusCoords & { name: string })[]
  /** Every two-port branch: lines, links and transformers. */
  branches: readonly { bus0: string; bus1: string }[]
  /** The sites that exist (none while the empty state shows; kept general). */
  sites: readonly { name: string }[]
}

export interface Readiness {
  lines: ReadinessLine[]
  /** Every line met: Generate 3D site may be offered. */
  ready: boolean
  /** The buses with coordinates, in model order — what Generate draws around. */
  placedBuses: string[]
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 'es'}`

/**
 * A branch connects two buses when both ends name a bus of the model and
 * they differ: a line to a bus that was deleted, or a link looped onto its
 * own bus, is no connection.
 */
export function connectedBranches(input: Pick<ReadinessInput, 'buses' | 'branches'>): number {
  const names = new Set(input.buses.map(b => b.name))
  return input.branches.filter(br => br.bus0 !== br.bus1 && names.has(br.bus0) && names.has(br.bus1)).length
}

export function siteReadiness(input: ReadinessInput): Readiness {
  const placedBuses = input.buses.filter(b => busLatLng(b) !== null).map(b => b.name)
  const total = input.buses.length
  const connected = connectedBranches(input)

  const coordinates: ReadinessLine = {
    id: 'coordinates',
    met: placedBuses.length > 0,
    text: total === 0
      ? 'No bus in the model yet'
      : placedBuses.length === 0
        ? `None of the ${plural(total, 'bus')} has coordinates`
        : `${placedBuses.length} of ${plural(total, 'bus')} ${placedBuses.length === 1 ? 'has' : 'have'} coordinates`,
    fix: 'placeBus',
    fixLabel: 'Place a bus on the Satellite map',
  }
  const branch: ReadinessLine = {
    id: 'branch',
    met: connected > 0,
    text: connected === 0
      ? 'No line, link or transformer connects two buses'
      : `${connected} branch${connected === 1 ? '' : 'es'} connect${connected === 1 ? 's' : ''} two buses`,
    fix: 'connect',
    fixLabel: 'Connect two buses',
  }
  const boundary: ReadinessLine = {
    id: 'boundary',
    met: input.sites.length > 0 || placedBuses.length > 0,
    text: input.sites.length > 0
      ? `Site boundary: ${input.sites.map(s => s.name).join(', ')}`
      : placedBuses.length > 0
        ? `A boundary can be drawn around ${placedBuses[0]} (Generate draws a rectangle; New site on the Satellite map draws any shape)`
        : 'No placed bus to draw a boundary around',
    fix: placedBuses.length > 0 ? 'generate' : 'placeBus',
    fixLabel: placedBuses.length > 0 ? 'Generate 3D site' : 'Place a bus on the Satellite map',
  }
  const lines = [coordinates, branch, boundary]
  return { lines, ready: lines.every(l => l.met), placedBuses }
}
