// The Results → Adequacy tab (plan v2 §3, the recorded IA split [S13]).
//
// ★ THE MOUNT INVARIANT MOVED HERE. It used to live on LostLoadTab
// ("mounts the MC panel in the no-lost-load branch" / "…in the data branch
// too") and encoded the lesson that a reliable system is EXACTLY where the
// adequacy surfaces must still render: the tab whose early return fired on
// zero lost load was hiding the studies precisely when the plan had
// succeeded. The surfaces have moved, so the invariant moves with them — and
// it is stronger here, because this tab has NO early return at all.
//
// ★ Bite for the whole block: add any early return to AdequacyTab.tsx gated on
// the adequacy report (or on a solve existing) — the no-data tests below go
// red while the data tests stay green, which is the exact regression.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import AdequacyTab from './AdequacyTab'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getAdequacy: vi.fn(), getCopt: vi.fn(),
      getFrontier: vi.fn(), startFrontier: vi.fn(),
      getMc: vi.fn(), startMc: vi.fn(), getElccCandidates: vi.fn(),
      getCouplingLoop: vi.fn(), startCouplingLoop: vi.fn(),
      abortCouplingLoop: vi.fn(), getReserveMargin: vi.fn(),
      getMarginLoop: vi.fn(), startMarginLoop: vi.fn(),
      abortMarginLoop: vi.fn(),
    },
  }
})

const ADEQUACY = {
  engine: 'lp_proxy', fidelity: 'deterministic_scenario',
  target: {
    basis: 'energy', binding: 'voll', zone_field_populated: true,
    system: { cap_mwh: 23.76, achieved_ens_mwh: 0, achieved_shed_hours: 0 },
    zones: [],
  },
  metrics: { ens_mwh: 0, shed_hours: 0 },
  energy: { involuntary_mwh: 0, demand_response_mwh: 0 },
}

const COPT = {
  engine: 'copt', fidelity: 'analytic_convolution',
  metrics: {
    lole_hours: 24, eue_mwh: 1080, lolp_max: 1,
    time_basis: 'hours_per_year', horizon_years: 1,
  },
  fleet: { units: 3, must_take: 0, delta_mw: 1 },
  voll_eur_per_mwh: 4000, per_mode: [],
}

// Shape-accurate to `sanitize_reserve_margin_payload`'s output.
const RESERVE_MARGIN = {
  margin: 0.15, horizon_wide: true,
  by_period: [{
    period: 'ALL', peak_mw: 150, required_mw: 172.5, firm_mw: 180,
    margin_achieved: 0.2, met: true, binding: false, n_peak_hours: 1,
    peak_snapshots: ['2030-01-01 00:00:00'],
    max_achievable_mw: 220, max_achievable_unbounded: false,
  }],
  assets: [{
    name: 'coal_a', period: 'ALL', kind: 'generator', capacity_mw: 100,
    derate: 0.9, basis: 'EFORd', source: 'asset', extendable: false,
    firm_mw: 90, energy_limited: false,
  }],
  derating_bases: { EFORd: 1 },
}

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // The NO-DATA state is the default: every adequacy surface serves 204 before
  // anything has been run, and this tab must render in full anyway.
  vi.mocked(resultsApi.getAdequacy).mockReset().mockResolvedValue(null as never)
  vi.mocked(resultsApi.getCopt).mockReset().mockResolvedValue(null as never)
  vi.mocked(resultsApi.getFrontier).mockReset().mockResolvedValue(null as never)
  vi.mocked(resultsApi.startFrontier).mockReset().mockResolvedValue({} as never)
  vi.mocked(resultsApi.getMc).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startMc).mockReset().mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.getElccCandidates).mockReset()
    .mockResolvedValue({ assets: [], max_assets: 10 })
  vi.mocked(resultsApi.getCouplingLoop).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startCouplingLoop).mockReset()
    .mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.abortCouplingLoop).mockReset()
    .mockResolvedValue({ status: 'done', aborting: false })
  // The MARGIN loop is a SECOND study surface with its own record and its own
  // 204: nothing has been run, and the tab must mount it anyway.
  vi.mocked(resultsApi.getMarginLoop).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startMarginLoop).mockReset()
    .mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.abortMarginLoop).mockReset()
    .mockResolvedValue({ status: 'done', aborting: false })
  // The firm-capacity readout serves 204 before any margin-set solve, and the
  // tab must still mount it — that is the invariant this file exists for.
  vi.mocked(resultsApi.getReserveMargin).mockReset().mockResolvedValue(null)
})

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><AdequacyTab /></QueryClientProvider>)
}

/** Every study surface this tab is responsible for hosting, in tab order.
 *  `margin-loop-panel` joined in Phase 9: the SECOND lever on the same
 *  coupled search — it drives the planning reserve margin instead of the
 *  energy cap, has its own record and its own 204, and mounts on exactly the
 *  same terms as the rest, including before anything has been run.
 *  `reserve-margin-panel` joined the list in Phase 8: it is a STANDARD
 *  readout, so it is hosted above the studies that are read against it, and
 *  it mounts on the same terms as the rest — including in the 204 state, its
 *  ordinary condition before anything has been solved with a margin. */
const PANELS = [
  'reserve-margin-panel', 'frontier-panel', 'mc-panel', 'loop-panel',
  'margin-loop-panel',
] as const

describe('AdequacyTab — the ★ mount invariant, no-data state', () => {
  it('mounts EVERY adequacy panel when nothing has been run', async () => {
    renderTab()
    for (const id of PANELS) {
      expect(await screen.findByTestId(id)).toBeTruthy()
    }
  })

  it('says a target has not been set rather than rendering an empty tab', async () => {
    renderTab()
    const line = await screen.findByTestId('adequacy-no-target')
    expect((line.textContent ?? '').length).toBeGreaterThan(30)
  })
})

describe('AdequacyTab — the ★ mount invariant, data state', () => {
  beforeEach(() => {
    vi.mocked(resultsApi.getAdequacy).mockResolvedValue(ADEQUACY as never)
    vi.mocked(resultsApi.getCopt).mockResolvedValue(COPT as never)
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(RESERVE_MARGIN as never)
  })

  it('mounts EVERY adequacy panel when the studies have data', async () => {
    renderTab()
    for (const id of PANELS) {
      expect(await screen.findByTestId(id)).toBeTruthy()
    }
  })

  it('hosts the achieved-vs-target chips and the COPT screening chips', async () => {
    renderTab()
    expect(await screen.findByTestId('adequacy-chips')).toBeTruthy()
    expect(await screen.findByTestId('copt-chips')).toBeTruthy()
    expect(screen.queryByTestId('adequacy-no-target')).toBeNull()
  })

  // The full hosting order the plan records, chips included: what BOUND comes
  // first, because every study below it is read against that answer.
  it('renders the chips above the panels, in the recorded order', async () => {
    renderTab()
    // The panels mount SYNCHRONOUSLY (that is the invariant above), so waiting
    // on one of them would not wait at all — the chips are the only part of
    // this tab gated on a resolved query, so they are what the order test has
    // to await before reading the tree.
    await screen.findByTestId('adequacy-chips')
    await screen.findByTestId('copt-chips')
    const html = document.body.innerHTML
    const seq = [
      'adequacy-chips', 'copt-chips', 'reserve-margin-panel', 'frontier-panel',
      'mc-panel', 'loop-panel', 'margin-loop-panel',
    ]
    const idx = seq.map(id => html.indexOf(`data-testid="${id}"`))
    expect(Math.min(...idx)).toBeGreaterThan(-1)
    for (let i = 1; i < idx.length; i++) {
      expect(idx[i]).toBeGreaterThan(idx[i - 1])
    }
  })
})

describe('AdequacyTab ordering', () => {
  // The reading order is the ANALYSIS order: what bound, then the screening
  // beside it, then the frontier, then the sampler, then the loop that drives
  // the sampler's verdict back into the plan.
  it('renders the panels in the order the plan records', async () => {
    renderTab()
    await screen.findByTestId('margin-loop-panel')
    const body = document.body
    const order = PANELS.map(
      id => body.innerHTML.indexOf(`data-testid="${id}"`))
    expect(order[0]).toBeGreaterThan(-1)
    for (let i = 1; i < order.length; i++) {
      expect(order[i]).toBeGreaterThan(order[i - 1])
    }
  })
})
