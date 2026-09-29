// P24 (guided-mode spec §5.3, §5.9): the hub-design Results / Improve cards
// read GET /results/eh_review under `nk(project, 'results', 'eh_review')`.
// Starting a study from the Expert panel invalidates that key together with
// the panel's own set, so a card never shows the previous study's review.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { EhReferenceDesignPanel } from './EhReferenceDesignPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: none(), getEhReferenceDesign: none(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: none(), getEhDtcPlanning: none(),
      getEhReadiness: none(), getEhTemplate: none(), startEhStudy: vi.fn(),
    },
  }
})

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.startEhStudy).mockResolvedValue(
    { status: 'running', study: 'eh_study', archetype: 'strong_grid' })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('eh_review invalidation', () => {
  it('Run invalidates the eh_review key with the panel set', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const inv = vi.spyOn(client, 'invalidateQueries')
    const user = userEvent.setup()
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    await user.click(screen.getByTestId('eh-reference-design-toggle'))
    await user.click(screen.getByTestId('eh-run'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalled())
    const keys = () => inv.mock.calls.map(([f]) => JSON.stringify(f?.queryKey))
    await waitFor(() => expect(keys()).toContain(
      JSON.stringify(nk('Demo', 'results', 'eh_review'))))
    expect(keys()).toContain(JSON.stringify(nk('Demo', 'results', 'eh_study')))
  })
})
