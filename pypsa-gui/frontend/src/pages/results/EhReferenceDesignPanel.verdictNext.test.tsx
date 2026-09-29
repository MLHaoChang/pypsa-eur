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
const INCONCLUSIVE_NEXT = 'Next: raise MC draws (Pack settings → Draws) or tighten the plan; an inconclusive verdict is not a failure.'
const NO_TARGET_NEXT = 'Next: set a shortfall target (Pack settings → LOLE target) to certify.'

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
    expect(screen.getByTestId('eh-certification-next').textContent).toBe(INCONCLUSIVE_NEXT)
    cleanup()
    await renderWith({ verdict: null, target_lole_h: null })
    expect(screen.getByTestId('eh-certification-next').textContent).toBe(NO_TARGET_NEXT)
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
