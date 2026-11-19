// template-e2e 11: "Set the Value of Lost Load (VOLL) in Solver settings ›" on
// the FMEA tab asks Solver settings for the VOLL block (not the goal block
// below it), never flips the mode, and Back names the tab it left.
import { beforeEach, expect, it } from 'vitest'
import { openVollField } from './flowNavigation'
import { useUIStore } from '../store/uiStore'

beforeEach(() => {
  useUIStore.setState({
    flowReturn: null, uiMode: 'expert', activeSection: 'results', activeSlidePanel: 'results',
    solverBlockRequest: null,
  })
})

it.each(['expert', 'guided'] as const)('asks for the VOLL block and names the origin (%s)', (mode) => {
  useUIStore.setState({ uiMode: mode })
  openVollField('FMEA')
  const ui = useUIStore.getState()
  expect(ui.uiMode).toBe(mode)
  expect(ui.solverBlockRequest).toBe('voll')
  expect(ui.flowReturn?.label).toBe('FMEA')
})
