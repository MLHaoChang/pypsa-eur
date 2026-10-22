// W10 R2: the legacy Grid study results panel is an address, not a second home.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'
import CampusResultsAlias from './CampusResultsAlias'

afterEach(cleanup)

describe('CampusResultsAlias', () => {
  it('opens Results on Grid code & connection', () => {
    useUIStore.setState({ activeSlidePanel: 'campusResults', resultsTabRequest: null })
    render(<CampusResultsAlias />)
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('grid_code')
  })
})
