import { applyUiNavigate } from './uiNavigate'
import { useUIStore } from '../store/uiStore'

/** Project-level Results tabs. Asset detail needs a selected component. */
export const KEY_RESULT_TABS = [
  { id: 'overview', label: 'Overview', blurb: 'Horizon economics, generation mix, and storage.' },
  { id: 'capex', label: 'Capacity expansion', blurb: 'Capital cost, operating cost, and sized assets.' },
  { id: 'dispatch', label: 'Dispatch', blurb: 'Generation, storage, and load at each snapshot.' },
  { id: 'loadflow', label: 'Load flow', blurb: 'Line flows, voltages, and losses.' },
  { id: 'prices', label: 'Prices', blurb: 'Marginal prices and what drives them.' },
  { id: 'economics', label: 'Economics', blurb: 'Revenue, profit, and levelised cost by asset.' },
  { id: 'emissions', label: 'Emissions', blurb: 'CO₂ totals and the cap shadow price.' },
  { id: 'curtailment', label: 'Curtailment', blurb: 'Renewable energy the model could not use.' },
  { id: 'lostload', label: 'Lost load', blurb: 'Demand that went unserved.' },
  { id: 'adequacy', label: 'Adequacy', blurb: 'Reliability targets and the cost of availability.' },
  { id: 'storage', label: 'Storage cycling', blurb: 'How hard each store is cycled.' },
  { id: 'fmea', label: 'FMEA', blurb: 'Failure modes ranked by yearly criticality.' },
  { id: 'investment', label: 'Investment', blurb: 'The site bill, participants, and value flows.' },
] as const

export type KeyResultTab = (typeof KEY_RESULT_TABS)[number]

export const KEY_RESULT_STEP_MS = 1200

export function keyResultsWalkRequested(text: string): boolean {
  const t = text.toLowerCase()
  const mentionsResults = /\bresults?\b/.test(t)
  const mentionsTabs = /\btabs?\b/.test(t)
  const asksToTour = /\b(walk|tour|show|open|each|through|guide|key)\b/.test(t)
  return mentionsResults && mentionsTabs && asksToTour
}

export function openProjectRequested(text: string): boolean {
  const t = text.toLowerCase()
  return /\bopen\b/.test(t) && /\bprojects?\b/.test(t) && !keyResultsWalkRequested(text)
}

/**
 * Open each key results tab through `applyUiNavigate` — the same function
 * ChatPanel runs when the harness tool `ui_open_panel` emits a ui_event.
 * The first tab is applied immediately so a caller can observe it without
 * waiting; the rest are spaced so each request can be consumed.
 */
export function startKeyResultsWalk(opts?: {
  schedule?: (fn: () => void, ms: number) => number
  stepMs?: number
  onStep?: (tab: KeyResultTab, index: number, last: boolean) => void
}): () => void {
  const schedule = opts?.schedule ?? ((fn, ms) => window.setTimeout(fn, ms))
  const stepMs = opts?.stepMs ?? KEY_RESULT_STEP_MS
  // Session-only. setUiMode would persist a mode the user did not choose.
  useUIStore.setState({ uiMode: 'expert' })
  const handles: number[] = []
  const apply = (tab: KeyResultTab, index: number) => {
    applyUiNavigate({ kind: 'navigate', panel_id: 'results', results_tab: tab.id })
    opts?.onStep?.(tab, index, index === KEY_RESULT_TABS.length - 1)
  }
  KEY_RESULT_TABS.forEach((tab, index) => {
    if (index === 0) apply(tab, index)
    else handles.push(schedule(() => apply(tab, index), index * stepMs))
  })
  return () => {
    for (const handle of handles) window.clearTimeout(handle)
  }
}

export function openProjectList(): void {
  applyUiNavigate({ kind: 'navigate', panel_id: 'project_picker' })
}
