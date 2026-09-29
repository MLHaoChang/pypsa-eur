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
import { actionLabel, actionText, actionTexts, askText, stressScenarioText } from '../delegate'
import { DC_TEMPLATE, REPORT, review } from '../testFixtures'
import { ImproveCard } from './ImproveCard'

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhTemplate: vi.fn(),
      getFmeaModes: vi.fn(),
    },
  }
})
const sweep = { mutate: vi.fn(), isPending: false, isSuccess: false }
vi.mock('../../../hooks/useStartFmeaSweep', () => ({ useStartFmeaSweep: vi.fn(() => sweep) }))

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign',
    resultsTabRequest: null, assistantDockOpen: false })
  useChatStore.setState({ composerSeed: null, requestQueue: [], lastRequest: null })
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

  // P25 (§5.7, §6.1): Do SENDS the request — it is queued for ChatPanel,
  // not seeded in the composer.
  const queued = () => useChatStore.getState().requestQueue.map(r => r.text)

  it('Do → sends the §5.7 text with JSON.stringify(args); no Do without an action', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-do-certification_fail'))
    expect(queued()).toEqual([actionText(F[0])])
    expect(queued()[0]).toContain(JSON.stringify(F[0].actions[0].args))
    expect(useChatStore.getState().requestQueue[0].source).toBe('hub-design')
    expect(useChatStore.getState().requestQueue[0].label).toBe(actionLabel(F[0]))
    expect(useChatStore.getState().composerSeed).toBeNull()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(screen.queryByTestId('hub-improve-do-not_established_frontier')).toBeNull()
  })

  it('Do on a finding with two actions → two messages, in order', async () => {
    const two = { ...F[0], actions: [F[0].actions[0],
      { tool: 'update_solver_config', args: { partial: { voll: 5000 } }, effect: 'set the price' }] }
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ findings: [two] }))
    const user = mount()
    await user.click(await screen.findByTestId('hub-improve-do-certification_fail'))
    expect(queued()).toEqual(actionTexts(two))
    expect(queued()).toHaveLength(2)
    // one card click = one group (a denial drops the rest), each labelled
    const q = useChatStore.getState().requestQueue
    expect(q[0].group).toBeTruthy()
    expect(q[1].group).toBe(q[0].group)
    expect(q.map(r => r.label)).toEqual([actionLabel(two), `${actionLabel(two)} (step 2 of 2)`])
    // P25 gate B1: true in both modes — no claim that each step is confirmed
    const line = screen.getByTestId('hub-improve-certification_fail').textContent
    expect(line).toContain('then 1 more step, one at a time')
    expect(line).not.toContain('confirmed separately')
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
    expect(queued()).toEqual(
      [stressScenarioText('a weak-grid site like the Data Center Energy Hub example')])
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
      // P26 gate friction 4: the button runs the study; the effect is its aim.
      .toContain('The assistant would run the reliability study again. Aim: compare the cost with and without a spare unit or extra storage (rough estimate).')
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

// P26 (coordinator item 7): a sweep started here was polled by no mounted
// panel (FmeaTab re-reads /simulation/status only if it is open when the sweep
// ends), so the greeting / status bar kept "Not solved yet.". Once a sweep has
// started, the card follows it on FmeaTab's own query and re-reads the status
// when it leaves running.
describe('ImproveCard follows the sweep it started (P26)', () => {
  it('sweep running → done invalidates simulationStatus', async () => {
    sweep.isSuccess = true
    vi.mocked(resultsApi.getFmeaModes)
      .mockResolvedValueOnce({ sweep_status: 'running' } as never)
      .mockResolvedValue({ sweep_status: 'done' } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const spy = vi.spyOn(client, 'invalidateQueries')
    render(<QueryClientProvider client={client}><ImproveCard /></QueryClientProvider>)
    await vi.waitFor(() => expect(spy).toHaveBeenCalledWith(
      { queryKey: ['simulationStatus', 'Demo'] }), { timeout: 5000 })
    expect(vi.mocked(resultsApi.getFmeaModes).mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('no sweep started from here → the modes are not polled', async () => {
    mount()
    await screen.findByTestId('hub-improve-fmea')
    await new Promise(r => setTimeout(r, 50))
    expect(resultsApi.getFmeaModes).not.toHaveBeenCalled()
  })
})

// P26 gate friction 4: the description said "the assistant would size the
// local capacity…" while the button opens a "Run the reliability study" card.
// For a study re-run the text now says so; the effect becomes its aim. Other
// tools keep "The assistant would <effect>".
describe('Improve description matches the action (P26 gate)', () => {
  it('run_eh_study → "run the reliability study again. Aim: …"', async () => {
    mount()
    const li = await screen.findByTestId('hub-improve-certification_fail')
    expect(li.textContent).toContain('The assistant would run the reliability study again. Aim: size the local capacity.')
  })

  it('another tool keeps "The assistant would <effect>"', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ findings: [
      { id: 'voll_missing', severity: 'high', title: 'frontier: not established', evidence: {},
        recommendation: 'Set VOLL.', actions: [{ tool: 'update_solver_config',
          args: { partial: { voll: 5000 } }, effect: 'set VOLL to 5000 €/MWh' }] },
    ] }))
    mount()
    const li = await screen.findByTestId('hub-improve-voll_missing')
    expect(li.textContent).toContain('The assistant would set the price of undelivered energy to 5000 €/MWh.')
  })
})
