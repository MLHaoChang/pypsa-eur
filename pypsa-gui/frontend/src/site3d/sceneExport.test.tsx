// SC-04: Export for Blender follows the server's /flow export_scene verdict
// (CODING_STANDARDS UI rule 1); the Site's scene only supplies the revision.
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { useSceneExport } from './sceneExport'

const flow = vi.hoisted(() => ({ value: null as unknown }))
const scene = vi.hoisted(() => ({ value: undefined as unknown }))
vi.mock('../hooks/useProjectFlow', () => ({ useProjectFlow: () => ({ flow: flow.value }) }))
vi.mock('./useSiteScene', () => ({ useSiteScene: () => ({ data: scene.value }) }))

const verdict = (available: boolean, reason?: string) => ({
  scene: { actions: [{ action: 'export_scene', available, required_inputs: [], computes: '',
    blocker: available ? null : { code: 'no_buildings', reason, link: '›', home: { site_part: 'generate' } } }] },
})

beforeEach(() => { useUIStore.setState({ currentProject: 'p' }) })

describe('useSceneExport', () => {
  it('is available exactly when the server verdict says so, with the scene revision for the title', () => {
    flow.value = verdict(true)
    scene.value = { state: 'enabled', revision: 7, buildings: [] }
    const { result } = renderHook(() => useSceneExport('campus'))
    expect(result.current).toMatchObject({ available: true, reason: null, revision: 7, busy: false })
  })

  it('shows the blocker reason the server sent, never one of its own', () => {
    flow.value = verdict(false, 'Unsaved project: save it to keep a 3D site')
    scene.value = { state: 'enabled', revision: 7, buildings: [{}] }
    const { result } = renderHook(() => useSceneExport('campus'))
    expect(result.current).toMatchObject({ available: false, reason: 'Unsaved project: save it to keep a 3D site' })
  })

  it('stays disabled without a reason while the flow is loading', () => {
    flow.value = null
    scene.value = undefined
    const { result } = renderHook(() => useSceneExport('campus'))
    expect(result.current).toMatchObject({ available: false, reason: null, revision: null })
  })
})
