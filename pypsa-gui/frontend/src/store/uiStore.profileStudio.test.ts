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

  it('lands on Time Series in Guided, in the same section', () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().setSlidePanel('profiles')
    expect(useUIStore.getState().activeSlidePanel).toBe('timeseries')
    expect(useUIStore.getState().activeSection).toBe('profiles')
  })

  it('leaves every other panel alone in Guided', () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().setSlidePanel('results')
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })
})
