// Hub-design step cards (guided-mode spec §5.2): which card is showing. The
// goal and the site type are the project's stored reliability goal
// (`hooks/useReliabilityGoal`, W3b-R), not card state. Not persisted — a reload re-derives the step
// from the study record (§5.4). `guided_step` in the chat context reads
// `step` (P25).
import { create } from 'zustand'
import type { FlowSectionId } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import type { SiteParams } from './genericSite'

export type HubStep = 'start' | 'site' | 'goal' | 'results' | 'improve'
export const HUB_STEPS: readonly HubStep[] = ['start', 'site', 'goal', 'results', 'improve']

/** The flow section each card belongs to (workspace-reconciliation §4): the
 *  Start card is section 1, Site is the connection, Goal is the question asked
 *  of the model, Results and Improve are section 10. Sections not listed here
 *  have no bespoke card: the generic section card shows them. */
export const STEP_SECTION: Record<HubStep, FlowSectionId> = {
  start: 'project', site: 'connection', goal: 'scenarios', results: 'results', improve: 'results',
}
export const SECTION_STEP: Partial<Record<FlowSectionId, HubStep>> = {
  project: 'start', connection: 'site', scenarios: 'goal', results: 'results',
}

export interface HubDesignDefaults {
  step: HubStep
}

interface HubDesignState {
  /** The project the fields below belong to; `ready` once `resetFor` ran. */
  project: string | null
  ready: boolean
  step: HubStep
  goalAdvancedRequested: boolean
  /** A manual rail click suppresses the next auto-advance (§5.6). */
  userMovedRail: boolean
  /** A generic site's values to start a NEW project from (the Site card's
   *  "Start a new project from these values"); the Start card's form opens
   *  with them. */
  siteSeed: { params: SiteParams; entered: string[] } | null
  /** The New-project wizard's "Your own site" card asked the Start card to
   *  open the generic site form (consumed once by the Start card). */
  siteFormRequested: boolean
  /** Projects whose assistant set-up banner was dismissed (this page load; the browser copy is `readSetupCardDismissed`; UM08-CHROME-1). */
  setupCardDismissedFor: string[]

  resetFor: (project: string | null, d: HubDesignDefaults) => void
  setStep: (step: HubStep, opts?: { user?: boolean }) => void
  clearUserMoved: () => void
  /** One-shot: the Goal card opens its "More settings" (a link to the stress editor). */
  setGoalAdvancedRequested: (v: boolean) => void
  setSiteSeed: (seed: { params: SiteParams; entered: string[] } | null) => void
  setSiteFormRequested: (v: boolean) => void
  dismissSetupCard: (project: string | null) => void
}

const DISMISSED_KEY = 'pypsa.assistantSetupCardDismissed'
const dismissedKey = (project: string | null) => `${DISMISSED_KEY}.${project ?? ''}`
/** The browser's copy of "dismissed for this project"; unreadable storage reads as not dismissed. */
export function readSetupCardDismissed(project: string | null): boolean {
  try { return localStorage.getItem(dismissedKey(project)) === '1' } catch { return false }
}

export const HUB_DESIGN_INITIAL = {
  project: null as string | null,
  ready: false,
  step: 'start' as HubStep,
  goalAdvancedRequested: false,
  userMovedRail: false,
  siteSeed: null as { params: SiteParams; entered: string[] } | null,
  siteFormRequested: false,
  setupCardDismissedFor: [] as string[],
}

export const useHubDesignStore = create<HubDesignState>()((set) => ({
  ...HUB_DESIGN_INITIAL,
  resetFor: (project, d) => set({
    project, ready: true, step: d.step, userMovedRail: false, siteSeed: null,
  }),
  // The card and the section are one position: moving the step moves the
  // section both modes share (`activeSection`, §7).
  setStep: (step, opts) => {
    useUIStore.getState().setActiveSection(STEP_SECTION[step])
    set(opts?.user ? { step, userMovedRail: true } : { step })
  },
  clearUserMoved: () => set({ userMovedRail: false }),
  setGoalAdvancedRequested: (goalAdvancedRequested) => set({ goalAdvancedRequested }),
  setSiteSeed: (siteSeed) => set({ siteSeed }),
  setSiteFormRequested: (siteFormRequested) => set({ siteFormRequested }),
  dismissSetupCard: (project) => set((st) => {
    const key = project ?? ''
    if (st.setupCardDismissedFor.includes(key)) return st
    const next = [...st.setupCardDismissedFor, key]
    try { localStorage.setItem(dismissedKey(project), '1') } catch { /* held for this page load only */ }
    return { setupCardDismissedFor: next }
  }),
}))
