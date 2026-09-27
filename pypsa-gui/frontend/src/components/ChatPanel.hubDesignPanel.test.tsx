// Guided-mode spec §3.6 — the assistant can open the hub-design panel.
// `ui_open_panel` arrives as a navigate ui_event; ChatPanel normalises the
// panel id (HubDesign / hubDesign / hub_design → 'hubDesign') and its
// setSlidePanel allow-list must accept the new slot. Hidden panels stay
// reachable in Guided through the same path (§3.5, §3.7).
import { beforeEach, describe, expect, it } from 'vitest'
import { APPLY_UI_NAVIGATE_FOR_TEST as applyUiNavigate } from './ChatPanel'
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
