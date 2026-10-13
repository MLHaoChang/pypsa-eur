// Quick win (guided-mode spec §2.10, obstacles 1 and 4): the section is named
// "Energy Hub reference design", and when a study finishes while the panel is
// mounted a sticky "Study finished — view report" pill scrolls the report
// into view (it sat below the fold with no cue that the run had ended).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
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
const RUNNING = { status: 'running', study: 'eh_study', archetype: 'strong_grid' }
const DONE = { status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT }

let scrolled: string[] = []

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  scrolled = []
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    scrolled.push((this as HTMLElement).dataset.testid ?? '?')
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function mount(client: QueryClient) {
  const user = userEvent.setup()
  render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
  await user.click(screen.getByTestId('eh-reference-design-toggle'))
  return user
}

/** Drive the study record from the test: set the cached payload directly. */
function setStudy(client: QueryClient, payload: unknown) {
  act(() => { client.setQueryData(nk('Demo', 'results', 'eh_study'), payload) })
}

describe('section name', () => {
  it('the header reads "Energy Hub reference design"', () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    const client = new QueryClient()
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    expect(screen.getByTestId('eh-reference-design-toggle').textContent)
      .toMatch(/^\s*Energy Hub reference design ▸$/)
  })
})

describe('finished cue', () => {
  it('appears on running → done and scrolls the report into view on click', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    const user = await mount(client)
    await waitFor(() => expect(screen.getByTestId('eh-run').textContent).toBe('Studying…'))
    expect(screen.queryByTestId('eh-study-finished-cue')).toBeNull()
    setStudy(client, DONE)
    const cue = await screen.findByTestId('eh-study-finished-cue')
    expect(cue.textContent).toBe('Study finished — view report')
    await screen.findByTestId('eh-report')
    await user.click(cue)
    expect(scrolled).toContain('eh-report')
    expect(screen.queryByTestId('eh-study-finished-cue')).toBeNull()
  })

  it('does not appear on a fresh mount with a done study', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    await mount(client)
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-study-finished-cue')).toBeNull()
  })

  it('hides on a new run and on a project switch', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    await mount(client)
    await waitFor(() => expect(screen.getByTestId('eh-run').textContent).toBe('Studying…'))
    setStudy(client, DONE)
    await screen.findByTestId('eh-study-finished-cue')
    setStudy(client, RUNNING)
    await waitFor(() => expect(screen.queryByTestId('eh-study-finished-cue')).toBeNull())
    setStudy(client, DONE)
    await screen.findByTestId('eh-study-finished-cue')
    // The other project's study is done too: only the switch can hide the cue.
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE as never)
    act(() => { useUIStore.setState({ currentProject: 'Other' }) })
    await waitFor(() => expect(screen.queryByTestId('eh-study-finished-cue')).toBeNull())
  })

  it('on failed, scrolls the error into view once (no cue)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    await mount(client)
    await waitFor(() => expect(screen.getByTestId('eh-run').textContent).toBe('Studying…'))
    setStudy(client, { ...RUNNING, status: 'failed', error: 'solver exploded' })
    await screen.findByTestId('eh-error')
    await waitFor(() => expect(scrolled).toEqual(['eh-error']))
    expect(screen.queryByTestId('eh-study-finished-cue')).toBeNull()
  })
})
