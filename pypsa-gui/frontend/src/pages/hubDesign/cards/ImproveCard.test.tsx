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

  // P24-FE gate B3 (spec §5.8): engine prose — stage ids, ‱, attribute
  // names, LOLE / ENS / N+1 — appears only inside the "Why" evidence list.
  const ENGINE = [
    { id: 'certification_fail', severity: 'high' as const,
      title: 'Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target — outage-driven',
      evidence: { lole_h_per_year: 12.383928571428568,
        lole_ci_per_horizon: [7.68316774385459, 17.084689399002553], draws: 500 },
      recommendation: 'The plan (achieved ENS 0 ‱) already serves demand; add an N+1 unit or a higher p_nom_min. Sizing islanded operation (dtc_planning) shows how much.',
      actions: [{ tool: 'run_eh_study', args: {}, effect: 'size the local capacity that islanded operation needs (dtc_stress + dtc_planning)' }] },
    { id: 'not_established_frontier', severity: 'medium' as const, title: 'frontier: not established',
      evidence: { section: 'frontier' }, recommendation: 'The frontier stage needs VOLL > 0.',
      actions: [] },
    { id: 'fmea_dominant_mode', severity: 'medium' as const,
      title: 'site_transformer carries 93% of ranked Link risk',
      evidence: {}, recommendation: 'The redundancy stage prices GENERIC N+1 options.',
      actions: [{ tool: 'run_eh_study', args: {}, effect: 'price generic N / N+1 / storage scenarios (indicative)' }] },
    { id: 'ens_target_missed', severity: 'high' as const, title: 'ENS target missed: 12 ‱ vs 10 ‱',
      evidence: {}, recommendation: 'Raise p_nom_max.', actions: [] },
  ]
  const FORBIDDEN = ['‱', 'dtc_', 'p_nom', 'stage', 'LOLE', 'ENS', 'N+1',
    'ranked', 'Link risk', 'generic', 'indicative', 'scenarios']

  function outsideEvidence(): string {
    const card = screen.getByTestId('hub-card-improve').cloneNode(true) as HTMLElement
    card.querySelectorAll('[data-testid^="hub-improve-evidence-"]').forEach(e => e.remove())
    return card.textContent ?? ''
  }

  it('no engine terms outside the Why evidence (B3)', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ findings: ENGINE }))
    const user = mount()
    await screen.findByTestId('hub-improve-certification_fail')
    for (const f of ENGINE) await user.click(screen.getByTestId(`hub-improve-why-${f.id}`))
    const text = outsideEvidence()
    for (const w of FORBIDDEN) expect({ w, found: text.includes(w) }).toEqual({ w, found: false })
    // the plain title and effect are on the card
    expect(screen.getByTestId('hub-improve-certification_fail').textContent)
      .toContain('Not certified: expected shortfall 12.38 h/yr exceeds the 3 h/yr target')
    expect(screen.getByTestId('hub-improve-not_established_frontier').textContent)
      .toContain('Cost versus reliability could not be worked out')
    expect(screen.getByTestId('hub-improve-fmea_dominant_mode').textContent)
      .toContain('site_transformer accounts for 93% of the outage risk')
    expect(screen.getByTestId('hub-improve-fmea_dominant_mode').textContent)
      .toContain('The assistant would compare the cost with and without a spare unit or extra storage (rough estimate).')
    // the engine prose moved into Why, verbatim
    const ev = screen.getByTestId('hub-improve-evidence-certification_fail').textContent ?? ''
    expect(ev).toContain(ENGINE[0].recommendation)
    expect(ev).toContain(ENGINE[0].actions[0].effect)
  })

  it('Why rounds long decimals', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ findings: ENGINE }))
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-why-certification_fail'))
    const ev = screen.getByTestId('hub-improve-evidence-certification_fail').textContent ?? ''
    expect(ev).toContain('12.38')
    expect(ev).toContain('[7.683, 17.08]')
    expect(ev).not.toContain('12.383928571428568')
    expect(ev).toContain('500')
  })

  it('no stand-alone hover: the stress-scenario term labels its button row', async () => {
    mount()
    await screen.findByTestId('hub-improve-add-stress')
    const term = screen.getByTestId('term-stress_scenario')
    expect(term.closest('[data-testid="hub-improve-stress-row"]')).not.toBeNull()
    expect(screen.getByTestId('hub-improve-stress-row')
      .querySelector('[data-testid="hub-improve-add-stress"]')).not.toBeNull()
  })

  it('no high or medium finding: says so', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ findings: [F[2], F[3]] }))
    mount()
    expect((await screen.findByTestId('hub-improve-none')).textContent).toMatch(/nothing urgent/i)
  })
})
