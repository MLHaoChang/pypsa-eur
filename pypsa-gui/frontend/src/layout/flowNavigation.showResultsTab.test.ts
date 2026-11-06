// DC 06: "Storage view ›" from Grid study opens Results › Storage scrolled to
// the Reserve products card, and Back names Grid study (UX ask 1 and 2).
import { beforeEach, expect, it } from 'vitest'
import { showResultsTab } from './flowNavigation'
import { useUIStore } from '../store/uiStore'

beforeEach(() => {
  useUIStore.setState({
    flowReturn: null, uiMode: 'expert', activeSection: 'scenarios', activeSlidePanel: 'campusStudy',
    resultsTabRequest: null, resultsSectionRequest: null,
  })
})

it('names the origin it was given on the Back pill and asks the tab for the block', () => {
  showResultsTab('storage', { section: 'reserves', from: 'Grid study' })
  const ui = useUIStore.getState()
  expect(ui.activeSlidePanel).toBe('results')
  expect([ui.resultsTabRequest, ui.resultsSectionRequest]).toEqual(['storage', 'reserves'])
  expect(ui.flowReturn?.label).toBe('Grid study')
  expect(ui.flowReturn?.panel).toBe('campusStudy')
})

it('keeps the section label without a named origin', () => {
  showResultsTab('storage')
  const ui = useUIStore.getState()
  expect(ui.resultsSectionRequest).toBeNull()
  expect(ui.flowReturn?.label).not.toBe('Grid study')
})
