// "Generate defaults for all" held to the backend's contract: the rows show
// why each default was picked (assumed), skipped components say why, peak
// sizing re-previews with that row named, and Apply posts the ticked rows'
// job ids and reports the rows that were kept.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import DefaultsReview from './DefaultsReview'

const api = vi.hoisted(() => ({ previewDefaults: vi.fn(), applyDefaults: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: api }
})

const ROWS = [
  { family: 'load', component_type: 'loads', attribute: 'p_set', component: 'office', job_id: 'j1',
    kind: 'commercial', reason: "matched 'office' in the load name (a commercial name) → commercial",
    assumed: true, sizing: 'mean', static_p_set_mw: 2, stats: { peak_mw: 3.4, mean_mw: 2 },
    warnings: ['default_peak_above_connection: the default\'s peak 3.40 MW is above 3.00 MW'] },
  { family: 'load', component_type: 'loads', attribute: 'p_set', component: 'misc', job_id: 'j2',
    kind: 'commercial', reason: "no match in the name 'misc'; the ac bus's default → commercial",
    assumed: true, sizing: 'mean', static_p_set_mw: 1, stats: { peak_mw: 1.7, mean_mw: 1 }, warnings: [] },
  { family: 'load', component_type: 'loads', attribute: 'p_set', component: 'metered',
    skipped: 'carries a measured upload; never replaced' },
]

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><DefaultsReview project="Site A" /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.previewDefaults.mockResolvedValue({ rows: ROWS })
})
afterEach(() => cleanup())

describe('DefaultsReview', () => {
  it('shows each default with its reason, and the skipped rows with theirs', async () => {
    renderIt()
    expect((await screen.findByTestId('reason-office')).textContent).toContain("matched 'office'")
    expect(screen.getAllByText('assumed')).toHaveLength(2)
    // A plain title on one line; the sentence and the code behind a disclosure.
    expect(screen.getByText('Peak above the grid connection').tagName).toBe('SUMMARY')
    expect(screen.getByTestId('defaults-skipped').textContent).toContain('metered: carries a measured upload')
  })

  it('re-previews a row sized by its peak', async () => {
    renderIt()
    await screen.findByTestId('reason-office')
    const row = screen.getByTestId('reason-office').closest('tr') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: 'Peak' }))
    await waitFor(() => expect(api.previewDefaults).toHaveBeenLastCalledWith('Site A',
      { replace_generated: false, peak_sized: ['office'] }))
  })

  it('applies the ticked rows and reports the ones kept', async () => {
    api.applyDefaults.mockResolvedValue({ applied: 1, undo: 'one undo', results: [
      { job_id: 'j1', component: 'office', applied: false, error_kind: 'measured_series_kept',
        message: "'office' carries a measured upload now; a default never replaces it" },
      { job_id: 'j2', component: 'misc', applied: true }] })
    renderIt()
    await screen.findByTestId('reason-office')
    await userEvent.click(screen.getByRole('button', { name: /Apply 2 defaults/ }))
    await waitFor(() => expect(api.applyDefaults).toHaveBeenCalledWith('Site A', ['j1', 'j2']))
    const out = await screen.findByTestId('defaults-result')
    expect(out.textContent).toContain('Applied 1. One undo reverts them all.')
    expect(out.textContent).toContain('office: \'office\' carries a measured upload now')
  })

  it('leaves an unticked row out', async () => {
    api.applyDefaults.mockResolvedValue({ applied: 1, undo: '', results: [] })
    renderIt()
    await screen.findByTestId('reason-office')
    await userEvent.click(screen.getByLabelText('Apply the default for misc'))
    await userEvent.click(screen.getByRole('button', { name: /Apply 1 default$/ }))
    await waitFor(() => expect(api.applyDefaults).toHaveBeenCalledWith('Site A', ['j1']))
  })
})
