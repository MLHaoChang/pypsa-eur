import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'

describe('requestAssetDetail', () => {
  beforeEach(() => {
    useUIStore.setState({
      assetDetailRequest: null, resultsTabRequest: null,
      selectedComponent: null, activeSlidePanel: null,
    })
  })

  it('stores the request verbatim', () => {
    useUIStore.getState().requestAssetDetail({
      componentClass: 'Generator', name: 'Gas 1',
      category: 'dispatch', metrics: ['p'], mode: 'duration', chart: true,
    })
    expect(useUIStore.getState().assetDetailRequest).toEqual({
      componentClass: 'Generator', name: 'Gas 1',
      category: 'dispatch', metrics: ['p'], mode: 'duration', chart: true,
    })
  })

  it('opens the Results panel on the asset tab in one call', () => {
    useUIStore.getState().requestAssetDetail({ componentClass: 'Line', name: 'L1' })
    const s = useUIStore.getState()
    expect(s.activeSlidePanel).toBe('results')
    expect(s.resultsTabRequest).toBe('asset')
  })

  it('also selects the component so PropertiesPanel stays in sync', () => {
    useUIStore.getState().requestAssetDetail({ componentClass: 'Bus', name: 'B1' })
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Bus', name: 'B1' })
  })

  it('clears cleanly so a second request re-fires', () => {
    useUIStore.getState().requestAssetDetail({ componentClass: 'Bus', name: 'B1' })
    useUIStore.getState().clearAssetDetailRequest()
    expect(useUIStore.getState().assetDetailRequest).toBeNull()
  })
})
