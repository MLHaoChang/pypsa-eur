// RP-04c (RP-Q4): Profile Studio is an Expert-only advanced view. Every route
// into the panel (the assistant's navigation, a link) goes through
// `setSlidePanel`, which sends a Guided request to Time Series instead.
import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'

beforeEach(() => useUIStore.setState({ activeSlidePanel: null, uiMode: 'expert' }))

describe('Profile Studio is Expert only', () => {
  it('opens in Expert', () => {
    useUIStore.getState().setSlidePanel('profiles')
    expect(useUIStore.getState().activeSlidePanel).toBe('profiles')
  })

  it("lands on the section's Time Series drawer over the hub design in Guided", () => {
    useUIStore.setState({ uiMode: 'guided', fieldDrawer: null })
    useUIStore.getState().setSlidePanel('profiles')
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('hubDesign')
    expect(ui.activeSection).toBe('profiles')
    expect(ui.fieldDrawer).toEqual({ section: 'profiles', panel: 'timeseries', component: null, entity: null, field: null })
  })

  it('leaves every other panel alone in Guided', () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().setSlidePanel('results')
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })
})
