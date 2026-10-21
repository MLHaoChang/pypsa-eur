// UX-flow audit finding 12: the palette's "New project" called resetNetwork()
// at once, with no confirmation, and could throw away the open project. A new
// project has one entry point, the Projects-home create wizard; the palette
// reaches it through "Projects home" and never resets the open network.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import CommandPalette from './CommandPalette'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'

vi.mock('../api/network', async (orig) => {
  const real = await orig<typeof import('../api/network')>()
  return { networkApi: { ...real.networkApi, resetNetwork: vi.fn().mockResolvedValue({}) } }
})
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn().mockResolvedValue(null),
}))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { loading: vi.fn(() => 't1'), success: vi.fn(), error: vi.fn() }),
}))

function renderPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useUIStore.setState({ paletteMode: 'all', currentProject: 'Alpha', projectName: 'Alpha' })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/app']}>
        <CommandPalette />
        <Routes>
          <Route path="/app" element={<p>workbench</p>} />
          <Route path="/projects" element={<p>projects home</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe('the palette never resets an open project', () => {
  it('has no "New project" action of its own', async () => {
    const user = userEvent.setup()
    renderPalette()
    await user.keyboard('new project')
    expect(screen.queryByText('New project')).toBeNull()
    expect(screen.queryByText('reset network')).toBeNull()
  })

  it('finds "Projects home" for "new project" and goes there without touching the network', async () => {
    const user = userEvent.setup()
    renderPalette()
    await user.keyboard('new project')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByText('projects home')).toBeTruthy())
    expect(networkApi.resetNetwork).not.toHaveBeenCalled()
    expect(useUIStore.getState().currentProject).toBe('Alpha')
  })
})
