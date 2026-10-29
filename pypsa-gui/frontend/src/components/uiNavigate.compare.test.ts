import { beforeEach, expect, it } from 'vitest'
import { applyUiNavigate } from './uiNavigate'
import { returnToOrigin } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'

// LC-18 (UX): the sensitivity comparison offers Compare for base vs a case;
// on the user's yes it opens on the matching tab, in the same mode, and
// "Back" returns to where the user was.
beforeEach(() => {
  useUIStore.setState({ flowReturn: null, compareNavRequest: null, compareRailOpen: false })
})

it.each(['guided', 'expert'] as const)('%s: Compare opens on the pair and tab, with a way back', mode => {
  useUIStore.setState({ uiMode: mode, activeSlidePanel: 'scenarios' })
  applyUiNavigate({ kind: 'navigate', panel_id: 'compare', compare_a: 'Base', compare_b: 'Gas 5',
    compare_tab: 'loading' })
  const ui = useUIStore.getState()
  expect(ui.uiMode).toBe(mode)
  expect(ui.activeSlidePanel).toBe('results')
  expect(ui.compareRailOpen).toBe(true)
  expect(ui.compareNavRequest).toMatchObject({ a: 'Base', b: 'Gas 5', tab: 'loading' })
  expect(ui.flowReturn?.panel).toBe('scenarios')
  returnToOrigin()
  expect(useUIStore.getState().activeSlidePanel).toBe('scenarios')
  expect(useUIStore.getState().uiMode).toBe(mode)
})
