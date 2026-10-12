/** A view ACK certifies a mounted target, never the scientific validity of its data. */
import { client } from '../api/client'

export interface NavigationAcknowledgement {
  version: 1
  action_id: string
  turn_id: string
  binding_generation: number
  project_name: string
  project_id: string
  input_sha256: string
  target: { panel_id: string; results_tab?: string }
}

export function acknowledgeMountedView(
  expected: NavigationAcknowledgement,
  isCurrent: () => boolean,
  onVerified?: () => void,
): () => void {
  let finished = false
  const mounted = () => Array.from(document.querySelectorAll<HTMLElement>('[data-assurance-panel]'))
    .some(element => {
      if (element.dataset.assurancePanel !== expected.target.panel_id
        || element.dataset.assuranceProject !== expected.project_name
        || element.closest('[hidden], [aria-hidden="true"]')) return false
      for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent)
        if (style.display === 'none' || style.visibility === 'hidden') return false
      }
      return !expected.target.results_tab || Array.from(element.querySelectorAll<HTMLElement>('[data-assurance-tab]'))
        .some(tab => tab.dataset.assuranceTab === expected.target.results_tab)
    })
  const stop = () => { finished = true; observer.disconnect(); clearTimeout(timeout) }
  const check = () => {
    if (finished) return
    if (!isCurrent()) { stop(); return }
    if (!mounted()) return
    stop()
    const { version, action_id, turn_id, binding_generation, target } = expected
    void client.post('/chat/ui-ack', { version, action_id, turn_id, binding_generation, target },
      { skipErrorToast: true }).then(response => {
      if (isCurrent() && mounted() && response.data?.outcome === 'completed') onVerified?.()
    }).catch(() => { /* Failed/expired ACKs retain submitted status. */ })
  }
  const observer = new MutationObserver(check)
  const timeout = setTimeout(stop, 5000)
  observer.observe(document.body, { subtree: true, childList: true, attributes: true })
  check()
  return stop
}
