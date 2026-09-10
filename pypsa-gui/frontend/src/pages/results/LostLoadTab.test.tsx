import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import LostLoadTab from './LostLoadTab'

// `getCopt` / `getMc` / `startMc` / `getElccCandidates` are no longer reached
// from this tab — the surfaces that fetched them moved to the Adequacy tab —
// but they stay stubbed so a regression that re-mounts one of them fails as a
// missing-testid assertion below rather than as a real axios call timing out
// somewhere else entirely. `getAdequacy` IS still read: the no-lost-load copy
// branches on whether a reliability target was set at all.
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

/** The no-lost-load branch with a reliability target SET and MET (ENS 0). */
function mockServedAllDemand() {
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
}

// ── ★ The adequacy surfaces MOVED (plan v2 §3, the recorded IA split [S13]).
//
// This tab used to host AdequacyChips, CoptChips, FrontierPanel and McPanel in
// BOTH branches, and the ★ mount invariant that pinned them there ("a reliable
// system is exactly where the surfaces must still render") now lives on
// AdequacyTab.test.tsx, where it is STRONGER because that tab has no early
// return at all. What stays pinned HERE is the other half of the split: the
// surfaces are gone from this tab, and the user is told where they went — a
// silent removal would read as a regression to anyone who knew the old layout.
//
// ★ Bite variant: re-add `<McPanel />` (or the chips) to either return in
// LostLoadTab.tsx — the mounts below go red while AdequacyTab stays green,
// which is the exact "moved by copy-paste, not by moving" regression.
const MOVED = ['adequacy-chips', 'copt-chips', 'frontier-panel', 'mc-panel'] as const

it('no longer mounts the moved adequacy surfaces in the no-lost-load branch', async () => {
  mockServedAllDemand()
  renderPage()
  // the cross-link is the anchor that proves the branch actually rendered
  await screen.findByTestId('lostload-adequacy-crosslink')
  for (const id of MOVED) expect(screen.queryByTestId(id)).toBeNull()
})

it('no longer mounts the moved adequacy surfaces in the data branch either', async () => {
  // default beforeEach payload has 424.24 MWh of lost load → data branch
  renderPage()
  // Wait for something only the DATA branch renders. The cross-link and the
  // empty copy are both on screen from the first paint, while the lost-load
  // query is still in flight and the EMPTY branch is what is mounted — so
  // waiting on either of those would assert against the empty branch twice
  // and let a re-mounted panel through on the branch that has data. Verified:
  // with `<McPanel />` restored to the data branch this test goes red only
  // once it waits for this header.
  await screen.findByText('Per-bus lost load')
  for (const id of MOVED) expect(screen.queryByTestId(id)).toBeNull()
})

it('cross-links the Adequacy tab from BOTH branches, naming what moved', async () => {
  renderPage()
  const dataBranch =
    (await screen.findByTestId('lostload-adequacy-crosslink')).textContent ?? ''
  expect(dataBranch).toMatch(/Adequacy tab/)
  expect(dataBranch).toMatch(/coupling loop/i)
  cleanup()

  mockServedAllDemand()
  renderPage()
  expect((await screen.findByTestId('lostload-adequacy-crosslink')).textContent)
    .toMatch(/Adequacy tab/)
})

// ★ Bite: leave the "reliability target ABOVE reports what actually bound"
// sentence in place. The target readout is no longer above — it is not on this
// tab at all — so the copy points the user at empty space, which is worse than
// saying nothing: it reads as a rendering bug in the tab they are looking at.
it('does not point at a target readout that is no longer on this tab', async () => {
  mockServedAllDemand()
  renderPage()
  // The empty-copy block is present in BOTH branches, so its testid resolves
  // on the first paint — before GET /results/adequacy has answered and while
  // the untargeted copy is still showing. The targeted sentence is what says
  // the query landed, so that is what this waits on.
  await screen.findByText(/No unserved energy/i)
  const copy = screen.getByTestId('lostload-empty-copy').textContent ?? ''
  expect(copy).not.toMatch(/above/i)
  // and it must still NOT tell the user to set a VoLL that is already set
  expect(screen.queryByText(/Set a VOLL/i)).toBeNull()
})

// The genuinely-untargeted branch is unchanged: no target was set, so the
// actionable advice is still about VoLL, not about a tab.
it('still explains VoLL when no target was ever set', async () => {
  vi.mocked(resultsApi.getLostLoad).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[0]],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 0, bus_carriers: {},
  } as never)
  renderPage()
  const copy = (await screen.findByTestId('lostload-empty-copy')).textContent ?? ''
  expect(copy).toMatch(/VOLL/i)
})
