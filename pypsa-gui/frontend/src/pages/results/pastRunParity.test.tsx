// LC-03 past-run parity: with a past run on Results, the group header names
// that run, never "From the latest run", and the Export chooser says which run
// the workbook holds and offers the past run's kept results in place.
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { resultsEvidenceApi } from '../../api/resultsEvidence'
import { useUIStore } from '../../store/uiStore'
import GroupHeader from './GroupHeader'
import { CARD_GROUPS } from './groups'
import { runWhen, runWhenPair } from './runWhen'
import { WorkbookRunNote } from '../../layout/sidebar/ResultsWorkbookPicker'

vi.mock('../../api/resultsEvidence', async importOriginal => {
  const real = await importOriginal<typeof import('../../api/resultsEvidence')>()
  return { ...real, resultsEvidenceApi: { ...real.resultsEvidenceApi, run: vi.fn(), runFile: vi.fn() } }
})

const PAST = 'a1b2c3d4e5f6a7b8'
const LATEST = 'ffeeddccbbaa9988'
const PAST_AT = '2026-10-31T13:50:00Z'
const LATEST_AT = '2026-10-31T14:05:00Z'
const solved = { running: false, dispatch: 'fresh', objective: 1 } as never
const group = CARD_GROUPS.find(g => g.tabs.length > 0 && !g.fromStudy)!

function wrap(node: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {node}
    </QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'oj-grid', discussedRun: null })
  vi.mocked(resultsEvidenceApi.run).mockResolvedValue({
    status: 'ok',
    binding: { run_id: PAST } as never,
    data: { run_id: PAST, status: 'completed', started_at: PAST_AT, metrics: {} },
  })
})
afterEach(() => { vi.clearAllMocks(); useUIStore.setState({ discussedRun: null }) })

describe('runWhen', () => {
  it('names a run by its start, with seconds only when two runs share a minute', () => {
    expect(runWhen(null)).toBeNull()
    expect(runWhen('not a date')).toBeNull()
    expect(runWhenPair(PAST_AT, LATEST_AT)).toEqual([runWhen(PAST_AT), runWhen(LATEST_AT)])
    const [a, b] = runWhenPair('2026-10-31T14:05:01Z', '2026-10-31T14:05:40Z')
    expect(a).not.toEqual(b)
  })
})

describe('GroupHeader with a past run', () => {
  it('says the latest run without one', () => {
    wrap(<GroupHeader group={group} status={solved} outsideScope={false} />)
    expect(screen.getByTestId('results-ghead-freshness').textContent).toContain('From the latest run')
  })

  it('names the past run, never the latest', () => {
    wrap(<GroupHeader group={group} status={solved} outsideScope={false}
      pastRun={{ runId: PAST, startedAt: PAST_AT }} />)
    const shown = screen.getByTestId('results-ghead-freshness')
    expect(shown.getAttribute('data-freshness')).toBe('past_run')
    expect(shown.getAttribute('title')).toBe(`Run ${PAST}`)
    expect(shown.textContent).toContain(`From the run of ${runWhen(PAST_AT)}`)
    expect(screen.queryByText('From the latest run')).toBeNull()
  })

  it('falls back to a short id without a start time, and hides the latest solve\'s failure', () => {
    const failedLatest = { running: false, dispatch: 'fresh', objective: 1, failure: { title: 'Infeasible' } } as never
    wrap(<GroupHeader group={group} status={failedLatest} outsideScope={false}
      pastRun={{ runId: PAST, startedAt: null }} />)
    expect(screen.getByTestId('results-ghead-freshness').textContent).toContain(`From past run ${PAST.slice(0, 8)}`)
    expect(screen.queryByTestId('results-ghead-failed')).toBeNull()
  })
})

describe('WorkbookRunNote', () => {
  const run = { run_id: LATEST, started_at: LATEST_AT }

  it('says which run the workbook holds, with no download link and no stub', () => {
    wrap(<WorkbookRunNote run={run} />)
    expect(screen.getByTestId('workbook-run').textContent).toContain(`Holds the latest run, from ${runWhen(LATEST_AT)}.`)
    expect(screen.queryByTestId('workbook-past-run-download')).toBeNull()
  })

  it('with a past run on Results, names both runs and downloads the past run\'s section', async () => {
    useUIStore.setState({ discussedRun: { runId: PAST, section: 'results_capacity' } })
    vi.mocked(resultsEvidenceApi.runFile).mockResolvedValue({ blob: new Blob(['{}']), filename: `run_${PAST}_results_capacity.json` })
    const createObjectURL = vi.fn(() => 'blob:x')
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() })
    wrap(<WorkbookRunNote run={run} />)
    const note = await screen.findByText(new RegExp(`Results shows the run of ${runWhen(PAST_AT)}`))
    expect(screen.getByTestId('workbook-past-run').textContent).toContain(
      `Results shows the run of ${runWhen(PAST_AT)}: this workbook holds the latest run, from ${runWhen(LATEST_AT)}.`)
    expect(note).toBeTruthy()
    const link = screen.getByTestId('workbook-past-run-download')
    expect(link.textContent).toContain(`Download the kept results of the run of ${runWhen(PAST_AT)} (JSON)`)
    await userEvent.click(link)
    await waitFor(() => expect(resultsEvidenceApi.runFile).toHaveBeenCalledWith(PAST, 'results_capacity'))
    expect(createObjectURL).toHaveBeenCalled()
  })

  it('shows a refused download inline in plain words', async () => {
    useUIStore.setState({ discussedRun: { runId: PAST, section: 'results_capacity' } })
    const message = "No project is open for you now, so this run's kept results are not shown."
    const body = new Blob([JSON.stringify({ detail: { error_kind: 'run_project_required', message } })])
    vi.mocked(resultsEvidenceApi.runFile).mockRejectedValue({ response: { status: 409, data: body } })
    wrap(<WorkbookRunNote run={run} />)
    await userEvent.click(await screen.findByTestId('workbook-past-run-download'))
    expect((await screen.findByTestId('workbook-past-run-refused')).textContent).toContain(message)
    expect(screen.queryByText(/run_project_required/)).toBeNull()
  })

  it('says nothing about a past run when Results shows the latest', () => {
    useUIStore.setState({ discussedRun: { runId: LATEST } })
    wrap(<WorkbookRunNote run={run} />)
    expect(screen.queryByTestId('workbook-past-run')).toBeNull()
  })
})
