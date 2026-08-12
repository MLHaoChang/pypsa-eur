// Render-level characterisation coverage for ModelHorizon.tsx — the page had
// six defect fixes land on its JSX across two branches with zero render
// coverage (every prior test exercised the extracted pure functions in
// modelHorizonModel.ts instead). This file exists to pin the CURRENT,
// already-correct behaviour before the guided-step restructure rewrites the
// JSX, so a silent regression during that rewrite fails a test instead of
// shipping. Harness follows PropertiesPanel.rescale.test.tsx (module mock
// via importOriginal + per-test overrides, one QueryClient per render) and
// ChatPanelSurfaces.test.tsx (store reset in beforeEach/afterEach, assert on
// rendered text / mock call args, class-name assertion only where the thing
// under test IS a visual/style state with no textual equivalent).
//
// Three behaviours, each named for the defect it pins:
//   1. Multi-period weight edit sends a period-qualified PATCH key
//      ("2030|2024-01-01T00:00:00"), never a bare ISO — a bare key on a
//      MultiIndex network resolves last-write-wins to the LAST period, so
//      editing 2030's row silently overwrote 2050's weight.
//   2. The Resolution stat card reads snap.freq from the network, never a
//      locally-seeded form default — the original defect rendered a form
//      field seeded to 'h' that was never updated from the network.
//   3. The PV × preview column greys out when auto-discount would not
//      actually write anything at solve time (mirrors solver_service's
//      gate), rather than showing a value the LP will never use.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import type { SnapshotInfo, SolverConfig } from '../api/types'
import ModelHorizon from './ModelHorizon'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getSnapshots: vi.fn(),
      getInvestmentPeriods: vi.fn(),
      getLoads: vi.fn(),
      updateSnapshotWeightings: vi.fn(),
    },
  }
})

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return {
    ...actual,
    simulationApi: {
      ...actual.simulationApi,
      getSolverConfig: vi.fn(),
    },
  }
})

// ── Fixtures ─────────────────────────────────────────────────────────────

/** Full SolverConfig — the interface has ~35 required fields; this mirrors
 * the app's own defaults (store/simulationStore.ts's `defaultConfig`) so
 * tests only need to override the 2-3 fields the behaviour under test cares
 * about. */
function baseSolverConfig(over: Partial<SolverConfig> = {}): SolverConfig {
  return {
    solver_name: 'highs',
    mode: 'lopf',
    transmission_losses: false,
    multi_investment_periods: false,
    solver_options: {},
    extra_functionality_code: '',
    discount_rate: 0.07,
    inflation_rate: 0,
    default_lifetime: 25,
    co2_price: 0,
    co2_price_per_period: {},
    voll: 0,
    investment_periods: [],
    sclopf: false,
    sclopf_include_all_lines: false,
    sclopf_include_all_transformers: false,
    sclopf_voltage_threshold_kv: 0,
    sclopf_extra_lines: [],
    sclopf_extra_transformers: [],
    sclopf_scope: 'horizon',
    run_ac_pf_after_lopf: false,
    ac_pf_slack_bus: '',
    ac_pf_x_tol: 1e-6,
    presolve_enabled: true,
    user_objective_scale: 1,
    auto_objective_scale: false,
    solve_strategy: 'full',
    rolling_horizon: 168,
    rolling_overlap: 24,
    lf_aggregate_future: false,
    lf_k_periods: 8,
    lf_period_length_h: 168,
    lf_cluster_method: 'hierarchical',
    lf_include_extreme: true,
    mip_gap: 0.01,
    mip_time_limit_s: 0,
    ...over,
  }
}

/** A MultiIndex (period × timestep) network: two investment periods, one
 * shared operational timestep each — the exact shape `df_to_json` emits for
 * a MultiIndex `n.snapshot_weightings` (rows carry `period` + `timestep`,
 * never `snapshot`). */
function multiPeriodSnapshots(over: Partial<SnapshotInfo> = {}): SnapshotInfo {
  return {
    count: 2,
    snapshots: ['2024-01-01T00:00:00', '2024-01-01T00:00:00'],
    periods: [2030, 2050],
    weightings: [
      { period: 2030, timestep: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1 },
      { period: 2050, timestep: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1 },
    ],
    ts_start: null,
    ts_end: null,
    can_sample_weeks: false,
    freq: 'h',
    ...over,
  }
}

/** A flat (single-period) network — index is plain ISO under `snapshot`. */
function flatSnapshots(over: Partial<SnapshotInfo> = {}): SnapshotInfo {
  return {
    count: 3,
    snapshots: ['2024-01-01T00:00:00', '2024-01-01T01:00:00', '2024-01-01T02:00:00'],
    weightings: [
      { snapshot: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1 },
      { snapshot: '2024-01-01T01:00:00', objective: 1, generators: 1, stores: 1 },
      { snapshot: '2024-01-01T02:00:00', objective: 1, generators: 1, stores: 1 },
    ],
    ts_start: null,
    ts_end: null,
    can_sample_weeks: false,
    freq: 'h',
    ...over,
  }
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ModelHorizon />
    </QueryClientProvider>,
  )
}

/** Post-restructure helper: a configured project lands on the summary, so any
 * test that needs a specific step's content mounted must click its summary
 * line first. Scoped to the "Model horizon summary" region specifically
 * (not a bare `screen` query) for two reasons: (1) while `snap` is still
 * loading, the page shows step 1's rail as a placeholder — which also has a
 * button whose name can match the same regex — so an unscoped query can grab
 * the wrong (rail) button mid-flight; waiting for the summary region first
 * guarantees the entry-routing decision has already landed; (2) once inside
 * a step, the rail re-introduces the same possible name collision. */
async function openStep(name: RegExp) {
  const summary = await screen.findByRole('region', { name: 'Model horizon summary' })
  const btn = within(summary).getByRole('button', { name })
  await userEvent.click(btn)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.getSnapshots).mockReset()
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [], weightings: [] })
  vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.updateSnapshotWeightings).mockReset()
    .mockResolvedValue({ count: 0, weightings: [] })
  vi.mocked(simulationApi.getSolverConfig).mockReset().mockResolvedValue(baseSolverConfig())
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

// ── 1. Multi-period weight edit sends a period-qualified key (defect B1) ──

it('PATCHes a multi-period weight edit with a period-qualified key, not a bare ISO', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(multiPeriodSnapshots())
  vi.mocked(networkApi.getInvestmentPeriods).mockResolvedValue({
    periods: [2030, 2050],
    weightings: [
      { period: 2030, years: 1, objective: 1 },
      { period: 2050, years: 1, objective: 1 },
    ],
  })
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue(
    baseSolverConfig({ multi_investment_periods: true }),
  )

  renderPage()

  // This project is configured (count: 2), so the page lands on the summary
  // (guided-step restructure, Task 3) — open the "Snapshot weightings" step
  // to mount the section this test exercises.
  await openStep(/Snapshot weightings/)
  await screen.findByRole('heading', { name: 'Snapshot weightings' })

  // Task 4: the per-row table moved behind StepShell's Advanced disclosure
  // (collapsed by default — see the "collapses the weights Advanced
  // disclosure" test below), so it must be opened before the table exists.
  await userEvent.click(screen.getByText('Advanced'))

  // Scoped to the table itself — the StatCard strip above also renders the
  // literal text "2030" (in the Mode card's "2 investment periods · 2030,
  // 2050" sub-label), so an unscoped row/text query would be ambiguous.
  const table = await screen.findByRole('table')
  const rows = within(table).getAllByRole('row')
  const row2030 = rows.find(r => within(r).queryByText('2030'))
  if (!row2030) throw new Error('row for period 2030 not found in Snapshot weightings table')

  const objectiveInput = within(row2030).getByRole('spinbutton')
  await userEvent.clear(objectiveInput)
  await userEvent.type(objectiveInput, '5')
  await userEvent.tab() // blur — the edit only commits onBlur

  expect(networkApi.updateSnapshotWeightings).toHaveBeenCalledWith({
    updates: { '2030|2024-01-01T00:00:00': { objective: 5 } },
  })
})

// ── 2. Resolution comes from the network, never a local form default (B3) ─

// "Resolution" is ambiguous on this page: the StatCard eyebrow (a <div>) AND
// the single-period constructor's field label (a <span>, "seed a NEW index")
// both carry the literal text. Only the StatCard is the status card under
// test — it's the one <div> among the matches.
function findResolutionCard(): HTMLElement {
  const matches = screen.getAllByText('Resolution').filter(el => el.tagName === 'DIV')
  if (matches.length !== 1) throw new Error(`expected exactly one Resolution stat-card eyebrow, found ${matches.length}`)
  const card = matches[0].parentElement
  if (!card) throw new Error('Resolution stat card not found')
  return card
}

it('reads the Resolution stat card from network freq, never a local form default', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots({ freq: '3h' }))

  renderPage()

  // The single-period snapshot-constructor <select> further down the page
  // renders a "3-hourly" <option> from the SAME static FREQ_OPTIONS list —
  // an unscoped `screen.findByText('3-hourly')` would resolve against that
  // static option instantly, before the mocked query even settles, and
  // silently prove nothing. Locate the StatCard first (its "Resolution"
  // eyebrow is static and present immediately) and wait for the value
  // WITHIN it, which only reads "3-hourly" once `snap.freq` has loaded.
  const card1 = findResolutionCard()
  await within(card1).findByText('3-hourly')

  cleanup()

  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots({ freq: null }))
  renderPage()

  const card2 = findResolutionCard()
  await within(card2).findByText('Irregular')
  // The page's OWN "seed a new index" form defaults `freq` state to 'h' and
  // renders a "Hourly (h)" <option> in the constructor <select> further down
  // the page — that is expected and lives outside this card. What must never
  // happen is that local default leaking INTO the stat card in place of the
  // network's real (here: unset) resolution.
  expect(within(card2).queryByText(/Hourly/)).toBeNull()
})

// ── 3. PV × preview column greys when auto-discount is inert ──────────────

it('greys the PV x preview column when auto-discount would not actually write anything', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(multiPeriodSnapshots())
  vi.mocked(networkApi.getInvestmentPeriods).mockResolvedValue({
    periods: [2030, 2050],
    weightings: [
      { period: 2030, years: 1, objective: 1 },
      { period: 2050, years: 1, objective: 1 },
    ],
  })

  function pvCellForPeriod2030(): HTMLElement {
    const header = screen.getByText('Period weightings')
    const card = header.parentElement?.parentElement
    if (!card) throw new Error('Period weightings card not found')
    const rows = within(card).getAllByRole('row')
    const row2030 = rows.find(r => within(r).queryByText('2030'))
    if (!row2030) throw new Error('row for period 2030 not found in Period weightings table')
    // The PV preview cell is identified by its tooltip text rather than
    // column position — both the active and inert copy are unique on the
    // page and don't depend on column order (which the guided-step
    // restructure this test protects against is free to change).
    const cell = within(row2030).getByTitle(/Auto-discount/)
    return cell
  }

  // Properly configured multi-period network with auto-discount ON: active.
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue(
    baseSolverConfig({ multi_investment_periods: true, auto_discount_periods: true }),
  )
  renderPage()
  // Configured project (count: 2) lands on the summary — open "Economics",
  // which is where the Period weightings / PV preview table now lives.
  await openStep(/Economics/)
  await screen.findByText('Period weightings') // wait for full render
  const activeCell = pvCellForPeriod2030()
  expect(activeCell.title).toMatch(/Auto-discount will set objective/)
  // jsdom has no paint/layout engine, so the "grey" visual state is only
  // observable through the class that drives it — same rationale as the
  // min-w-0/truncate check in ChatPanelSurfaces.test.tsx.
  expect(activeCell.className).toContain('text-text')
  expect(activeCell.className).not.toContain('text-muted/40')

  cleanup()

  // Same network, auto-discount OFF: inert — must grey out.
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue(
    baseSolverConfig({ multi_investment_periods: true, auto_discount_periods: false }),
  )
  renderPage()
  await openStep(/Economics/)
  await screen.findByText('Period weightings')
  const inertCell = pvCellForPeriod2030()
  expect(inertCell.title).toMatch(/Auto-discount is off/)
  expect(inertCell.className).toContain('text-muted/40')
})

// ── 4. Guided-step shell: routing between summary and steps (Task 3) ──────

it('opens on step 1, not the summary, when the horizon is unset (count <= 1)', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots({ count: 1 }))

  renderPage()

  const nav = await screen.findByRole('navigation', { name: 'Model horizon steps' })
  expect(within(nav).getByRole('button', { name: /Mode/ }).getAttribute('aria-current')).toBe('step')
  expect(screen.queryByRole('region', { name: 'Model horizon summary' })).toBeNull()
})

it('opens on the summary, not step 1, when the horizon is already configured', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots()) // count: 3

  renderPage()

  const summary = await screen.findByRole('region', { name: 'Model horizon summary' })
  expect(summary).not.toBeNull()
  expect(screen.queryByRole('navigation', { name: 'Model horizon steps' })).toBeNull()
})

it('opens the corresponding step when a summary line is clicked', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots()) // count: 3, configured

  renderPage()
  await openStep(/Snapshot weightings/)

  const nav = await screen.findByRole('navigation', { name: 'Model horizon steps' })
  expect(within(nav).getByRole('button', { name: /Snapshot weightings/ }).getAttribute('aria-current')).toBe('step')
  expect(await screen.findByRole('heading', { name: 'Snapshot weightings' })).not.toBeNull()
})

it('shows four rail entries in single-period mode, six in multi-period', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots({ count: 1 }))
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue(
    baseSolverConfig({ multi_investment_periods: false }),
  )
  renderPage()
  const singleNav = await screen.findByRole('navigation', { name: 'Model horizon steps' })
  expect(within(singleNav).getAllByRole('button')).toHaveLength(4)

  cleanup()

  vi.mocked(networkApi.getSnapshots).mockResolvedValue(multiPeriodSnapshots({ count: 1 }))
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue(
    baseSolverConfig({ multi_investment_periods: true }),
  )
  renderPage()
  // The rail renders immediately off the placeholder single-period default
  // (isMultiPeriod defaults false until solverConfig resolves), so wait for
  // a multi-only rail entry before counting — otherwise this can observe the
  // pre-solverConfig 4-button paint instead of the settled 6-button one.
  await screen.findByRole('button', { name: /Investment years/ })
  const multiNav = screen.getByRole('navigation', { name: 'Model horizon steps' })
  expect(within(multiNav).getAllByRole('button')).toHaveLength(6)
})

// ── 5. Economics / Window must not dead-end at zero investment years ──────
// Before the guided-step rail existed, this state was unreachable: the old
// scroll gated the whole "Multi-period planning" section on isMultiPeriod
// alone, and the always-visible "Investment years" add-UI sat directly
// above the Period-weightings / MultiIndex-constructor blocks — a user
// physically could not land on an empty screen. The rail now lists
// Economics and Snapshot window as clickable regardless of period count,
// so both steps must give the user a way out rather than rendering blank.

// ── 6. Weights step: Advanced disclosure gates the per-row table (Task 4) ─
// The per-row table is unusable at 8,760-row scale, so Task 4 moves it (plus
// the CSV controls) behind StepShell's `advanced` disclosure. It must be
// collapsed on arrival — the table must not even be mounted, not just
// visually hidden — and only mount once the user opens "Advanced".

it('collapses the weights Advanced disclosure by default, mounting the per-row table only once opened', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(flatSnapshots()) // count: 3, configured

  renderPage()
  await openStep(/Snapshot weightings/)

  await screen.findByRole('heading', { name: 'Snapshot weightings' })
  // Collapsed by default: the per-row table must not be in the document yet.
  expect(screen.queryByRole('table')).toBeNull()
  expect(screen.getByText('Advanced')).not.toBeNull()

  await userEvent.click(screen.getByText('Advanced'))

  expect(await screen.findByRole('table')).not.toBeNull()
})

it('gives Economics and Window an actionable way out at zero investment years, not a blank frame', async () => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue(multiPeriodSnapshots()) // count: 2, configured
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue(
    baseSolverConfig({ multi_investment_periods: true }),
  )
  // getInvestmentPeriods defaults (beforeEach) to { periods: [], weightings: [] } — zero years.

  renderPage()
  await openStep(/Economics/)

  // Scope to the step's own body section (excludes the rail, which also has
  // an "Investment years" entry) — same `heading.closest('section')` pattern
  // the multi-period-weight-edit test above uses.
  const economicsHeading = await screen.findByRole('heading', { name: /Economics/ })
  const economicsBody = economicsHeading.closest('section')
  if (!economicsBody) throw new Error('Economics step body section not found')

  // Must NOT silently render nothing: no Period weightings table...
  expect(within(economicsBody).queryByText('Period weightings')).toBeNull()
  // ...but a real, actionable way to reach the step that creates a year.
  const goToYearsFromEconomics = within(economicsBody).getByRole('button', { name: /Go to Investment years/ })
  await userEvent.click(goToYearsFromEconomics)

  const nav = screen.getByRole('navigation', { name: 'Model horizon steps' })
  expect(within(nav).getByRole('button', { name: /Investment years/ }).getAttribute('aria-current')).toBe('step')

  // Same fallback path must cover the multi-period Window step too.
  await userEvent.click(within(nav).getByRole('button', { name: /Snapshot window/ }))
  const windowHeading = await screen.findByRole('heading', { name: /Snapshot window/ })
  const windowBody = windowHeading.closest('section')
  if (!windowBody) throw new Error('Window step body section not found')
  expect(within(windowBody).queryByText('Snapshot constructor (MultiIndex)')).toBeNull()
  expect(within(windowBody).getByRole('button', { name: /Go to Investment years/ })).not.toBeNull()
})
