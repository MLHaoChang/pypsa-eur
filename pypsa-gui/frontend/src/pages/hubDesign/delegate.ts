// How the hub-design cards hand work to the assistant (guided-mode spec §5.7).
//
// `ask(text)` opens the dock with the text in the composer — shown, not sent
// (the P22 behaviour). `delegate(text)` is the "Let the assistant do this"
// path: in P24 it is identical to `ask`; P25 changes this ONE function to
// `useChatStore.getState().sendRequest(text, { source: 'hub-design' })`.
import type { EhArchetype, EhReviewFinding } from '../../api/simulation'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import type { HubStep } from './hubDesignStore'

export function ask(text: string): void {
  useUIStore.getState().setAssistantDockOpen(true)
  useChatStore.getState().seedComposer(text)
}

export function delegate(text: string): void {
  ask(text)
}

/** Button titles: in P24 nothing is sent until the user presses Send. */
export const ASK_TITLE =
  'Opens the assistant with a question prefilled — nothing is sent until you press Send.'
export const DELEGATE_TITLE =
  'Opens the assistant with this request prefilled — nothing is sent or changed until you send it and confirm any action.'

export type SiteFix = 'grid' | 'critical' | 'strength' | 'outage'

/** Site-card fix requests. P24 omits the grid text's suggest_eh_setup clause
 *  (the tool arrives in P25, which adds it back). */
export function siteFixText(kind: SiteFix, n = 0): string {
  switch (kind) {
    case 'grid':
      return 'On the Site card, the grid connection is missing. Tag the import Link with eh_role = grid_import and the grid-side bus eh_poc = true, explaining each choice before the confirmation.'
    case 'critical':
      return 'On the Site card, no critical load is tagged. Propose which buses must stay on (eh_critical = true) and tag them after I confirm.'
    case 'strength':
      return 'On the Site card, grid-strength data is missing. Ask me for the short-circuit level (MVA) at the point of connection and set eh_sk_mva on that bus after I confirm.'
    case 'outage':
      return `On the Site card, ${n} units have no outage data. List them and ask me for outage rate and repair time per unit, then set outage_rate_value and mttr_hours after I confirm.`
  }
}

export const VOLL_TEXT = 'Set VOLL to 5000 €/MWh so the study can price shortfall.'

/** "Let the assistant do this" on a finding: its FIRST action (P25 queues
 *  one message per action). Null when the finding has no action. */
export function actionText(f: EhReviewFinding): string | null {
  const a = f.actions?.[0]
  if (!a) return null
  return `Apply this recommendation from the study review: "${f.title}". Run the tool ${a.tool} with exactly these arguments: ${JSON.stringify(a.args)}. Say in one sentence what will change, then proceed to the confirmation.`
}

export function askText(f: EhReviewFinding): string {
  return `Explain in plain language: "${f.title}". Evidence: ${JSON.stringify(f.evidence)}. What are my options?`
}

export function stressScenarioText(context: string): string {
  return `Add a stress scenario to this project's registry for ${context}; read the current registry first and send the whole list back with put_stress_scenarios.`
}

const STEP_NAME: Record<HubStep, string> = {
  start: 'Start', site: 'Site', goal: 'Goal', results: 'Results', improve: 'Improve',
}

/** The question each card's "Ask" footer carries. The catalogue's `hub_*`
 *  entries are hover statements (P24-BE gate N5), so the questions live here. */
export const STEP_QUESTION: Record<HubStep, string> = {
  start: 'Which one fits my situation?',
  site: 'What is missing?',
  goal: 'What goal suits my site?',
  results: 'What does this mean for me?',
  improve: 'What should I do first?',
}

/** Short site-type words for sentences ("a weak-grid site"). */
export const ARCHETYPE_SHORT: Record<EhArchetype, string> = {
  strong_grid: 'strong-grid', weak_flexible: 'weak-grid', off_grid: 'off-grid',
}

export function footerAskText(step: HubStep): string {
  return `I am on the ${STEP_NAME[step]} card of the hub design. ${STEP_QUESTION[step]}`
}

export function footerDelegateText(step: HubStep, archetype: EhArchetype): string {
  switch (step) {
    case 'start':
      return `Pick the template that best fits a ${ARCHETYPE_SHORT[archetype]} site and create the project.`
    case 'site':
      return 'Review the Site card for this network and fix every gap you can, one confirmation at a time.'
    case 'goal':
      return 'Run the Energy Hub study with the recommended settings for this project.'
    case 'results':
      return 'Summarise the study results for a non-specialist and tell me the single most important next step.'
    case 'improve':
      return 'Apply the highest-severity recommendation from review_eh_study, one confirmation at a time, then review again.'
  }
}
