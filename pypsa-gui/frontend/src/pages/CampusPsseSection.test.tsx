// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PsseBundles } from '../api/campusElectrical'
import { CampusPsseSection } from './CampusElectricalPanel'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: Object.assign((sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }), { subscribe: () => () => {} }),
}))
const api = vi.hoisted(() => ({ psse: vi.fn(), psseZip: vi.fn() }))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const listing = (over: Partial<PsseBundles> = {}): PsseBundles => ({
  has_run: true, stale: false, run_at: '2026-11-24T08:00:00+00:00', skipped: null, error: null,
  bundles: [{ name: 'case_p2030_h12', period: 2030, hour: 12, size: 4096,
    files: [{ name: 'case.raw', size: 2048 }, { name: 'case.dyr', size: 2048 }] }],
  ...over,
})

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusPsseSection name="Hub A" /></QueryClientProvider>)
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => cleanup())

describe('the PSS/E case section of the grid-code results', () => {
  it('offers Download PSS/E case per bundle and saves the zip', async () => {
    api.psse.mockResolvedValue(listing())
    api.psseZip.mockResolvedValue({ blob: new Blob(['PK']), stale: false })
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /^Download PSS\/E case/ }))
    await waitFor(() => expect(api.psseZip).toHaveBeenCalledWith('Hub A', 'case_p2030_h12'))
    expect(screen.queryByTestId('psse-stale')).toBeNull()
    expect(screen.getByText(/Period 2030, hour 12/)).toBeTruthy()
    expect(screen.getByText(/From the run of 2026-11-24 08:00 UTC/)).toBeTruthy()
  })

  it('says a bundle from a run older than the last edit is stale', async () => {
    api.psse.mockResolvedValue(listing({ stale: true }))
    renderSection()
    expect((await screen.findByTestId('psse-stale')).textContent).toMatch(/out of date/)
  })

  it('has no button and names what to run when there is no run', async () => {
    api.psse.mockResolvedValue(listing({ has_run: false, bundles: [], run_at: null }))
    renderSection()
    expect((await screen.findByTestId('psse-none')).textContent).toMatch(/Run the study in the Grid study panel/)
    expect(screen.queryByRole('button', { name: /^Download PSS\/E case/ })).toBeNull()
  })

  it('shows why a run wrote none, in place of the button', async () => {
    api.psse.mockResolvedValue(listing({ bundles: [], skipped: 'no island critical hours in this run' }))
    renderSection()
    expect((await screen.findByTestId('psse-skipped')).textContent).toMatch(/no island critical hours/)
    expect(screen.queryByRole('button', { name: /^Download PSS\/E case/ })).toBeNull()
  })

  it('shows a write error in place of the button', async () => {
    api.psse.mockResolvedValue(listing({ bundles: [], error: 'disk full' }))
    renderSection()
    const text = (await screen.findByTestId('psse-write-error')).textContent
    expect(text).toMatch(/could not write the PSS\/E cases/)
    expect(text).not.toMatch(/disk full/)               // the raw error stays out of the screen
    expect(screen.queryByRole('button', { name: /^Download PSS\/E case/ })).toBeNull()
  })

  it('says when the download fails', async () => {
    api.psse.mockResolvedValue(listing())
    api.psseZip.mockRejectedValue(new Error('network down'))
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /^Download PSS\/E case/ }))
    const text = (await screen.findByTestId('psse-download-error')).textContent
    expect(text).toBe('The PSS/E case could not be downloaded. Try again.')
    expect(text).not.toMatch(/network down/)               // the raw request text stays off the screen
  })

  it('says in plain words when the list cannot be loaded', async () => {
    api.psse.mockRejectedValue(new Error('Request failed with status code 500'))
    renderSection()
    const text = (await screen.findByTestId('psse-error')).textContent
    expect(text).toBe('The PSS/E cases could not be loaded. Reload the page to try again.')
    expect(text).not.toMatch(/status code/)
  })
})
