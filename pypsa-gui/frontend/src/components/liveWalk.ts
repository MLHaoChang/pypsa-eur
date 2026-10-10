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

export const KEY_RESULT_STEP_MS = 1800

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
 *
 * Steps are chained. Scheduling every timeout up front lets a long Results
 * render hold the thread until they are all due, so only the last tab paints.
 */
export function startKeyResultsWalk(opts?: {
  schedule?: (fn: () => void, ms: number) => number
  stepMs?: number
  onStep?: (tab: KeyResultTab, index: number, last: boolean) => void
}): () => void {
  // Frame pacing, not a pile of timeouts. A long Results render makes every
  // already-queued timeout due at once, so the tour used to paint only the
  // last tab. Each step starts its own wait after the previous one has run.
  const stops: Array<() => void> = []
  const schedule = opts?.schedule ?? ((fn: () => void, ms: number) => {
    stops.push(scheduleAfterFrames(fn, ms))
    return 0
  })
  const stepMs = opts?.stepMs ?? KEY_RESULT_STEP_MS
  // Session-only. setUiMode would persist a mode the user did not choose.
  useUIStore.setState({ uiMode: 'expert' })
  let cancelled = false
  const handles: number[] = []
  const apply = (index: number) => {
    if (cancelled || index >= KEY_RESULT_TABS.length) return
    const tab = KEY_RESULT_TABS[index]
    applyUiNavigate({ kind: 'navigate', panel_id: 'results', results_tab: tab.id })
    opts?.onStep?.(tab, index, index === KEY_RESULT_TABS.length - 1)
    if (index + 1 < KEY_RESULT_TABS.length) {
      handles.push(schedule(() => apply(index + 1), stepMs))
    }
  }
  apply(0)
  return () => {
    cancelled = true
    for (const stop of stops) stop()
    for (const handle of handles) window.clearTimeout(handle)
  }
}

/** Wait about `ms` of presented frames, then run `fn`. */
function scheduleAfterFrames(fn: () => void, ms: number): () => void {
  let left = Math.max(1, Math.round(ms / 16))
  let id = 0
  let stopped = false
  const tick = () => {
    if (stopped) return
    left -= 1
    if (left <= 0) fn()
    else id = window.requestAnimationFrame(tick)
  }
  id = window.requestAnimationFrame(tick)
  return () => {
    stopped = true
    window.cancelAnimationFrame(id)
  }
}

export function openProjectList(): void {
  applyUiNavigate({ kind: 'navigate', panel_id: 'project_picker' })
}
