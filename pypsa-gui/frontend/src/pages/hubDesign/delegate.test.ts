// Guided-mode spec §5.7: the cards hand work to the assistant through two
// calls. In P24 both seed the composer and open the dock — nothing is sent
// (P25 turns `delegate` into chatStore.sendRequest at this one import site).
import { beforeEach, describe, expect, it } from 'vitest'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import type { EhReviewFinding } from '../../api/simulation'
import {
  actionText, ask, askText, delegate, footerAskText, footerDelegateText,
  siteFixText, stressScenarioText, VOLL_TEXT,
} from './delegate'

beforeEach(() => {
  useUIStore.setState({ assistantDockOpen: false })
  useChatStore.setState({ composerSeed: null })
})

const F: EhReviewFinding = {
  id: 'certification_fail', severity: 'high', title: 'Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target',
  evidence: { verdict: 'fail', lole_h_per_year: 12.38 },
  recommendation: 'Add firm local capacity.',
  actions: [
    { tool: 'run_eh_study', args: { archetype: 'weak_flexible', stages: ['apply_pack', 'dtc_stress'] }, effect: 'size it' },
    { tool: 'update_solver_config', args: { partial: { voll: 5000 } }, effect: 'second' },
  ],
}

describe('ask / delegate (P24: seed, never send)', () => {
  it('ask opens the dock and seeds the composer with the text', () => {
    ask('hello there')
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useChatStore.getState().composerSeed).toBe('hello there')
  })

  it('delegate behaves exactly like ask until P25', () => {
    useChatStore.setState({ messages: [], streaming: false })
    delegate('do it')
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useChatStore.getState().composerSeed).toBe('do it')
    // shown, not sent: no transcript entry, no stream
    expect(useChatStore.getState().messages).toEqual([])
    expect(useChatStore.getState().streaming).toBe(false)
  })
})

describe('texts (§5.7, verbatim)', () => {
  it('site fixes', () => {
    expect(siteFixText('grid')).toBe(
      'On the Site card, the grid connection is missing. Tag the import Link with eh_role = grid_import and the grid-side bus eh_poc = true, explaining each choice before the confirmation.')
    expect(siteFixText('critical')).toBe(
      'On the Site card, no critical load is tagged. Propose which buses must stay on (eh_critical = true) and tag them after I confirm.')
    expect(siteFixText('strength')).toBe(
      'On the Site card, grid-strength data is missing. Ask me for the short-circuit level (MVA) at the point of connection and set eh_sk_mva on that bus after I confirm.')
    expect(siteFixText('outage', 4)).toBe(
      'On the Site card, 4 units have no outage data. List them and ask me for outage rate and repair time per unit, then set outage_rate_value and mttr_hours after I confirm.')
  })

  it('improve: do uses the first action with its exact JSON arguments', () => {
    expect(actionText(F)).toBe(
      'Apply this recommendation from the study review: "Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target". '
      + 'Run the tool run_eh_study with exactly these arguments: '
      + JSON.stringify({ archetype: 'weak_flexible', stages: ['apply_pack', 'dtc_stress'] })
      + '. Say in one sentence what will change, then proceed to the confirmation.')
  })

  it('improve: ask quotes the title and the evidence', () => {
    expect(askText(F)).toBe(
      'Explain in plain language: "Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target". Evidence: '
      + JSON.stringify(F.evidence) + '. What are my options?')
  })

  it('card footers', () => {
    expect(footerAskText('site')).toBe('I am on the Site card of the hub design. What is missing?')
    expect(footerAskText('start')).toBe('I am on the Start card of the hub design. Which one fits my situation?')
    expect(footerDelegateText('start', 'weak_flexible')).toBe(
      'Pick the template that best fits a weak-grid site and create the project.')
    expect(footerDelegateText('site', 'off_grid')).toBe(
      'Review the Site card for this network and fix every gap you can, one confirmation at a time.')
    expect(footerDelegateText('goal', 'off_grid')).toBe(
      'Run the Energy Hub study with the recommended settings for this project.')
    expect(footerDelegateText('results', 'off_grid')).toBe(
      'Summarise the study results for a non-specialist and tell me the single most important next step.')
    expect(footerDelegateText('improve', 'off_grid')).toBe(
      'Apply the highest-severity recommendation from review_eh_study, one confirmation at a time, then review again.')
  })

  it('VOLL and the stress scenario', () => {
    expect(VOLL_TEXT).toBe('Set VOLL to 5000 €/MWh so the study can price shortfall.')
    expect(stressScenarioText('an off-grid site like the Island Microgrid example')).toBe(
      "Add a stress scenario to this project's registry for an off-grid site like the Island Microgrid example; "
      + 'read the current registry first and send the whole list back with put_stress_scenarios.')
  })
})
