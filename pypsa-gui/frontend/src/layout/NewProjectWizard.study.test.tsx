// The Study tab is the spec's "pick type [2] planning → dynamics" step: it
// creates a project of a DIFFERENT KIND through `/api/gridspine/projects`, not
// a blank network through `/api/projects/{name}`. These tests pin that the tab
// exists, that it posts the study config the user typed (numbers as numbers),
// that a backend 422 is rendered inline rather than swallowed, and that a taken
// name is refused before any request — the same overwrite discipline as the
// Blank tab.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import NewProjectWizard from './NewProjectWizard'
import { gridspineApi } from '../api/gridspine'
import type { ProjectInfo } from '../api/types'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))
vi.mock('../api/gridspine', () => ({
  gridspineApi: { createStudy: vi.fn() },
}))

const EXISTING: ProjectInfo = {
  id: 'id-existing', name: 'taken', created_at: '2026-01-01T00:00:00',
  has_solver_config: true, bus_count: 5, snapshot_count: 24, objective: null,
}

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={qc}>
      <NewProjectWizard existingProjects={[EXISTING]} onConfirm={() => {}} onClose={onClose} isPending={false} initialTab="study" />
    </QueryClientProvider>,
  )
  return { onClose }
}

beforeEach(() => vi.clearAllMocks())

describe('NewProjectWizard — Study tab', () => {
  it('offers a Study tab beside the network tabs', () => {
    renderWizard()
    expect(screen.getByRole('button', { name: /^study$/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /create study/i })).toBeTruthy()
  })

  it('posts the typed config as numbers and opens the study on success', async () => {
    vi.mocked(gridspineApi.createStudy).mockResolvedValue({
      id: 'new-id', name: 'Winter 2030', kind: 'planning_dynamics',
      config: { hours: 336, k: 2, window: 168, overlap: 24, screen: false, n2_prune_threshold_pct: 0, from_dispatch: null },
      status: { status: 'not started', resumable: false, error: null, selected_hours: [], converged_hours: [], bundles: {}, stages: {} as never },
    })
    const { onClose } = renderWizard()
    await userEvent.type(screen.getByLabelText('Study name'), 'Winter 2030')
    const hours = screen.getByLabelText('Hours')
    await userEvent.clear(hours); await userEvent.type(hours, '336')
    const k = screen.getByLabelText('k')
    await userEvent.clear(k); await userEvent.type(k, '2')
    await userEvent.click(screen.getByLabelText('Screen contingencies'))   // on → off
    await userEvent.click(screen.getByRole('button', { name: /create study/i }))

    await waitFor(() => expect(gridspineApi.createStudy).toHaveBeenCalledTimes(1))
    expect(gridspineApi.createStudy).toHaveBeenCalledWith('Winter 2030', {
      hours: 336, k: 2, window: 168, overlap: 24, screen: false,
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('renders the backend’s 422 inline and keeps the dialog open', async () => {
    vi.mocked(gridspineApi.createStudy).mockRejectedValue({
      response: { status: 422, data: { detail: 'window must be a positive whole number of days, got 25' } },
    })
    const { onClose } = renderWizard()
    await userEvent.type(screen.getByLabelText('Study name'), 'Bad Window')
    await userEvent.click(screen.getByRole('button', { name: /create study/i }))
    expect((await screen.findByRole('alert')).textContent).toContain('whole number of days')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('refuses a name that already exists before making any request', async () => {
    renderWizard()
    await userEvent.type(screen.getByLabelText('Study name'), 'taken')
    expect(screen.getByText(/already exists/i)).toBeTruthy()
    expect((screen.getByRole('button', { name: /create study/i }) as HTMLButtonElement).disabled).toBe(true)
    expect(gridspineApi.createStudy).not.toHaveBeenCalled()
  })
})
