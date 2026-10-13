// Guided-mode spec §3.3 — the command palette's `act-ui-mode` entry. Titled
// by the NEXT state (like act-toggle-theme); running it is the same action as
// the header switch: an explicit choice plus a toast.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import toast from 'react-hot-toast'
import CommandPalette from './CommandPalette'
import { useUIStore } from '../store/uiStore'
import { fetchLocalSettings } from '../api/localSettings'
import { fetchLLMSettingsOrNull } from '../api/llmSettings'

vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn(),
}))
vi.mock('../api/llmSettings', async (orig) => ({
  ...(await orig<typeof import('../api/llmSettings')>()),
  fetchLLMSettingsOrNull: vi.fn(),
}))

function renderPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useUIStore.setState({ paletteMode: 'all' })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const realSet = useUIStore.getState().setUiMode

beforeEach(() => {
  vi.mocked(fetchLocalSettings).mockResolvedValue(null)
  vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
  vi.spyOn(toast, 'success').mockImplementation(() => '')
})
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ setUiMode: realSet })
})

describe('CommandPalette — act-ui-mode', () => {
  it('in Guided the entry offers Expert', () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderPalette()
    expect(screen.getByText('Switch to Expert mode')).toBeTruthy()
    expect(screen.queryByText('Switch to Guided mode')).toBeNull()
  })

  it('in Expert the entry offers Guided', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderPalette()
    expect(screen.getByText('Switch to Guided mode')).toBeTruthy()
    expect(screen.queryByText('Switch to Expert mode')).toBeNull()
  })

  it('running it makes the explicit choice and toasts', () => {
    const setUiMode = vi.fn()
    useUIStore.setState({ uiMode: 'expert', setUiMode })
    renderPalette()
    fireEvent.click(screen.getByText('Switch to Guided mode'))
    expect(setUiMode).toHaveBeenCalledWith('guided', { explicit: true })
    expect(toast.success).toHaveBeenCalledWith(
      'Guided mode on — advanced panels hidden, ask the assistant for any of them')
  })

  it('running it for real persists the explicit choice', () => {
    useUIStore.setState({ uiMode: 'guided', uiModeExplicit: false })
    renderPalette()
    fireEvent.click(screen.getByText('Switch to Expert mode'))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().uiModeExplicit).toBe(true)
    expect(toast.success).toHaveBeenCalledWith('Expert mode on')
  })
})
