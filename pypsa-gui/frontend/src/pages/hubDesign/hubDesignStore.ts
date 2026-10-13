// Hub-design step cards (guided-mode spec §5.2): which card is showing and the
// three choices the cards own. Not persisted — a reload re-derives the step
// from the study record (§5.4). `guided_step` in the chat context reads
// `step` (P25).
import { create } from 'zustand'
import type { EhArchetype } from '../../api/simulation'

export type HubStep = 'start' | 'site' | 'goal' | 'results' | 'improve'
export const HUB_STEPS: readonly HubStep[] = ['start', 'site', 'goal', 'results', 'improve']

/** Where the Goal card's shortfall target came from: the template's own
 *  override, the pack default readiness reports, or the user's typing. Only
 *  a pack-seeded value follows the pack when the site type changes. */
export type LoleSource = 'template' | 'pack' | 'user' | null

export interface HubDesignDefaults {
  step: HubStep
  archetype: EhArchetype
  loleTarget: string
  ensCap: string
}

interface HubDesignState {
  /** The project the fields below belong to; `ready` once `resetFor` ran. */
  project: string | null
  ready: boolean
  step: HubStep
  archetype: EhArchetype
  /** Allowed shortfall, h/yr — a string like `PackForm` (half-typed input). */
  loleTarget: string
  loleSource: LoleSource
  /** Energy strictness (ENS cap); '' = the pack's own value. */
  ensCap: string
  /** A manual rail click suppresses the next auto-advance (§5.6). */
  userMovedRail: boolean

  resetFor: (project: string | null, d: HubDesignDefaults) => void
  setStep: (step: HubStep, opts?: { user?: boolean }) => void
  clearUserMoved: () => void
  setArchetype: (a: EhArchetype) => void
  setLoleTarget: (v: string) => void
  seedLoleFromPack: (v: string) => void
  setEnsCap: (v: string) => void
}

export const HUB_DESIGN_INITIAL = {
  project: null as string | null,
  ready: false,
  step: 'start' as HubStep,
  archetype: 'strong_grid' as EhArchetype,
  loleTarget: '',
  loleSource: null as LoleSource,
  ensCap: '',
  userMovedRail: false,
}

export const useHubDesignStore = create<HubDesignState>()((set) => ({
  ...HUB_DESIGN_INITIAL,
  resetFor: (project, d) => set({
    project, ready: true, step: d.step, archetype: d.archetype,
    loleTarget: d.loleTarget, loleSource: d.loleTarget !== '' ? 'template' : null,
    ensCap: d.ensCap, userMovedRail: false,
  }),
  setStep: (step, opts) => set(opts?.user ? { step, userMovedRail: true } : { step }),
  clearUserMoved: () => set({ userMovedRail: false }),
  setArchetype: (archetype) => set({ archetype }),
  setLoleTarget: (loleTarget) => set({ loleTarget, loleSource: 'user' }),
  seedLoleFromPack: (loleTarget) => set({ loleTarget, loleSource: 'pack' }),
  setEnsCap: (ensCap) => set({ ensCap }),
}))
