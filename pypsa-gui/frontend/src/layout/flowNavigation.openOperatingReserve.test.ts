// control-strategy walkthrough 03: "Edit the reserve shares ›" (Results) and the
// failure card's "Open Operating reserve" land on Solver settings › Dispatch ›
// Operating reserve: the section's panel in Expert with a Back pill naming the
// origin, in place in Guided (never flips the mode).
import { beforeEach, expect, it } from 'vitest'
import { openOperatingReserve } from './flowNavigation'
import { useUIStore } from '../store/uiStore'

beforeEach(() => {
  useUIStore.setState({
    flowReturn: null, uiMode: 'expert', activeSection: 'results', activeSlidePanel: 'results',
    solverBlockRequest: null, fieldDrawer: null,
  })
})

it('asks Solver settings for the reserve block and opens section 8 in Expert, with Back to Results', () => {
  openOperatingReserve()
  const ui = useUIStore.getState()
  expect(ui.solverBlockRequest).toBe('reserve')
  expect(ui.uiMode).toBe('expert')
  expect(ui.activeSection).toBe('scenarios')
  expect(ui.flowReturn?.label).toBe('Results & reports')
})

it('opens Solver settings in place in Guided and stays in Guided', () => {
  useUIStore.setState({ uiMode: 'guided' })
  openOperatingReserve()
  const ui = useUIStore.getState()
  expect(ui.solverBlockRequest).toBe('reserve')
  expect(ui.uiMode).toBe('guided')
  expect(ui.fieldDrawer).toMatchObject({ section: 'scenarios', panel: 'simparams' })
})
