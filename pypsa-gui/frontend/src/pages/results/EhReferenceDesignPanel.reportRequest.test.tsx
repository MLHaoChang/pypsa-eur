// P24 (guided-mode spec §5.3, Results card "Open full report"): the hub-design
// card sends the user to Results → Adequacy and asks for the full report. The
// Expert panel starts collapsed, so it consumes `ehReportRequest`: it opens,
// scrolls `eh-report` into view once the report renders, and clears the
// request. Without a request the panel behaves exactly as before.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import {
  EhReferenceDesignPanel, ehStudyQueryKeys, ehStudyRefetchInterval,
} from './EhReferenceDesignPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReferenceDesign: none(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: none(), getEhDtcPlanning: none(),
      getEhReadiness: none(), getEhTemplate: none(), startEhStudy: vi.fn(),
    },
  }
})

const REPORT = {
  archetype: 'strong_grid', pack_hash: 'h', assumptions_hash: 'a',
  completeness: { target: 'ok' },
}
const DONE = { status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT }

let scrolled: string[] = []
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', ehReportRequest: false })
  scrolled = []
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    scrolled.push((this as HTMLElement).dataset.testid ?? '?')
  })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
}

describe('ehReportRequest', () => {
  it('opens the collapsed panel, scrolls the report into view and clears the request', async () => {
    useUIStore.getState().requestEhReport()
    mount()
    await screen.findByTestId('eh-report')
    await waitFor(() => expect(scrolled).toContain('eh-report'))
    expect(useUIStore.getState().ehReportRequest).toBe(false)
  })

  it('without a request the panel stays collapsed (Expert unchanged)', async () => {
    mount()
    await waitFor(() => expect(resultsApi.getEhStudy).toHaveBeenCalled())
    expect(screen.queryByTestId('eh-report')).toBeNull()
    expect(scrolled).toEqual([])
  })
})

describe('shared study query options (spec §9: cache shared with the cards)', () => {
  it('polls every 2 s only while the study runs', () => {
    const q = (data: unknown) => ({ state: { data } }) as never
    expect(ehStudyRefetchInterval(q({ status: 'running' }))).toBe(2000)
    expect(ehStudyRefetchInterval(q({ status: 'done' }))).toBe(false)
    expect(ehStudyRefetchInterval(q(null))).toBe(false)
  })

  it('the invalidation set includes the review the cards read', () => {
    expect(ehStudyQueryKeys('Demo')).toEqual(['eh_study', 'eh_reference_design',
      'eh_redundancy', 'eh_levers', 'eh_dtc', 'eh_dtc_planning', 'eh_review']
      .map(leaf => nk('Demo', 'results', leaf)))
  })
})
