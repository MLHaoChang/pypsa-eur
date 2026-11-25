import { beforeEach, describe, expect, it } from 'vitest'
import { ISLAND_RETURN_LABEL, openAssetControls } from './islandControlsNav'
import { useUIStore } from '../store/uiStore'

describe('openAssetControls', () => {
  beforeEach(() => useUIStore.setState({ flowReturn: null, fieldDrawer: null, activeSlidePanel: null }))

  it.each(['guided', 'expert'] as const)('in %s, Back names the island panel it came from', mode => {
    useUIStore.setState({ uiMode: mode, activeSlidePanel: 'campusElectrical' })
    openAssetControls('StorageUnit', 'bess')
    expect(useUIStore.getState().flowReturn?.label).toBe(ISLAND_RETURN_LABEL)
  })
})
