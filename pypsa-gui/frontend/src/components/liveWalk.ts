import { applyUiNavigate } from './uiNavigate'
import { resultsEvidenceApi } from '../api/resultsEvidence'
import { displayedResultsSource, sameResultsSource } from '../utils/resultsSource'
import { manualNavigation, navigationEpoch, onManualNavigation } from '../utils/navigationEpoch'
import { useUIStore } from '../store/uiStore'

/** Project-level Results tabs. Asset detail needs a selected component. */
export const KEY_RESULT_TABS = [
  { id: 'summary', label: 'Summary', blurb: 'Recorded solve outcomes and evidence availability.' },
  { id: 'overview', label: 'Overview', blurb: 'Horizon economics, generation mix, and storage.' },
  { id: 'capex', label: 'Capacity expansion', blurb: 'Capital cost, operating cost, and sized assets.' },
  { id: 'dispatch', label: 'Dispatch', blurb: 'Generation, storage, and load at each snapshot.' },
  { id: 'loadflow', label: 'Load flow', blurb: 'Branch-loading evidence and its conventions.' },
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

function normalizedCommand(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, ' ')
}

export function keyResultsWalkRequested(text: string): boolean {
  // Only complete generic UI commands bypass the harness. Names, sources,
  // windows, negations and additional instructions need ordinary resolution.
  const command = normalizedCommand(text)
  return /^(?:please )?(?:walk|guide)(?: me)? through (?:the )?(?:key )?(?:current )?results (?:in )?(?:each|every|all) tabs?(?: please)?[.!?]?$/.test(command)
    || /^(?:please )?(?:show|open|tour)(?: me)? (?:the )?(?:key )?(?:current )?results? tabs?(?: please)?[.!?]?$/.test(command)
}

export function openProjectRequested(text: string): boolean {
  const command = normalizedCommand(text)
  return /^(?:please )?open (?:(?:the|my) )?(?:projects|project(?: list| picker)?)(?: please)?[.!?]?$/.test(command)
}

export type ResultsWalk = (() => void) & { next: () => void }

/** Only adapters that render and revalidate a matching native source may advance. */
export function startKeyResultsWalk(opts?: {
  onStep?: (tab: KeyResultTab, index: number, last: boolean) => void
  onWaiting?: (tab: KeyResultTab, total: number) => void
  onStop?: (reason: string) => void
}): ResultsWalk {
  manualNavigation() // Supersede navigation from any earlier model turn.
  const epoch = navigationEpoch()
  const initial = useUIStore.getState()
  const project = initial.currentProject
  const run = initial.discussedRun
  let stopped = false, ready = false, checking = false, index = 0
  let tabs: KeyResultTab[] = []
  let observer: MutationObserver | null = null
  let timeout: ReturnType<typeof setTimeout> | null = null
  let detachManual = () => {}, detachContext = () => {}
  const stop = (reason?: string) => {
    if (stopped) return
    stopped = true
    observer?.disconnect()
    if (timeout) clearTimeout(timeout)
    detachManual(); detachContext()
    if (reason) opts?.onStop?.(reason)
  }
  const cancel = (() => stop()) as ResultsWalk
  detachManual = onManualNavigation(() => stop('Walk stopped. Your newer navigation takes priority.'))
  detachContext = useUIStore.subscribe(state => {
    if (state.currentProject !== project || state.discussedRun?.runId !== run?.runId
      || state.discussedRun?.baselineRunId !== run?.baselineRunId
      || state.uiMode !== initial.uiMode || state.resultSource !== initial.resultSource) {
      stop('Walk stopped because the project, source or mode changed.')
    }
  })
  const current = () => !stopped && navigationEpoch() === epoch
  const check = async () => {
    if (!current() || checking || ready) return
    const tab = tabs[index]
    if (!tab) return
    const shown = displayedResultsSource(tab.id)
    if (!shown || shown.project_name !== project || (run ? shown.scope !== 'historical' || shown.run_id !== run.runId
      || (shown.baseline_run_id ?? undefined) !== run.baselineRunId : shown.scope !== 'current'
      || shown.source !== initial.resultSource)) return
    checking = true
    try {
      const evidence = run
        ? await resultsEvidenceApi.run(run.runId, shown.section ?? undefined, run.baselineRunId)
        : await resultsEvidenceApi.loadflow(initial.resultSource, shown.window_from, shown.window_to)
      const now = displayedResultsSource(tab.id)
      if (!current() || !now || !evidence.binding || !sameResultsSource(now, shown)
        || !sameResultsSource(shown, evidence.binding)) return
      ready = true
      if (timeout) clearTimeout(timeout)
      opts?.onStep?.(tab, index, index === tabs.length - 1)
      if (index === tabs.length - 1) stop()
    } catch { stop('Matching evidence could not be verified. The walkthrough remains incomplete.') }
    finally { checking = false }
  }
  const open = () => {
    if (!current()) return
    ready = false
    const tab = tabs[index]
    opts?.onWaiting?.(tab, tabs.length)
    applyUiNavigate({ kind: 'navigate', panel_id: 'results', results_tab: tab.id }, epoch)
    if (timeout) clearTimeout(timeout)
    timeout = setTimeout(() => stop('Matching result data did not become available. Walkthrough incomplete.'), 10000)
    void check()
  }
  cancel.next = () => {
    if (!current() || !ready || index + 1 >= tabs.length) return
    index += 1; open()
  }
  observer = new MutationObserver(() => { void check() })
  observer.observe(document.body, { subtree: true, childList: true, attributes: true })
  if (!project) stop('Save and open a project before walking through source-bound results.')
  else if (run) {
    void resultsEvidenceApi.run(run.runId, undefined, run.baselineRunId).then(view => {
      if (!current()) return
      const available = new Set((view.data.sections ?? []).filter(s => s.available).map(s => s.name))
      tabs = KEY_RESULT_TABS.filter(t => (t.id === 'summary' || available.has(t.id)
        || t.id === run.resultsTab && !!run.section && available.has(run.section))
        && (initial.uiMode === 'expert' || ['summary', 'adequacy', 'fmea'].includes(t.id)))
      if (!tabs.length) stop('This retained run has no eligible results evidence.')
      else open()
    }).catch(() => stop('Retained run evidence unavailable. Current results were not substituted.'))
  } else if (initial.uiMode === 'expert') {
    // Current load flow has a shared, source-attested adapter. Other tabs
    // remain manually available; a mount alone cannot certify their data.
    tabs = KEY_RESULT_TABS.filter(t => t.id === 'loadflow')
    open()
  } else stop('No source-bound current-results walkthrough adapter is available in Guided mode. Open the results tabs individually.')
  return cancel
}

export function openProjectList(): void {
  applyUiNavigate({ kind: 'navigate', panel_id: 'project_picker' })
}
