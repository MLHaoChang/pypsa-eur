import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

const { list, sw } = vi.hoisted(() => ({ list: vi.fn(), sw: vi.fn() }))
vi.mock('../api/projects', () => ({ projectsApi: { list } }))
vi.mock('../utils/projectActions', () => ({ switchToProject: sw }))

import { useProjectDeepLink, retryOpen } from './useProjectDeepLink'

const qc = new QueryClient()
beforeEach(() => {
  list.mockReset(); sw.mockReset()
  useUIStore.setState({ openFailure: null, currentProject: null })
  window.history.replaceState({}, '', '/app?project=p1')
})

describe('useProjectDeepLink', () => {
  it('words a transport error as a plain sentence, never the raw axios text', async () => {
    list.mockResolvedValue([{ id: 'p1', name: 'Grid' }])
    sw.mockResolvedValue({ status: 'error', message: 'Request failed with status code 500' })
    renderHook(() => useProjectDeepLink(null, qc))
    await waitFor(() => expect(useUIStore.getState().openFailure).not.toBeNull())
    const msg = useUIStore.getState().openFailure!.message
    expect(msg).not.toMatch(/status code|Request failed/)
    expect(msg).toMatch(/Try again/)
  })

  it('reports a switch that does not land, instead of leaving an empty app', async () => {
    list.mockResolvedValue([{ id: 'p1', name: 'Grid' }])
    sw.mockResolvedValue({ status: 'abort-failed' })
    renderHook(() => useProjectDeepLink(null, qc))
    await waitFor(() => expect(useUIStore.getState().openFailure?.target).toBe('p1'))
    expect(window.location.search).toBe('')
  })

  it('activates once per value even when currentProject changes under it', async () => {
    let release!: () => void
    list.mockReturnValue(new Promise(r => { release = () => r([{ id: 'p1', name: 'Grid' }]) }))
    sw.mockResolvedValue({ status: 'switched' })
    const { rerender } = renderHook(({ c }) => useProjectDeepLink(c, qc), { initialProps: { c: null as string | null } })
    rerender({ c: 'other' }); rerender({ c: null })
    release()
    await waitFor(() => expect(sw).toHaveBeenCalledTimes(1))
    expect(sw).toHaveBeenCalledWith('p1', qc)
  })

  it('Retry reruns the open and clears the failure on success', async () => {
    useUIStore.setState({ openFailure: { target: 'p1', message: 'x' } })
    sw.mockResolvedValue({ status: 'switched' })
    await retryOpen(qc)
    expect(sw).toHaveBeenCalledWith('p1', qc)
    expect(useUIStore.getState().openFailure).toBeNull()
  })
})
