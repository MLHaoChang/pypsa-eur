// Phase 2 spec E6 (WP4 gate): optimised sizes are opted into per project.
import { describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'

describe('uiStore siteSizing', () => {
  it('defaults to installed and resets on a project switch', () => {
    expect(useUIStore.getState().siteSizing).toBe('installed')
    useUIStore.getState().setSiteSizing('optimised')
    expect(useUIStore.getState().siteSizing).toBe('optimised')
    useUIStore.getState().setCurrentProject('another-project')
    expect(useUIStore.getState().siteSizing).toBe('installed')
  })
})
