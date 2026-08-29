// Characterization of the palette's pointer-drag, written BEFORE the gesture
// moves into hooks/useAssetDrag.ts. Sidebar.tsx:270-303 has zero coverage
// today (recon §14 risk 1) and every line of it is about to move, so these
// three cases are the only thing that will notice a behaviour change.
//
// jsdom facts this file depends on, measured in this worktree:
//   • PointerEvent exists, so fireEvent.pointerDown works.
//   • document.elementFromPoint does NOT exist — it must be installed with
//     defineProperty, not vi.spyOn.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { useUIStore } from '../store/uiStore'
import Sidebar from './Sidebar'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getMeta: vi.fn(), undoInfo: vi.fn() },
  }
})
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return { ...actual, projectsApi: { ...actual.projectsApi, list: vi.fn() } }
})
vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, preflight: vi.fn() },
  }
})

import { networkApi } from '../api/network'
import { projectsApi } from '../api/projects'
import { simulationApi } from '../api/simulation'

/** jsdom has no elementFromPoint. Install one that returns `el` (or null). */
function stubElementFromPoint(el: Element | null) {
  Object.defineProperty(document, 'elementFromPoint', {
    value: () => el,
    configurable: true,
    writable: true,
  })
}

function renderSidebar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * Open the DATA ▸ Assets disclosure so the palette items are in the tree.
 * The DATA section itself is open by default (Sidebar.tsx:1622), but the
 * Assets row inside it starts collapsed (`assetsOpen` = false, :1235).
 *
 * The palette item's label is 'Bus (Node)', not 'Bus' (Sidebar.tsx:133);
 * getByText is an exact matcher, so the full label is what finds it.
 */
async function openPalette() {
  renderSidebar()
  await userEvent.click(screen.getByText('Assets'))
  return screen.getByText('Bus (Node)').closest('[role="button"]') as HTMLElement
}

beforeEach(() => {
  vi.mocked(networkApi.getMeta).mockReset().mockResolvedValue({} as never)
  vi.mocked(networkApi.undoInfo).mockReset().mockResolvedValue({ depth: 0, unsaved: false })
  vi.mocked(projectsApi.list).mockReset().mockResolvedValue([])
  vi.mocked(simulationApi.preflight).mockReset().mockResolvedValue({} as never)
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, creationItem: null })
})

describe('palette drag — behaviour as of c2cc4510', () => {
  it('a click with no movement opens the form with NO dropPosition', async () => {
    const item = await openPalette()
    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 10, clientY: 10 }))

    const req = useUIStore.getState().creationItem
    expect(req?.id).toBe('bus')
    expect(req?.dropPosition).toBe(undefined)
  })

  it('a drag past the 3px threshold onto .react-flow carries a dropPosition', async () => {
    const item = await openPalette()

    const canvas = document.createElement('div')
    canvas.className = 'react-flow'
    document.body.appendChild(canvas)
    stubElementFromPoint(canvas)
    ;(window as unknown as { rfInstance?: unknown }).rfInstance = {
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x: x * 2, y: y * 2 }),
    }

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 90, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 90, clientY: 70 }))

    const req = useUIStore.getState().creationItem
    expect(req?.id).toBe('bus')
    expect(req?.dropPosition).toEqual({ x: 180, y: 140 })

    delete (window as unknown as { rfInstance?: unknown }).rfInstance
  })

  it('a drag released outside .react-flow cancels silently', async () => {
    const item = await openPalette()

    const elsewhere = document.createElement('div')
    document.body.appendChild(elsewhere)
    stubElementFromPoint(elsewhere)

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 300, clientY: 300 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 300, clientY: 300 }))

    expect(useUIStore.getState().creationItem).toBe(null)
  })

  it('a movement of 2px stays a click, not a drag', async () => {
    const item = await openPalette()

    const elsewhere = document.createElement('div')
    document.body.appendChild(elsewhere)
    stubElementFromPoint(elsewhere)

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 12, clientY: 12 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 12, clientY: 12 }))

    // Below the 3px threshold `moved` stays false, so this is the click path
    // and the form opens even though the release was outside the canvas.
    expect(useUIStore.getState().creationItem?.id).toBe('bus')
    expect(useUIStore.getState().creationItem?.dropPosition).toBe(undefined)
  })
})
