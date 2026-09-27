// Guided-mode spec §3.6 — the assistant can open the hub-design panel.
// `ui_open_panel` arrives as a navigate ui_event; ChatPanel normalises the
// panel id (HubDesign / hubDesign / hub_design → 'hubDesign') and its
// setSlidePanel allow-list must accept the new slot. Hidden panels stay
// reachable in Guided through the same path (§3.5, §3.7).
import { beforeEach, describe, expect, it } from 'vitest'
import { APPLY_UI_NAVIGATE_FOR_TEST as applyUiNavigate, starterPromptsFor } from './ChatPanel'
import { useUIStore } from '../store/uiStore'

beforeEach(() => {
  useUIStore.setState({ activeSlidePanel: null, currentProject: 'Demo', uiMode: 'expert' })
})

describe('ui_open_panel → hubDesign', () => {
  for (const id of ['HubDesign', 'hubDesign', 'hub_design']) {
    it(`'${id}' opens the hubDesign panel`, () => {
      applyUiNavigate({ kind: 'navigate', panel_id: id })
      expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    })
  }

  it('a panel Guided hides from the sidebar still opens in Guided', () => {
    useUIStore.setState({ uiMode: 'guided' })
    applyUiNavigate({ kind: 'navigate', panel_id: 'SolverSettings' })
    expect(useUIStore.getState().activeSlidePanel).toBe('simparams')
  })
})

// §10 addendum (gate P23 B3): an explicit results_tab request reaches Results
// in Guided (Results honours it — Results.hiddenTabRequest.test.tsx).
describe('ui_open_panel with results_tab in Guided', () => {
  it('opens Results and requests the hidden tab', () => {
    useUIStore.setState({ uiMode: 'guided', resultsTabRequest: null })
    applyUiNavigate({ kind: 'navigate', panel_id: 'Results', results_tab: 'economics' })
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('economics')
  })
})

// §10 addendum (gate note): in Guided the greeting chips offer only
// Guided-visible destinations, plus Hub design. Expert is unchanged.
describe('greeting chips', () => {
  it('Guided: Hub design, adequacy, and a summary — nothing hidden', () => {
    const chips = starterPromptsFor('Demo', 'guided')
    expect(chips.map(c => c.label)).toEqual(['Open Hub design', 'Check adequacy', 'Summarize this solve'])
    const text = chips.map(c => c.text).join(' | ')
    expect(text).not.toMatch(/Economics|compare/i)
  })

  it('Expert: the original three', () => {
    expect(starterPromptsFor('Demo', 'expert').map(c => c.label))
      .toEqual(['Compare two scenarios', 'Open Economics', 'Summarize this solve'])
  })

  it('no project: the unbound pair in either mode', () => {
    for (const m of ['guided', 'expert'] as const) {
      expect(starterPromptsFor(null, m).map(c => c.label)).toEqual(['Open a project', 'Browse projects'])
    }
  })
})
