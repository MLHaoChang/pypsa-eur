// Guided-mode spec §5.3 / §5.7 (Improve): only high and medium findings;
// "Why" shows the evidence; "Let the assistant do this" / "Ask" hand the exact
// §5.7 texts to the assistant; Check risks starts the lifted FMEA sweep hook;
// the FMEA tab opens through requestResultsTab.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../../api/simulation'
import { useChatStore } from '../../../store/chatStore'
import { useUIStore } from '../../../store/uiStore'
import { useStartFmeaSweep } from '../../../hooks/useStartFmeaSweep'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { actionText, askText, stressScenarioText } from '../delegate'
import { DC_TEMPLATE, REPORT, review } from '../testFixtures'
import { ImproveCard } from './ImproveCard'

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhTemplate: vi.fn(),
    },
  }
})
const sweep = { mutate: vi.fn(), isPending: false, isSuccess: false }
vi.mock('../../../hooks/useStartFmeaSweep', () => ({ useStartFmeaSweep: vi.fn(() => sweep) }))

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign',
    resultsTabRequest: null, assistantDockOpen: false })
  useChatStore.setState({ composerSeed: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true,
    step: 'improve', archetype: 'weak_flexible' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'done', report: REPORT })
  vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
  sweep.mutate.mockReset()
  sweep.isSuccess = false
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ImproveCard /></QueryClientProvider>)
  return userEvent.setup()
}

const F = review().findings

describe('ImproveCard', () => {
  it('lists only the high and medium findings', async () => {
    mount()
    await screen.findByTestId('hub-improve-certification_fail')
    expect(screen.getByTestId('hub-improve-not_established_frontier')).toBeTruthy()
    expect(screen.queryByTestId('hub-improve-budget_tight')).toBeNull()
    expect(screen.queryByTestId('hub-improve-frontier_knee')).toBeNull()
    expect(screen.getByTestId('hub-improve-list').children.length).toBe(2)
  })

  it('Why toggles the evidence, labelled as technical', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-why-certification_fail'))
    const ev = screen.getByTestId('hub-improve-evidence-certification_fail')
    expect(ev.textContent).toMatch(/technical evidence/i)
    expect(ev.textContent).toContain('lole_h_per_year')
    expect(ev.textContent).toContain('12.38')
    await user.click(screen.getByTestId('hub-improve-why-certification_fail'))
    expect(screen.queryByTestId('hub-improve-evidence-certification_fail')).toBeNull()
  })

  it('Do → the §5.7 text with JSON.stringify(args); no Do without an action', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-do-certification_fail'))
    expect(useChatStore.getState().composerSeed).toBe(actionText(F[0]))
    expect(useChatStore.getState().composerSeed).toContain(JSON.stringify(F[0].actions[0].args))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(screen.queryByTestId('hub-improve-do-not_established_frontier')).toBeNull()
  })

  it('Ask → the explain text', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-ask-not_established_frontier'))
    expect(useChatStore.getState().composerSeed).toBe(askText(F[1]))
  })

  it('Check risks (FMEA) runs the lifted sweep hook', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-fmea'))
    expect(useStartFmeaSweep).toHaveBeenCalled()
    expect(sweep.mutate).toHaveBeenCalledTimes(1)
  })

  it('open FMEA → Results with the fmea tab requested', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-open-fmea'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('fmea')
  })

  it('Add a stress scenario → the registry request with the site context', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-add-stress'))
    expect(useChatStore.getState().composerSeed).toBe(
      stressScenarioText('a weak-grid site like the Data Center Energy Hub example'))
  })

  it('no high or medium finding: says so', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ findings: [F[2], F[3]] }))
    mount()
    expect((await screen.findByTestId('hub-improve-none')).textContent).toMatch(/nothing urgent/i)
  })
})
