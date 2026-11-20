// Template-e2e 22 (IMPROVE-MANUAL-1): an Improve card's study re-run arrives
// as the filled-in form: the panel and its settings open, nothing runs, and
// "Filled in from the Improve card" stands only while the form is the one
// handed over (UX 2 S1 condition 3). Another project's never applies (E3).
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { EMPTY_PACK_FORM } from './EhReferenceDesignPanel'
import { EhReferenceDesignPanel } from './EhReferenceDesignPanel'
import { useImprovePrefillStore } from '../hubDesign/improvePrefill'

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

const PACK = { ...EMPTY_PACK_FORM, budget: '40', stages: ['redundancy'] }

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', ehReportRequest: false })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks(); useImprovePrefillStore.setState({ prefill: null }) })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
}

it('opens with the form filled in and says where from; nothing runs', async () => {
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Price a spare', pack: PACK })
  mount()
  const note = await screen.findByTestId('eh-filled-from-improve')
  expect(note.textContent).toBe('Filled in from the Improve card: Price a spare. Review and run.')
  expect(screen.getByTestId('eh-pack-settings')).toBeTruthy()
  expect((screen.getByTestId('eh-pack-budget') as HTMLInputElement).value).toBe('40')
  expect(resultsApi.startEhStudy).not.toHaveBeenCalled()
  expect(useImprovePrefillStore.getState().prefill).toBeNull()
})

it('the note goes once the user edits a field', async () => {
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Price a spare', pack: PACK })
  mount()
  await screen.findByTestId('eh-filled-from-improve')
  fireEvent.change(screen.getByTestId('eh-pack-budget'), { target: { value: '41' } })
  await waitFor(() => expect(screen.queryByTestId('eh-filled-from-improve')).toBeNull())
  // Putting the value back does not bring the note back: it described the hand-over.
  fireEvent.change(screen.getByTestId('eh-pack-budget'), { target: { value: '40' } })
  expect(screen.queryByTestId('eh-filled-from-improve')).toBeNull()
})

it('another project’s prefill never applies', async () => {
  useImprovePrefillStore.getState().set({ project: 'Other', title: 'Price a spare', pack: PACK })
  mount()
  await waitFor(() => expect(resultsApi.getEhStudy).toHaveBeenCalled())
  expect(screen.queryByTestId('eh-filled-from-improve')).toBeNull()
  expect(screen.queryByTestId('eh-pack-settings')).toBeNull()
  expect(useImprovePrefillStore.getState().prefill).toBeNull()
})

// GK B3 E5: a viewer's run of the filled-in study is refused in plain words.
it('a viewer’s run is refused in the gate’s plain words, never the code', async () => {
  const MESSAGE = 'You can view this project but not change it. Ask its owner for edit access.'
  vi.mocked(resultsApi.startEhStudy).mockRejectedValueOnce(
    { response: { status: 403, data: { detail: { error_kind: 'project_view_only', message: MESSAGE } } } })
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Price a spare', pack: PACK })
  mount()
  await screen.findByTestId('eh-filled-from-improve')
  const run = screen.getByTestId('eh-run') as HTMLButtonElement
  await waitFor(() => expect(run.disabled).toBe(false))
  fireEvent.click(run)
  expect((await screen.findByTestId('eh-blocked')).textContent).toContain(MESSAGE)
  expect(document.body.textContent).not.toContain('project_view_only')
})

it('a template that loads after the hand-over does not overwrite the form', async () => {
  vi.mocked(resultsApi.getEhTemplate).mockImplementation(
    () => new Promise(r => setTimeout(() => r({ pack_overrides: {}, stages: ['frontier'] } as never), 30)))
  // A goal-only finding hands over the empty form: the template must not
  // take it for an untouched one.
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Tighter target', pack: EMPTY_PACK_FORM })
  mount()
  await screen.findByTestId('eh-filled-from-improve')
  await waitFor(() => expect(resultsApi.getEhTemplate).toHaveBeenCalled())
  await new Promise(r => setTimeout(r, 80))
  expect(screen.getByTestId('eh-filled-from-improve')).toBeTruthy()
})

it('a project switch takes the note away, and switching back does not bring it', async () => {
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Price a spare', pack: PACK })
  mount()
  await screen.findByTestId('eh-filled-from-improve')
  act(() => useUIStore.setState({ currentProject: 'Other' }))
  await waitFor(() => expect(screen.queryByTestId('eh-filled-from-improve')).toBeNull())
  act(() => useUIStore.setState({ currentProject: 'Demo' }))
  await new Promise(r => setTimeout(r, 30))
  expect(screen.queryByTestId('eh-filled-from-improve')).toBeNull()
})
