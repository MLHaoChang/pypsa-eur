// Guided "Run grid checks" (owner decision A, UX review M4): draft, then run, behind one button.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RunGridChecks from './RunGridChecks'
import { DEFAULT_SETTINGS } from '../CampusElectricalPanel'

const api = vi.hoisted(() => ({ state: vi.fn(), draft: vi.fn(), run: vi.fn() }))
vi.mock('../../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../../api/campusElectrical')>('../../api/campusElectrical')
  return { ...real, campusApi: { ...real.campusApi, ...api } }
})

const mount = (again = false) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <RunGridChecks project="Hub" again={again} disabled={false} />
  </QueryClientProvider>)

beforeEach(() => { api.run.mockResolvedValue({ id: 'j1', status: 'queued' }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('RunGridChecks', () => {
  it('drafts the campus file when there is none, then runs with the default settings', async () => {
    api.state.mockResolvedValue({ campus_yaml: null, settings: null })
    api.draft.mockResolvedValue({ campus_yaml: 'campus: {}', skipped: [] })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run grid checks' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub', DEFAULT_SETTINGS))
    expect(api.draft).toHaveBeenCalledWith('Hub', false)
  })

  it('keeps an existing campus file and runs with the settings of the last run', async () => {
    const settings = { ...DEFAULT_SETTINGS, margin: 0.3 }
    api.state.mockResolvedValue({ campus_yaml: 'campus: {}', settings })
    mount(true)
    await userEvent.click(screen.getByRole('button', { name: 'Run the grid checks again' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub', settings))
    expect(api.draft).not.toHaveBeenCalled()
  })

  it('an unsolved project gets the Run the solve state, not a red sentence', async () => {
    api.state.mockResolvedValue({ campus_yaml: null, settings: null })
    api.draft.mockRejectedValue({ response: { status: 422, data: { detail: 'the project is not solved; solve it and save, then draft the campus' } } })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run grid checks' }))
    expect(await screen.findByRole('button', { name: 'Run the solve' })).toBeTruthy()
    expect(api.run).not.toHaveBeenCalled()
  })

  it('any other refusal is shown as the backend said it', async () => {
    api.state.mockResolvedValue({ campus_yaml: 'campus: {}', settings: null })
    api.run.mockRejectedValue({ response: { status: 422, data: { detail: 'pcc: sk_min_mva is above sk_max_mva' } } })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run grid checks' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/sk_min_mva/)
  })
})
