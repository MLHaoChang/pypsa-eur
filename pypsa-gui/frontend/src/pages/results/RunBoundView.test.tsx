import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RunBoundView from './RunBoundView'
import { resultsEvidenceApi } from '../../api/resultsEvidence'
import { useUIStore } from '../../store/uiStore'

vi.mock('../../api/resultsEvidence', async importOriginal => ({
  ...await importOriginal<typeof import('../../api/resultsEvidence')>(),
  resultsEvidenceApi: { loadflow: vi.fn(), run: vi.fn() },
}))

const REASON = 'This project is not available to you: your access was removed or the project was deleted. '
  + 'Its runs and their kept results are not shown.'

function refused(detail: unknown) {
  return Object.assign(new Error('Request failed with status code 404'), { response: { status: 404, data: { detail } } })
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <RunBoundView run={{ runId: 'fg-old', resultsTab: 'capex', section: 'results_capacity' }} tab="capex" />
  </QueryClientProvider>)
}

describe('RunBoundView refusals (LC-T08)', () => {
  beforeEach(() => {
    useUIStore.setState({ currentProject: 'oj-grid' })
    vi.mocked(resultsEvidenceApi.run).mockReset()
  })

  it('says plainly when access to the project was lost since the run', async () => {
    vi.mocked(resultsEvidenceApi.run).mockRejectedValue(refused({ error_kind: 'project_unavailable', message: REASON }))
    show()
    expect(await screen.findByText(REASON)).toBeTruthy()
    expect(screen.getByRole('status').textContent).toBe(REASON)   // the server's words, in full
    expect(screen.queryByText(/not been substituted/)).toBeNull()
    expect(screen.queryByText(/native retained evidence/)).toBeNull()
  })

  it('says plainly when no project is open for the user now, as after access loss', async () => {
    const open = "No project is open for you now, so this run's kept results are not shown. "
      + 'If the project was closed elsewhere or your access was removed, open it again from Projects.'
    vi.mocked(resultsEvidenceApi.run).mockRejectedValue(refused({ error_kind: 'run_project_required', message: open }))
    show()
    expect(await screen.findByText(open)).toBeTruthy()
    expect(screen.getByRole('status').textContent).toBe(open)
    expect(screen.queryByText(/run_project_required/)).toBeNull()
    expect(screen.queryByText(/not been substituted/)).toBeNull()
  })

  it('keeps the unavailable line for any other refusal', async () => {
    vi.mocked(resultsEvidenceApi.run).mockRejectedValue(refused({ error_kind: 'evidence_unavailable', message: 'x' }))
    show()
    expect(await screen.findByText(/Retained results_capacity evidence unavailable for run fg-old\. Current results have not been substituted\./))
      .toBeTruthy()
  })
})
