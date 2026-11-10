/** V1 certifies a mount; V2 certifies an exact displayed source. Neither grants domain approval. */
import { displayedResultsSource, sameResultsSource, type ResultsSourceBinding } from './resultsSource'
import { client } from '../api/client'

export interface NavigationAcknowledgement {
  version: 1 | 2
  action_id: string
  turn_id: string
  binding_generation: number
  project_name: string
  project_id: string
  input_sha256: string
  target: { panel_id: string; canvas_view?: 'site'; results_tab?: string; highlight?: { component: string; name: string } }
  source_binding?: ResultsSourceBinding
}

export function acknowledgeMountedView(
  expected: NavigationAcknowledgement,
  isCurrent: () => boolean,
  onVerified?: () => void,
  // SC-07 S5b R6: told once when this ACK can no longer complete (the 5 s window
  // ended, the view stopped being current, or the server did not complete it).
  onUnverified?: () => void,
): () => void {
  let finished = false
  const mounted = () => Array.from(document.querySelectorAll<HTMLElement>('[data-assurance-panel]'))
    .some(element => {
      if (element.dataset.assurancePanel !== expected.target.panel_id
        || element.dataset.assuranceProject !== expected.project_name
        || element.closest('[hidden], [aria-hidden="true"]')) return false
      // SC-07 S5b R6: a canvas-view target (Site 3D) counts only on that view's own marker.
      if ((element.dataset.assuranceCanvas ?? null) !== (expected.target.canvas_view ?? null)) return false
      for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent)
        if (style.display === 'none' || style.visibility === 'hidden') return false
      }
      if (expected.version === 2) {
        const data = displayedResultsSource(expected.target.results_tab ?? '', element)
        if (!data || !expected.source_binding || !sameResultsSource(data, expected.source_binding)) return false
      }
      // Live-voice 07: a highlighted branch counts only once its row is shown and marked.
      const highlight = expected.target.highlight
      if (highlight && !Array.from(element.querySelectorAll<HTMLElement>('[data-branch-row][aria-current="true"]'))
        .some(row => row.dataset.branchClass === highlight.component && row.dataset.branchName === highlight.name)) return false
      return !expected.target.results_tab || Array.from(element.querySelectorAll<HTMLElement>('[data-assurance-tab]'))
        .some(tab => tab.dataset.assuranceTab === expected.target.results_tab)
    })
  const stop = () => { finished = true; observer.disconnect(); clearTimeout(timeout) }
  const giveUp = () => { if (!finished) { stop(); onUnverified?.() } }
  const check = () => {
    if (finished) return
    if (!isCurrent()) { giveUp(); return }
    if (!mounted()) return
    stop()
    const { version, action_id, turn_id, binding_generation, target } = expected
    void client.post('/chat/ui-ack', { version, action_id, turn_id, binding_generation, target,
      ...(version === 2 ? { source_binding: expected.source_binding } : {}) },
      { skipErrorToast: true }).then(response => {
      if (isCurrent() && mounted() && response.data?.outcome === 'completed') onVerified?.()
      else onUnverified?.()
    }).catch(() => { onUnverified?.() /* Failed/expired ACKs retain submitted status. */ })
  }
  const observer = new MutationObserver(check)
  const timeout = setTimeout(giveUp, 5000)
  observer.observe(document.body, { subtree: true, childList: true, attributes: true })
  check()
  return stop
}
