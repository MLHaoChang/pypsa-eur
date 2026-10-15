import { act, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import BranchLoading from './BranchLoading'
import { resultsEvidenceApi, type LoadflowView } from '../../api/resultsEvidence'
import { useUIStore } from '../../store/uiStore'
vi.mock('../../api/resultsEvidence', () => ({ resultsEvidenceApi: { loadflow: vi.fn() } }))
const view: LoadflowView = {
  status: 'ok', binding: { version: 1, project_id: 'project-1', project_name: 'Demo', scope: 'current', source: 'lopf', input_sha256: 'b'.repeat(64), window_from: 1, window_to: 2, data_sha256: 'a'.repeat(64) },
  window: { from: 1, to: 2, snapshots: 2, weighted_hours: 2 },
  rows: [{ name: 'L_p1', class: 'Line', basis: 'active_power_proxy', util_pct: 60, rating_pct: 60, mean_util_pct: 50, peak_flow: 60, mean_flow: 50, peak_at: 'hour 1', h_at_limit: 0, h_over_limit: 0, rating: { value: 100, basis: 'configured' }, s_max_pu: { min: 1, max: 1 } }],
  skipped: [{ name: 'Unrated', class: 'Line', reason: 'rating_missing_or_zero' }],
}
function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BranchLoading from={1} to={2} enabled /></QueryClientProvider>)
}
beforeEach(() => { vi.clearAllMocks(); useUIStore.setState({ currentProject: 'Demo', resultSource: 'lopf' }); vi.mocked(resultsEvidenceApi.loadflow).mockResolvedValue(view) })
it('renders the shared terminal/weighted values and exact window, with unavailable branches separate', async () => {
  renderView()
  const row = await screen.findByText('Line: L_p1')
  expect(row.closest('tr')?.textContent).toContain('60.00')
  expect(row.closest('tr')?.textContent).toContain('50.00')
  expect(resultsEvidenceApi.loadflow).toHaveBeenCalledWith('lopf', 1, 2)
  expect(JSON.parse(screen.getByTestId('branch-loading').dataset.resultBinding!)).toEqual(view.binding)
  expect(screen.getByText(/Unrated.*unavailable/)).toBeTruthy()
})
it('does not attest data from a different project even if numbers are present', async () => {
  vi.mocked(resultsEvidenceApi.loadflow).mockResolvedValue({ ...view, binding: { ...view.binding!, project_name: 'Other' } })
  renderView()
  await screen.findByText('Matching project evidence unavailable.')
  expect(document.querySelector('[data-result-binding]')).toBeNull()
  expect(screen.queryByText('Line: L_p1')).toBeNull()
})
it('shows configured and optimized capacity and highlights expansion from the same bound evidence', async () => {
  vi.mocked(resultsEvidenceApi.loadflow).mockResolvedValue({ ...view, rows: [
    { ...view.rows![0], rating: { value: 200, basis: 'optimized' }, capacity: { configured: 10, optimized: 200, extendable: true } },
  ] })
  renderView()
  const row = (await screen.findByText('Line: L_p1')).closest('tr')!
  expect(row.textContent).toContain('10.00')
  expect(screen.getByText('200.00 · expanded').className).toContain('text-accent font-semibold')
  expect(screen.getByRole('columnheader', { name: 'Optimized MVA (s_nom_opt)' })).toBeTruthy()
})
it('removes the old data marker while a new source is fetching', async () => {
  let release: (data: LoadflowView) => void = () => {}
  renderView()
  await screen.findByText('Line: L_p1')
  vi.mocked(resultsEvidenceApi.loadflow).mockImplementation(() => new Promise(r => { release = r }))
  act(() => useUIStore.getState().setResultSource('ac_pf'))
  await screen.findByText('Loading matching load-flow evidence…')
  expect(document.querySelector('[data-result-binding]')).toBeNull()
  await act(async () => release({ ...view, status: 'unavailable', message: 'AC convergence unavailable.' }))
  await screen.findByText('AC convergence unavailable.')
  expect(document.querySelector('[data-result-binding]')).toBeNull()
})
