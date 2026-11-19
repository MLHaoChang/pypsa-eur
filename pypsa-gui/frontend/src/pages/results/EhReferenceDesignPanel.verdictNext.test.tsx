// Obstacle 5, part (guided-mode spec §2.9): a failed or inconclusive MC
// certification told the user nothing about what to do next, and the "ok"
// completeness chips were brand red (the accent) — read as errors.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import {
  certificationHeadline, EhReferenceDesignPanel, statusTone,
} from './EhReferenceDesignPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: none(), getEhReferenceDesign: vi.fn(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: none(), getEhDtcPlanning: none(),
      getEhReadiness: none(), getEhTemplate: none(),
    },
  }
})

const FAIL_NEXT = 'Next: tighten the energy target or add firm capacity — press Ask the assistant for a reviewed recommendation.'
// W3b-R: the draws and the target are the reliability goal's, linked by the line.
const INCONCLUSIVE_NEXT = 'Next: raise the MC draws in the reliability goal or tighten the plan; an inconclusive verdict is not a failure.'
const NO_TARGET_NEXT = 'Next: set an allowed shortfall in the reliability goal to certify.'
const GOAL_LINK = 'Change in Scenarios & optimisation ›'

function report(payload: Record<string, unknown>) {
  return {
    archetype: 'strong_grid', pack_hash: 'h', assumptions_hash: 'a',
    completeness: { target: 'ok', certification: 'ok' },
    sections: { certification: { status: 'ok', note: null, payload: {
      metric: 'mc_lole', lole_h_per_year: 12, horizon_years: 1, lole_ci: [10, 14],
      ...payload } } },
  } as never
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('certificationHeadline.next', () => {
  it.each([
    ['fail', 3, FAIL_NEXT],
    ['inconclusive', 3, INCONCLUSIVE_NEXT],
    ['pass', 3, null],
  ])('verdict %s → next', (verdict, target, next) => {
    expect(certificationHeadline(report({ verdict, target_lole_h: target }))!.next).toBe(next)
  })

  it('inconclusive held by a feeder bound → the feeder, not more draws (GK A F6\')', () => {
    const next = certificationHeadline(report({ verdict: 'inconclusive', target_lole_h: 3,
      feeder_bound: { pockets: [{ buses: ['it_bus'], feeders: ['site_transformer'],
        bound_h_per_year: 43.8 }], chained: [] } }))!.next
    expect(next).toMatch(/second feeder/)
    expect(next).not.toMatch(/raise the MC draws/)
  })

  it('no target (verdict null) → set a target', () => {
    expect(certificationHeadline(report({ verdict: null, target_lole_h: null }))!.next)
      .toBe(NO_TARGET_NEXT)
  })

  it("metric 'none' → set a target", () => {
    expect(certificationHeadline(report({ metric: 'none', verdict: null }))!.next)
      .toBe(NO_TARGET_NEXT)
  })
})

describe('rendered next step', () => {
  async function renderWith(payload: Record<string, unknown>) {
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(report(payload))
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    await user.click(screen.getByTestId('eh-reference-design-toggle'))
    await screen.findByTestId('eh-report')
  }

  it('renders under a failed verdict', async () => {
    await renderWith({ verdict: 'fail', target_lole_h: 3 })
    expect(screen.getByTestId('eh-certification-verdict').dataset.verdict).toBe('fail')
    expect(screen.getByTestId('eh-certification-next').textContent).toBe(FAIL_NEXT)
  })

  it('renders for inconclusive and for no target, not for pass', async () => {
    await renderWith({ verdict: 'inconclusive', target_lole_h: 3 })
    expect(screen.getByTestId('eh-certification-next').textContent).toBe(INCONCLUSIVE_NEXT + GOAL_LINK)
    expect(screen.getByTestId('eh-certification-next-goal').textContent).toBe(GOAL_LINK)
    cleanup()
    await renderWith({ verdict: null, target_lole_h: null })
    expect(screen.getByTestId('eh-certification-next').textContent).toBe(NO_TARGET_NEXT + GOAL_LINK)
    cleanup()
    await renderWith({ verdict: 'pass', target_lole_h: 30 })
    expect(screen.queryByTestId('eh-certification-next')).toBeNull()
  })
})

describe('statusTone', () => {
  it("'ok' uses the success tone, not the accent, and differs from not_established", () => {
    expect(statusTone('ok')).not.toContain('accent')
    expect(statusTone('ok')).toBe('text-success')
    expect(statusTone('ok')).not.toBe(statusTone('not_established'))
    expect(statusTone('not_established')).toBe('text-warn')
    expect(statusTone('skipped')).toBe('text-muted')
  })
})

describe('Expert says the verdict in the words Guided uses (asset-walk 2026-11-17)', () => {
  async function renderWith(payload: Record<string, unknown>) {
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(report(payload))
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    await user.click(screen.getByTestId('eh-reference-design-toggle'))
    await screen.findByTestId('eh-report')
  }

  it.each([['pass', 'Certified'], ['fail', 'Not certified'], ['inconclusive', 'Not decided']])(
    '%s → "%s", as Guided headline says it', async (verdict, words) => {
      await renderWith({ verdict, target_lole_h: 3 })
      expect(screen.getByTestId('eh-certification-verdict').textContent!.startsWith(`${words} (`)).toBe(true)
    })

  it('names the must-stay-on feeder note in plain names', async () => {
    await renderWith({ verdict: 'fail', target_lole_h: 3, mc_lole_h: 87.6,
      critical_feeder_note: 'Must-stay-on feeders: added in closed form for hospital_feeder: …' })
    expect(screen.getByTestId('eh-certification-feeders').textContent)
      .toBe('Must-stay-on feeders: added in closed form for Hospital feeder: …')
  })
})
