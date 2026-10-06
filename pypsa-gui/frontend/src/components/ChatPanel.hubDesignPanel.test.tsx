// Guided-mode spec §3.6 — the assistant can open the hub-design panel.
// `ui_open_panel` arrives as a navigate ui_event; ChatPanel normalises the
// panel id (HubDesign / hubDesign / hub_design → 'hubDesign') and its
// setSlidePanel allow-list must accept the new slot. Hidden panels stay
// reachable in Guided through the same path (§3.5, §3.7).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { APPLY_UI_NAVIGATE_FOR_TEST as applyUiNavigate, ChatStarterChips, workflowContextFor } from './ChatPanel'
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

// The greeting chips are the harness's start menu (chat harness issue 03):
// `GET /chat/workflows?context=…`, where the context is derived here from
// where the user is. The menu's CONTENT per context is pinned on the backend
// (`tests/test_chat_workflows_route.py`); this side pins the mapping and the
// click (owner decision Q10: a chip SENDS, it does not prefill).
describe('start menu', () => {
  it('maps where the user is onto the registry context', () => {
    expect(workflowContextFor(null, 'guided')).toBe('unbound')
    expect(workflowContextFor(null, 'expert')).toBe('unbound')
    expect(workflowContextFor('Demo', 'guided')).toBe('guided')
    expect(workflowContextFor('Demo', 'expert')).toBe('expert')
  })

  it('a chip hands the whole prompt to onPick, with the intent as its tooltip', () => {
    const onPick = vi.fn()
    render(
      <ChatStarterChips
        prompts={[{ label: 'Build a network', text: 'Help me build my network step by step.', title: 'Add buses…' }]}
        onPick={onPick}
      />,
    )
    const chip = screen.getByTestId('chat-starter-chip')
    expect(chip.getAttribute('title')).toBe('Add buses…')
    fireEvent.click(chip)
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'Build a network', text: 'Help me build my network step by step.' }),
    )
  })
})
