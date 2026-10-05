// Bug 2 follow-up: the panels that poll a study invalidate the shared
// `simulationStatus` query (the dock greeting's source) when the study ends,
// so the greeting stops saying "Not solved yet." after a sweep re-solved.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import FmeaTab from './FmeaTab'
import { EhReferenceDesignPanel } from './EhReferenceDesignPanel'
import { FrontierPanel } from './FrontierPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getFmeaModes: vi.fn(), getWorksheet: vi.fn(), getStressScenarios: none(),
      getStressProfilePacks: none(),
      getEhStudy: vi.fn(), getEhReferenceDesign: none(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: none(), getEhDtcPlanning: none(),
      getEhReadiness: none(), getEhTemplate: none(), getFrontier: vi.fn(),
    },
  }
})

const STATUS_KEY = JSON.stringify(nk('Demo', 'simulationStatus'))
let qc: QueryClient
let spy: ReturnType<typeof vi.spyOn>
const statusInvalidations = () =>
  spy.mock.calls.filter(([f]: [{ queryKey?: unknown }]) => JSON.stringify(f?.queryKey) === STATUS_KEY).length

function mount(ui: ReactNode) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  spy = vi.spyOn(qc, 'invalidateQueries')
  render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}
const set = (key: unknown[], v: unknown) => act(() => { qc.setQueryData(key, v) })

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getWorksheet).mockResolvedValue({ version: 1, manual_rows: [], overlays: {} })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('study completion refreshes simulationStatus', () => {
  it('FMEA sweep running → done', async () => {
    const key = nk('Demo', 'results', 'fmea_modes')
    vi.mocked(resultsApi.getFmeaModes).mockResolvedValue(
      { per_mode: [], sweep_status: 'running', sweep_error: null } as never)
    mount(<FmeaTab />)
    await waitFor(() => expect(qc.getQueryData(key)).toBeTruthy())
    expect(statusInvalidations()).toBe(0)
    set(key, { per_mode: [], sweep_status: 'done', sweep_error: null })
    await waitFor(() => expect(statusInvalidations()).toBe(1))
  })

  it('EH study running → done', async () => {
    const key = nk('Demo', 'results', 'eh_study')
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running' } as never)
    mount(<EhReferenceDesignPanel />)
    await waitFor(() => expect(qc.getQueryData(key)).toBeTruthy())
    set(key, { status: 'done', report: null })
    await waitFor(() => expect(statusInvalidations()).toBe(1))
  })

  it('frontier running → done', async () => {
    const key = nk('Demo', 'results', 'frontier')
    vi.mocked(resultsApi.getFrontier).mockResolvedValue({ status: 'running' } as never)
    mount(<FrontierPanel />)
    await waitFor(() => expect(qc.getQueryData(key)).toBeTruthy())
    set(key, { status: 'done' })
    await waitFor(() => expect(statusInvalidations()).toBe(1))
  })
})
