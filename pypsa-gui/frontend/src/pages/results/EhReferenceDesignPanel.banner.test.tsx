// Quick win (guided-mode spec §2.10): the template banner stayed hidden while
// a study ran, and readiness unmounted — the panel lost its context exactly
// when the user was waiting. The banner stays; readiness says it is paused.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { EhReferenceDesignPanel } from './EhReferenceDesignPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReferenceDesign: none(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: none(), getEhDtcPlanning: none(),
      getEhReadiness: none(), getEhTemplate: vi.fn(),
    },
  }
})

const TEMPLATE = {
  id: 'eh_datacenter', name: 'Data center', recommended_archetype: 'strong_grid',
  pack_overrides: {}, study_notes: ['Grid import is the only feed.'],
  provenance: 'synthetic example data',
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(TEMPLATE as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('template banner while a study runs', () => {
  it('stays visible with status running, and readiness shows the paused line', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(
      { status: 'running', study: 'eh_study', archetype: 'strong_grid' } as never)
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    await user.click(screen.getByTestId('eh-reference-design-toggle'))
    await waitFor(() => expect(screen.getByTestId('eh-run').textContent).toBe('Studying…'))
    expect(await screen.findByTestId('eh-template-banner')).toBeTruthy()
    expect(screen.getByTestId('eh-readiness-paused').textContent)
      .toBe('Readiness is paused while the study runs')
    expect(resultsApi.getEhReadiness).not.toHaveBeenCalled()
  })
})
