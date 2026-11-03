// The open site's layout findings, published by the 3D view for the Issues
// panel (S2: "Validation, never a block" — the findings show in the overlay,
// as outlines, and as a section in the Issues panel with deep links). The
// panel lives in the main bundle and has no layout of its own, so it reads
// what the site view last computed; nothing here imports three.
import { create } from 'zustand'
import type { NativeFinding, PlacementFinding } from './placementCheck'

export interface PlacementFindingsState {
  /** The site the findings belong to; null when no site view is open. */
  site: { id: string; name: string } | null
  findings: PlacementFinding[]
  /** SC-02: the findings against the native scene's buildings and roofs. */
  native: NativeFinding[]
  publish: (site: { id: string; name: string }, findings: PlacementFinding[], native?: NativeFinding[]) => void
  clear: () => void
}

const NONE: PlacementFinding[] = []
const NO_NATIVE: NativeFinding[] = []

export const usePlacementFindings = create<PlacementFindingsState>((set) => ({
  site: null,
  findings: NONE,
  native: NO_NATIVE,
  publish: (site, findings, native = NO_NATIVE) => set({ site, findings, native }),
  clear: () => set({ site: null, findings: NONE, native: NO_NATIVE }),
}))

/** The class and name a finding's key names — what `setSelectedComponent` takes. */
export function componentOfKey(key: string): { type: string; name: string } {
  const i = key.indexOf(':')
  return i < 0 ? { type: '', name: key } : { type: key.slice(0, i), name: key.slice(i + 1) }
}
