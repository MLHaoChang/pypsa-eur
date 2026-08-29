import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import LostLoadTab from './LostLoadTab'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getLostLoad: vi.fn(), getAdequacy: vi.fn(), getCopt: vi.fn(), getMc: vi.fn(), startMc: vi.fn(), getElccCandidates: vi.fn() } }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getSnapshots: vi.fn(),
      getInvestmentPeriods: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Full getLostLoad shape (api/simulation.ts:357-367): `total_mwh` /
  // `total_cost_eur` / `voll_eur_per_mwh` are required alongside the
  // index/columns/data TS payload, not optional. LostLoadTab.tsx's `llMeta`
  // cast (LostLoadTab.tsx:54-59) only ever reads `voll_eur_per_mwh` and
  // `bus_carriers` off this object — `total_mwh`/`total_cost_eur` feed
  // nothing in the component, so 0 is a fully inert default for both.
  // `voll_eur_per_mwh: 0` keeps the "Lost-load cost" / "Cost (€)" / "VOLL
  // price" figures at their zero baseline (verified below), so they can't
  // coincidentally collide with the "424.2" text this test asserts on.
  vi.mocked(resultsApi.getLostLoad).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[424.24]],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 0,
  })
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [], weightings: [] })
  // Existing tests predate the adequacy surfaces: default both to the 204
  // (no report) case so they behave exactly as before.
  vi.mocked(resultsApi.getAdequacy).mockReset().mockResolvedValue(null as never)
  vi.mocked(resultsApi.getCopt).mockReset().mockResolvedValue(null as never)
  // Same 204 default for the sequential-MC surface: no study run this session.
  vi.mocked(resultsApi.getMc).mockReset().mockResolvedValue(null as never)
  vi.mocked(resultsApi.startMc).mockReset().mockResolvedValue({ status: 'running' } as never)
  // The MC panel's ELCC picker fetches its candidates only while the panel is
  // OPEN, and it ships collapsed — so this mock is not reached in this suite
  // today. It is stubbed anyway because the alternative failure is a real
  // axios call from a jsdom test the day the panel's default changes, which
  // fails as a timeout somewhere else entirely.
  vi.mocked(resultsApi.getElccCandidates).mockReset()
    .mockResolvedValue({ assets: [], max_assets: 10 })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LostLoadTab />
    </QueryClientProvider>,
  )
}

it('renders a distinctive lost-load KPI sourced from a single mocked snapshot', async () => {
  renderPage()
  // LostLoadTab.tsx's local fmtEnergy (lines 286-292) mirrors Curtailment's:
  // "424.2 MWh". With a single bus ('Bus 0') and a single snapshot, the same
  // 424.24 MWh total renders THREE times: the "Total lost load" KPI
  // (LostLoadTab.tsx:153), the "By carrier" table's lone "Electrical" row
  // (:179, since an empty `bus_carriers` map falls back to the "electrical"
  // alias), and the "Per-bus lost load" table's lone "Bus 0" row (:265) — all
  // three sum the identical one-column, one-row payload via
  // `weightedSumSplit`. A single-match `findByText` throws
  // "Found multiple elements" here (verified); use `findAllByText` and
  // assert on the known count (3) instead, same resolution
  // Curtailment.test.tsx used for its own two-way duplicate. Recharts' axis
  // ticks are live DOM in this suite too, but the rendered X-axis date tick
  // and Y-axis "Lost load (MW)" label contain no "424.2" substring, so they
  // don't inflate the count (confirmed by dumping the full rendered body
  // text during development — see task-24-report.md).
  const matches = await screen.findAllByText((text) => text.includes('424.2'))
  expect(matches.length).toBe(3)
})

// ── The success case: a reliability target SET and MET, so ENS is 0.
//
// This used to render as a bare "No lost-load data available" page telling
// the user to set a VoLL they had already set. The early return fired before
// AdequacyChips and CoptChips, so the achieved-vs-target readout, the badge
// naming which standard bound, and the whole COPT screening block — which
// needs no solve at all and is meaningful whether or not the LP shed
// anything — were all suppressed exactly when the plan had succeeded.
//
// Found by driving the real UI in a browser: the tab looked empty on a solve
// that had met its target.
it('still shows the target and COPT chips when the plan served all demand', async () => {
  vi.mocked(resultsApi.getLostLoad).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[0]],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 4000, bus_carriers: {},
  } as never)
  vi.mocked(resultsApi.getAdequacy).mockReset().mockResolvedValue({
    engine: 'lp_proxy', fidelity: 'deterministic_scenario',
    target: { basis: 'energy', binding: 'voll', zone_field_populated: true,
      system: { cap_mwh: 23.76, achieved_ens_mwh: 0, achieved_shed_hours: 0 }, zones: [] },
    metrics: { ens_mwh: 0, shed_hours: 0, lole_hours: null, eue_mwh: null,
      confidence_interval: null, n_samples: null, time_basis: 'hours_per_year' },
    cost: { total_system_cost_eur: 1, excludes_shed_cost: true, period_basis: 'single_period' },
    energy: { involuntary_mwh: 0, demand_response_mwh: 0 },
  } as never)
  vi.mocked(resultsApi.getCopt).mockReset().mockResolvedValue({
    engine: 'copt', fidelity: 'analytic_convolution',
    metrics: { lole_hours: 24, eue_mwh: 1080, lolp_max: 1, time_basis: 'hours_per_year' },
    fleet: { units: 1, must_take: 0, delta_mw: 1 },
    voll_eur_per_mwh: 4000, per_mode: [],
  } as never)

  renderPage()
  // the standard that actually bound, and the COPT screening beside it
  // the chips block itself, and the badge naming the standard that bound
  expect(await screen.findByTestId('adequacy-chips')).toBeTruthy()
  expect(await screen.findByText(/standard:/i)).toBeTruthy()
  expect(await screen.findByText(/COPT screening/i)).toBeTruthy()
  // and it must NOT tell the user to set a VoLL that is already set
  expect(screen.queryByText(/Set a VOLL/i)).toBeNull()
})

// ── ★ The MC panel is mounted in BOTH branches of this tab.
//
// The zero-lost-load early return is precisely where a reliable system lands,
// and where the MC's CI-bearing zero and the ELCC refusals are the whole
// story — the same Phase-QA chips lesson the adequacy/COPT chips learned the
// hard way, applied in advance. Mounting only in the data branch would hide
// the study exactly when it matters most.
//
// ★ Bite variant: delete `<McPanel />` from the early (no-lost-load) return in
// LostLoadTab.tsx — the first of these two tests must go red.
it('mounts the MC panel in the no-lost-load branch', async () => {
  vi.mocked(resultsApi.getLostLoad).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[0]],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 4000, bus_carriers: {},
  } as never)
  renderPage()
  expect(await screen.findByTestId('mc-panel')).toBeTruthy()
})

it('mounts the MC panel in the data branch too', async () => {
  // default beforeEach payload has 424.24 MWh of lost load → data branch
  renderPage()
  expect(await screen.findByTestId('mc-panel')).toBeTruthy()
})
