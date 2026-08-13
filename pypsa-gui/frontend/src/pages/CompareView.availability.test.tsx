// Task 5: CompareView had no unavailable branch at all, so Task 4's
// `available: boolean` flags (added to every Comparison block on
// ResultsSummary — see backend/models/schemas.py) changed nothing a user
// saw. A solved-but-unresolved figure still rendered as a confident €0.00,
// indistinguishable from a real zero — exactly what ADR-0001 forbids.
//
// EconomicsTab is exported (CompareView.tsx) solely so this test can render
// it in isolation, following the render/mock/QueryClientProvider recipe in
// PropertiesPanel.rescale.test.tsx:55-63.
//
// Mock shape verified against backend/models/schemas.py: EconomicsComparison
// has no `total_cost` field (that was the brief's placeholder) — it carries
// `available` + `by_carrier: dict[str, CarrierEconomics]` +
// `per_asset_lcoh`, nested under ResultsSummary.economics. `by_carrier` is
// left empty in the unavailable case to match the real backend contract
// (every dict on an unresolved block defaults to `{}` — see the "False
// means this block resolved nothing" docstrings), which exercises
// EconomicsTab's own "no economic data" fallback path and proves it now
// reports unavailability rather than reusing that unrelated empty-fleet
// message. `has_solve: true` is required — EconomicsTab bails out to
// UnsolvedBanner otherwise, which would make both cases render identical
// prose and the test pass no matter what the `available` branch did.
//
// The RESOLVED side's `gas` carrier gives every `CarrierPeriodValue` field
// its own distinct NON-ZERO number (111.1, 22.2, 33.3, ...), deliberately —
// an earlier version zeroed every field but revenue. Task 4 established
// that `available: true` with a zero figure is a LEGITIMATE state (a
// solved network can genuinely have zero OPEX, zero CAPEX, ...) and must
// still render `0.00`, not a marker. With a mostly-zero resolved fixture,
// the third test's "no `0.00` anywhere in the document" check couldn't
// tell a fabricated zero (the defect) from the resolved side's own real
// zeros (not the defect) — it was tripping on EconomicsTable's own
// OPEX/CAPEX/LCOE cells for `alpha`, nothing to do with `beta`. Once every
// resolved field is non-zero, that assertion is unambiguous again: any
// `0.00` on screen can only have come from the unresolved side. Do not
// simplify these back to zeros — that quietly defeats the check.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { COST_UNAVAILABLE } from './results/shared'

vi.mock('../api/projects', () => ({
  projectsApi: { resultsSummary: vi.fn(), compareState: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import {
  EconomicsTab, EmissionsTab, StorageCyclingTab,
  LoadingTab, PricesTab, OverviewTab, LostLoadTab,
} from './CompareView'

const summary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  economics: {
    available,
    by_carrier: available
      ? {
          gas: {
            revenue_meur: { total: 1234.5, by_period: {} },
            // opex_meur is the headline TOTAL (see CarrierEconomics'
            // docstring in api/types.ts) — sum of the four split fields
            // below (22.2+33.3+44.4+55.5=155.4), not an independent number.
            opex_meur: { total: 155.4, by_period: {} },
            gen_cost_meur: { total: 22.2, by_period: {} },
            storage_charge_cost_meur: { total: 33.3, by_period: {} },
            curtailment_cost_meur: { total: 44.4, by_period: {} },
            lost_load_cost_meur: { total: 55.5, by_period: {} },
            capex_meur: { total: 66.6, by_period: {} },
            dispatch_gwh: { total: 77.7, by_period: {} },
            // Re-derived by EconomicsTab from capex/opex/dispatch above
            // (see CompareView.tsx's `canon` LCOE recompute) — this input
            // value is a placeholder, same as CompareView.test.tsx's fixture.
            lcoe_eur_per_mwh: { total: 88.8, by_period: {} },
          },
        }
      : {},
    per_asset_lcoh: [],
  },
})

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <EconomicsTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

describe('Compare tabs distinguish unavailable from zero', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the unavailable marker, never a zero, when the block did not resolve', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(summary(false, name) as never),
    )
    renderTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText(/0\.00/)).toBeNull()
  })

  it('renders the figure when the block resolved', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(summary(true, name) as never),
    )
    renderTab()
    // Both sides resolve with the SAME fixture data (A/A identity), so the
    // real revenue figure legitimately renders twice — once per side's
    // column in EconomicsTable. That's correct behaviour, not a defect to
    // design around, so assert plural/non-empty rather than a single match.
    expect(await screen.findAllByText(/1,?234/)).not.toHaveLength(0)
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })

  it('renders the resolved side\'s real figure and marks only the unresolved side unavailable', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(summary(name === 'alpha', name) as never),
    )
    renderTab()
    // alpha resolved -> its revenue cell shows the real figure.
    expect(await screen.findByText(/1,?234/)).toBeTruthy()
    // beta did not resolve -> at least one cell reads the marker, not 0.00.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText(/0\.00/)).toBeNull()
  })

  // A payload's `available` flag can be genuinely ABSENT (not `false`) —
  // an older cached response, a partially-migrated payload. `api/types.ts`
  // declares `available: boolean` as required, so TypeScript believes the
  // `?? false` on every read site can never fire; nothing short of an
  // actual missing-field fixture proves it does. `summary()` above always
  // sets the field, so this needs its own fixture that omits it entirely.
  it('reads an ABSENT `available` field as unavailable, not as available (the `?? false` guard)', async () => {
    const withMissingFlag = (project: string) => ({
      project,
      has_solve: true,
      periods: [],
      economics: {
        // `available` intentionally omitted below — this is not `available:
        // false`. If a future edit changes `sa.economics?.available ?? false`
        // to `!!sa.economics?.available` or drops the `?? false` outright,
        // this is the only test that would catch it; every other fixture in
        // this file sets the field explicitly, so `??` never fires there.
        by_carrier: {
          gas: {
            revenue_meur: { total: 999.9, by_period: {} },
            opex_meur: { total: 11.1, by_period: {} },
            gen_cost_meur: { total: 2.2, by_period: {} },
            storage_charge_cost_meur: { total: 3.3, by_period: {} },
            curtailment_cost_meur: { total: 4.4, by_period: {} },
            lost_load_cost_meur: { total: 5.5, by_period: {} },
            capex_meur: { total: 6.6, by_period: {} },
            dispatch_gwh: { total: 7.7, by_period: {} },
            lcoe_eur_per_mwh: { total: 8.8, by_period: {} },
          },
        },
        per_asset_lcoh: [],
      },
    })
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(withMissingFlag(project) as never),
    )
    renderTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    // The real revenue figure must never leak through — a missing flag has
    // to read as fully unavailable, not partially trusted.
    expect(screen.queryByText(/999\.9/)).toBeNull()
  })

  // Task 4 established `available: true` + a zero field as a LEGITIMATE
  // state (a solved network can genuinely have zero OPEX for a carrier) —
  // it must still render `0.00`, not the unavailable marker. The other
  // tests in this file deliberately use all-non-zero fixtures (see header
  // comment) so THIS is the only place a resolved zero is exercised at all.
  it('renders a genuine resolved zero as 0.00, never as the unavailable marker (available: true)', async () => {
    const withResolvedZero = (project: string) => ({
      project,
      has_solve: true,
      periods: [],
      economics: {
        available: true,
        by_carrier: {
          gas: {
            revenue_meur: { total: 1234.5, by_period: {} },
            // A must-run renewable with no fuel/variable cost genuinely has
            // zero OPEX on a solved network — not an absence.
            opex_meur: { total: 0, by_period: {} },
            gen_cost_meur: { total: 0, by_period: {} },
            storage_charge_cost_meur: { total: 0, by_period: {} },
            curtailment_cost_meur: { total: 0, by_period: {} },
            lost_load_cost_meur: { total: 0, by_period: {} },
            capex_meur: { total: 66.6, by_period: {} },
            dispatch_gwh: { total: 77.7, by_period: {} },
            lcoe_eur_per_mwh: { total: 88.8, by_period: {} },
          },
        },
        per_asset_lcoh: [],
      },
    })
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(withResolvedZero(project) as never),
    )
    renderTab()
    // OPEX (total) row, both sides — a REAL zero (available: true on both).
    expect(await screen.findAllByText('0.00 M€')).not.toHaveLength(0)
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })
})

// EmissionsTab exercises ABKpiPair (total CO2, intensity) AND ABTable with
// totalsRow (by-carrier kt breakdown) — the two KPI numbers are the C1
// defect this section pins: an unavailable side's total_kt/intensity ship
// as a zero-valued object (never null — see compare.py's
// _compute_emissions_summary), so without an availability guard on
// ABKpiPair a zero-CO2 side rendered a fabricated "0.0 kt" beside the
// other side's real figure, indistinguishable from a genuine zero-carbon
// scenario.
function renderEmissionsTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <EmissionsTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

const emissionsSummary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  emissions: {
    available,
    total_kt: { total: available ? 500.5 : 0, by_period: {} },
    by_carrier_kt: available ? { gas: { total: 500.5, by_period: {} } } : {},
    intensity_kg_per_mwh: { total: available ? 123.4 : 0, by_period: {} },
  },
})

describe('EmissionsTab distinguishes unavailable from zero (C1)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders alpha\'s real kt beside beta\'s marker — never a fabricated 0.0 kt — and marks the ABTable totals cell unavailable', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(emissionsSummary(project === 'alpha', project) as never),
    )
    renderEmissionsTab()
    // alpha's real total CO2 figure renders — legitimately more than once
    // (the KPI, the by-carrier row, and the totals row all show 500.5 for
    // alpha), so assert plural/non-empty rather than a single match — same
    // idiom as this file's existing A/A-identity check.
    expect(await screen.findAllByText(/500\.5/)).not.toHaveLength(0)
    // beta's KPI + ABTable cells all read the marker.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    // No fabricated "0.0 kt" / "0.0 kg/MWh" anywhere — that's the literal
    // shape of the C1 defect (ABKpiPair had no availability guard).
    expect(screen.queryByText(/0\.0 kt/)).toBeNull()
    expect(screen.queryByText(/0\.0 kg\/MWh/)).toBeNull()
    // The ABTable totals row specifically must show the marker on beta's
    // side, not a summed 0.00 — the totals row is where the prior report
    // said "the one place a literal fabricated zero could previously reach
    // the screen" for ABTable itself; this pins the same discipline for
    // the KPI pair above it.
    const totalRow = (await screen.findByText('Total')).closest('tr')
    expect(totalRow?.textContent).toContain(COST_UNAVAILABLE)
  })
})

// StorageCyclingTab's StorageUnitTable is C2: a per-unit row whose OTHER
// side never resolved (by_unit: [] — see compare.py:2564) rendered
// `0.0 MW / 0.0 MWh / 0.0 cyc` plus a signed Δ, because `r.a?.p_nom_mw ?? 0`
// does not distinguish "unit absent because the side never resolved" from
// "unit present with a real zero". hasUnits at the tab level is an OR, so a
// mixed pair reaches this table.
function renderStorageCyclingTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <StorageCyclingTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

const storageCyclingSummary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  storage_cycling: {
    available,
    cycles_by_carrier: available ? { battery: { total: 3.9, by_period: {} } } : {},
    by_unit: available
      ? [{
          name: 'battery1',
          carrier: 'battery',
          p_nom_mw: 123.4,
          energy_mwh: 456.7,
          throughput_mwh: { total: 1780.0, by_period: {} },
          cycles: { total: 3.9, by_period: {} },
        }]
      : [],
  },
})

describe('StorageCyclingTab distinguishes unavailable from zero (C2)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders alpha\'s real per-unit row beside beta\'s marker — never 0.0 MW/MWh/cyc or a signed Δ', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(storageCyclingSummary(project === 'alpha', project) as never),
    )
    renderStorageCyclingTab()
    // alpha's real p_nom renders.
    expect(await screen.findByText('123.4 MW')).toBeTruthy()
    // beta has no unit named battery1 (its by_unit is [] — unavailable) —
    // its cells must read the marker, never a fabricated zero.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText('0.0 MW')).toBeNull()
    expect(screen.queryByText('0.0 MWh')).toBeNull()
    expect(screen.queryByText('0.0 cyc')).toBeNull()
    // The Δ cyc cell for battery1's row must be a dash, never a signed
    // number computed against beta's fabricated 0 cycles.
    const row = (await screen.findByText('battery1')).closest('tr')
    expect(row?.textContent).not.toMatch(/[+-]\d/)
  })
})

// ── Task 6 ────────────────────────────────────────────────────────────────
// Loading, Prices, and Overview render bespoke tables rather than the
// shared ABTable/StorageUnitTable primitives Task 5 wired, so they carried
// the same C1/C2-shaped defect independently. `LoadingComparison` and
// `PricesComparison` also turned out to be MISSING `available` from
// api/types.ts entirely (verified against backend/models/schemas.py:806-876
// — the backend has always sent it) even though every other Comparison
// block had it; that gap is fixed alongside the frontend wiring here.
//
// ── Task 7 ────────────────────────────────────────────────────────────────
// LostLoadTab was left unwired by Task 6 (see task-6-report.md) because
// `available` conflates one genuine "no shedding" zero with five different
// "the capture was never read" states — no frontend-only choice could turn
// it into a correct three-state signal. Task 7 adds `captured` to
// `LostLoadComparison` (backend/models/schemas.py) specifically so the
// frontend has something to key on: `captured=false` means nothing was
// read (render the marker); `captured=true` means the capture WAS read,
// whether it says zero (`available=false`, a REAL measured zero — render
// `0.0 MWh`) or nonzero (`available=true`, real shedding).
//
// The first three tests below pin exactly those three states, all against
// the SAME fixture shape (alpha always resolved with real, non-zero
// shedding so the tab's own "neither project shed load" banner never fires
// and both ABKpiPair sections plus both LostLoad tables render).
//
// Review round 1 (C1/C2) found the tab-level gate ITSELF still keyed on
// `available`: `if (!llA?.available && !llB?.available)` fired the
// "Neither project shed load ... the LP found a feasible dispatch without
// unserved demand" prose whenever BOTH sides were unavailable — which,
// after this task's own fix, includes every unread-capture state, not just
// genuine zeros. Worse, that gate returns BEFORE the KPI section, so the
// `captured`-keyed wiring above never even ran for those cases. Fixed by
// keying the gate on `captured` too (both uncaptured -> plain marker; both
// captured AND both genuine zero -> the prose; anything mixed -> fall
// through to the KPI section). The three tests after the first three below
// pin that gate directly, plus the ungated VOLL-price panel (C2).

function lostLoadSummary(project: string, ll: Record<string, unknown>) {
  return {
    project,
    has_solve: true,
    periods: [],
    lost_load: ll,
  }
}

// alpha: real, non-zero shedding on both sections (KPI + per-bus/per-carrier
// tables) — the fixed reference side every case below compares beta against.
const resolvedAlphaLostLoad = {
  available: true,
  captured: true,
  voll_eur_per_mwh: 3000,
  total_mwh: { total: 123.4, by_period: {} },
  total_cost_meur: { total: 0.37, by_period: {} },
  by_bus: [{ bus: 'BusA', energy_mwh: { total: 123.4, by_period: {} }, cost_meur: { total: 0.37, by_period: {} } }],
  by_carrier: [{
    carrier: 'electricity', bus_count: 1,
    energy_mwh: { total: 123.4, by_period: {} }, cost_meur: { total: 0.37, by_period: {} },
  }],
}

function renderLostLoadTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <LostLoadTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

describe('LostLoadTab distinguishes a measured zero from an unread capture (Task 7)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the unavailable marker, never 0.0 MWh, when the capture was never read (captured: false)', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(
        project,
        project === 'alpha'
          ? resolvedAlphaLostLoad
          : {
              // Unread capture: `available` defaults to false alongside it
              // (see the six default-constructed backend return sites),
              // but the distinguishing field for THIS case is `captured`.
              available: false, captured: false, voll_eur_per_mwh: 0,
              total_mwh: { total: 0, by_period: {} },
              total_cost_meur: { total: 0, by_period: {} },
              by_bus: [], by_carrier: [],
            },
      ) as never),
    )
    renderLostLoadTab()
    // alpha's real figure renders — legitimately twice (the KPI AND the
    // per-bus table's BusA row, both 123.4), so assert plural/non-empty
    // rather than a single match — same idiom as this file's other
    // A/A-identity checks.
    expect(await screen.findAllByText('123.4 MWh')).not.toHaveLength(0)
    // beta's KPI cells (total MWh + VOLL cost) AND its per-bus table cell
    // all read the marker.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    // No fabricated "0.0 MWh" anywhere — an unread capture is NOT a
    // measured zero, so it must never render as one.
    expect(screen.queryByText(/0\.0 MWh/)).toBeNull()
  })

  it('renders 0.0 MWh, never the unavailable marker, on a genuine measured zero (captured: true, available: false)', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(
        project,
        project === 'alpha'
          ? resolvedAlphaLostLoad
          : {
              // The solver ran, the capture WAS read, and it says zero —
              // a REAL measured result, not an absence. This is the case
              // that distinguishes Task 7 from a naive `available`-keyed
              // wiring (see task-7-brief.md's "middle rung").
              available: false, captured: true, voll_eur_per_mwh: 3000,
              total_mwh: { total: 0, by_period: {} },
              total_cost_meur: { total: 0, by_period: {} },
              by_bus: [], by_carrier: [],
            },
      ) as never),
    )
    renderLostLoadTab()
    expect(await screen.findAllByText('123.4 MWh')).not.toHaveLength(0)
    // beta's real zero — rendered as a genuine figure, not a marker.
    // Appears twice: the "Total lost load" KPI AND the per-bus table's
    // BusA row (beta's column, defaulting to 0 since BusA never reaches
    // the >1e-6 per-bus threshold — see LostLoadBus's docstring).
    expect(await screen.findAllByText('0.0 MWh')).not.toHaveLength(0)
    expect(await screen.findAllByText('0.00 M€')).not.toHaveLength(0)
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })

  it('renders both sides\' real shedding figures when both captured genuine, non-zero results (captured: true, available: true)', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(
        project,
        project === 'alpha'
          ? resolvedAlphaLostLoad
          : {
              available: true, captured: true, voll_eur_per_mwh: 2500,
              total_mwh: { total: 55.5, by_period: {} },
              total_cost_meur: { total: 0.14, by_period: {} },
              by_bus: [{ bus: 'BusB', energy_mwh: { total: 55.5, by_period: {} }, cost_meur: { total: 0.14, by_period: {} } }],
              by_carrier: [{
                carrier: 'electricity', bus_count: 1,
                energy_mwh: { total: 55.5, by_period: {} }, cost_meur: { total: 0.14, by_period: {} },
              }],
            },
      ) as never),
    )
    renderLostLoadTab()
    expect(await screen.findAllByText('123.4 MWh')).not.toHaveLength(0)
    expect(await screen.findAllByText('55.5 MWh')).not.toHaveLength(0)
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })

  // ── Review round 1 — C1 ────────────────────────────────────────────────
  // The three tests above all use an alpha with `available: true`, so the
  // OLD `!llA?.available && !llB?.available` gate never fired for any of
  // them regardless of whether it keyed on `available` or `captured` — none
  // of them exercised the gate itself. These three do: they hold `available`
  // false on BOTH sides (the only way to reach the gate at all) and vary
  // `captured` to walk its three branches.

  const uncapturedLostLoad = {
    available: false, captured: false, voll_eur_per_mwh: 0,
    total_mwh: { total: 0, by_period: {} },
    total_cost_meur: { total: 0, by_period: {} },
    by_bus: [], by_carrier: [],
  }
  const genuineZeroLostLoad = {
    available: false, captured: true, voll_eur_per_mwh: 3000,
    total_mwh: { total: 0, by_period: {} },
    total_cost_meur: { total: 0, by_period: {} },
    by_bus: [], by_carrier: [],
  }

  it('renders the plain unavailable marker — never the "neither shed load" claim — when NEITHER capture was ever read (captured: false on both sides)', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(project, uncapturedLostLoad) as never),
    )
    renderLostLoadTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    // Two solved-but-uncaptured projects must NOT get the "neither shed
    // load" prose — that claims a feasible dispatch was found on both
    // sides, which is unknown for both. This is the "likelier in the
    // field" case review finding C1 called out by name.
    expect(screen.queryByText(/Neither project shed load/)).toBeNull()
    expect(screen.queryByText(/feasible dispatch/)).toBeNull()
  })

  it('shows the resolved side\'s real zero — never the "neither shed load" claim — when the OTHER side\'s capture was never read (mixed captured/uncaptured, both available: false)', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(
        project, project === 'alpha' ? uncapturedLostLoad : genuineZeroLostLoad,
      ) as never),
    )
    renderLostLoadTab()
    // Both sides' `available` are false here — exactly what the OLD gate
    // keyed on, which would have shown "Neither project shed load" and
    // asserted alpha found a feasible dispatch despite alpha's capture
    // never being opened. Must fall through to the KPI section instead.
    expect(screen.queryByText(/Neither project shed load/)).toBeNull()
    expect(screen.queryByText(/feasible dispatch/)).toBeNull()
    // alpha's marker AND beta's real, rendered zero both reach the screen.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(await screen.findAllByText('0.0 MWh')).not.toHaveLength(0)
  })

  it('keeps the "neither shed load" claim when BOTH captures were read and BOTH are confirmed zero', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(project, genuineZeroLostLoad) as never),
    )
    renderLostLoadTab()
    expect(await screen.findByText('Neither project shed load.')).toBeTruthy()
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })

  // ── Review round 1 — C2 ────────────────────────────────────────────────
  // The VOLL-price panel is a fifth figure on this tab, rendered directly
  // from `llA?.voll_eur_per_mwh ?? 0` / `llB?.voll_eur_per_mwh ?? 0` with no
  // availability guard at all. `voll_eur_per_mwh` defaults to 0.0 on every
  // unread-capture return, so an unread side rendered a confident "0 €/MWh"
  // directly beside the marker its own KPI cells showed two rows up — the
  // review's `queryByText(/0\.0 MWh/)` check in the first test above could
  // never catch this: the VOLL panel formats with `.toFixed(0)` (no
  // decimal point at all), not `.toFixed(1)`.
  it('marks the VOLL price panel unavailable for an unread capture instead of a fabricated 0 €/MWh (C2)', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(lostLoadSummary(
        project, project === 'alpha' ? resolvedAlphaLostLoad : uncapturedLostLoad,
      ) as never),
    )
    renderLostLoadTab()
    expect(await screen.findByText('3000 €/MWh')).toBeTruthy()
    expect(screen.queryByText('0 €/MWh')).toBeNull()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
  })
})

function renderLoadingTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <LoadingTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

// LineLoadingEntry verified against api/types.ts — peak_loading/mean_loading
// are FRACTIONS (0..1), rendered ×100 by LoadingTable's fmtPct.
const loadingSummary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  loading: {
    available,
    lines: available
      ? [{
          name: 'Line1', s_nom_opt: 500, is_transformer: false, is_link: false, carrier: null,
          peak_loading: { total: 0.654, by_period: {} },
          mean_loading: { total: 0.321, by_period: {} },
          binding_hours: { total: 42, by_period: {} },
        }]
      : [],
  },
})

describe('LoadingTab distinguishes unavailable from zero', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders alpha\'s real branch row beside beta\'s marker — never a fabricated 0.0 % or a signed Δ', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(loadingSummary(project === 'alpha', project) as never),
    )
    const { container } = renderLoadingTab()
    // alpha's real peak loading renders (0.654 -> "65.4 %").
    expect(await screen.findByText('65.4 %')).toBeTruthy()
    // beta's loading block never resolved (lines: []) — Line1 only exists
    // in alpha's array, so without the availability guard beta's cells
    // would default through readPV's `!pv` branch to a fabricated 0 %.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText('0.0 %')).toBeNull()
    // The Δ peak cell for Line1's row must be a dash, never a signed number
    // computed against beta's fabricated 0 % loading.
    const row = (await screen.findByText('Line1')).closest('tr')
    expect(row?.textContent).not.toMatch(/[+-]\d/)
    // LoadingBarChart (peak-lines + mean-lines sections both render for
    // this fixture — one branch, no Links) must not plot beta's fabricated
    // 0 % as a bar. Text assertions can't reach recharts' SVG output, so
    // this counts mounted `<Bar>` series directly: verified empirically
    // (see task-6-report.md) that WITHOUT the availableA/availableB guard
    // on LoadingBarChart this fixture renders 4 `.recharts-bar` elements —
    // 2 chart sections × 2 series (alpha AND a fabricated beta bar in
    // each). With the guard, beta's series is never mounted at all, so
    // only 2 remain (2 sections × alpha's 1 real series).
    expect(container.querySelectorAll('.recharts-bar').length).toBe(2)
  })

  // Task 4 established `available: true` + a zero field as a LEGITIMATE
  // state — it must still render the real value, not the unavailable
  // marker AND not the "absent from this scenario" dash. LoadingTable's
  // hasA/hasB ladder is the most intricate of the three tabs fixed in this
  // task (per-branch presence AND per-side availability both gate the same
  // cell), so this is the one place that ladder rung is exercised at all.
  it('renders a genuine resolved 0 % as 0.0 %, never as the unavailable marker (available: true on both sides)', async () => {
    const bothResolvedZero = (project: string) => ({
      project,
      has_solve: true,
      periods: [],
      loading: {
        available: true,
        lines: [{
          // A fully-idle branch on a solved network genuinely has 0 %
          // loading — not an absence and not an unresolved figure.
          name: 'Line1', s_nom_opt: 500, is_transformer: false, is_link: false, carrier: null,
          peak_loading: { total: 0, by_period: {} },
          mean_loading: { total: 0, by_period: {} },
          binding_hours: { total: 0, by_period: {} },
        }],
      },
    })
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(bothResolvedZero(project) as never),
    )
    renderLoadingTab()
    // Both sides resolve with the SAME real-zero fixture (A/A identity), so
    // "0.0 %" legitimately renders more than once (peak + mean columns,
    // both sides) — assert plural/non-empty, same idiom as this file's
    // other A/A-identity checks.
    expect(await screen.findAllByText('0.0 %')).not.toHaveLength(0)
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })
})

function renderPricesTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <PricesTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

const pricesSummary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  prices: {
    available,
    duration_curve: available ? Array.from({ length: 101 }, (_, i) => 100 - i) : [],
    mean_price: { total: available ? 456.7 : 0, by_period: {} },
    median_price: { total: available ? 111.1 : 0, by_period: {} },
    p90_price: { total: available ? 222.2 : 0, by_period: {} },
    max_price: available ? 999 : 0,
    min_price: available ? -5 : 0,
    bus_count: available ? 3 : 0,
    by_carrier_stats: available
      ? {
          electricity: {
            bus_count: 3,
            mean_price: { total: 456.7, by_period: {} },
            median_price: { total: 111.1, by_period: {} },
            p90_price: { total: 222.2, by_period: {} },
          },
        }
      : {},
  },
})

describe('PricesTab distinguishes unavailable from zero', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders alpha\'s real price stats beside beta\'s marker — never a fabricated 0.0 €/MWh or a signed Δ', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(pricesSummary(project === 'alpha', project) as never),
    )
    renderPricesTab()
    // alpha's real mean price renders — legitimately twice (PricesTable's
    // "All periods" row AND PerCarrierPricesTable's electricity row), so
    // assert plural/non-empty rather than a single match.
    expect(await screen.findAllByText(/456\.70 €\/MWh/)).not.toHaveLength(0)
    // beta's prices block never resolved — its cells read the marker.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    // PricesTable/PerCarrierPricesTable format with TWO decimals
    // (`v.toFixed(2)`), but DurationCurveChart's extreme-badge strip
    // formats with ONE (`v.toFixed(1)`) — a `/0\.00/`-only check would
    // silently miss a fabricated badge value, which is exactly what
    // happened before this was caught on review. `0\.0+` catches both.
    expect(screen.queryByText(/0\.0+ €\/MWh/)).toBeNull()
    // The Δ mean-price cell must be a dash, never a signed number computed
    // against beta's fabricated €0.00.
    const meanRow = (await screen.findByText('All periods')).closest('tr')
    expect(meanRow?.textContent).not.toMatch(/[+-]\d/)
    // The duration-curve badge strip itself: beta's max_price/min_price
    // default to 0.0 on the backend and are ALWAYS serialised (unlike the
    // curve array, which is genuinely empty and safe), so without
    // DurationCurveChart's own availableA/availableB guard this specific
    // spot would read "beta: max 0.0 €/MWh · min 0.0 €/MWh" — a fabricated
    // number the two checks above don't reach (mean/median/p90 are a
    // different data path). getByText's default matcher concatenates only
    // an element's OWN direct text-node children (see this file's sibling
    // CompareView.test.tsx:238-243 for the same idiom applied to <Delta>),
    // so this matches the badge <span>'s own text specifically, not the
    // nested name <span> inside it.
    expect(await screen.findByText(/max unavailable · min unavailable/)).toBeTruthy()
  })
})

function renderOverviewTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <OverviewTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

// CompareState verified against api/types.ts — every field is REQUIRED
// (no `?`), unlike the ResultsSummary blocks above. Identical for alpha and
// beta on purpose: `optimised_capacity_by_carrier` is structural network
// data (compare-state, not the results-summary `capacity` block) and
// carries no `available` flag of its own — it must render as a real number
// on BOTH sides regardless of the results-summary capacity block's
// availability, proving the per-cell marker below is scoped correctly
// rather than blanket-applied across the whole tab.
const overviewCompareState = (project: string) => ({
  name: project,
  bus_count: 5, generator_count: 3, line_count: 4, load_count: 2,
  link_count: 1, storage_unit_count: 1, store_count: 0, snapshot_count: 10,
  snapshot_start: null, snapshot_end: null,
  installed_capacity_by_carrier: { solar: 150 },
  optimised_capacity_by_carrier: { solar: 200 },
  storage_capacity_by_carrier: { battery: 30 },
  store_capacity_by_carrier: {},
  peak_demand_mw: 120,
  total_energy_mwh: 300_000,
  objective: 1000,
  solve_time: 5,
  dispatch_status: 'fresh',
  parent_project: null,
  scenario_description: null,
  created_at: null,
  last_saved: null,
})

const overviewSummary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  capacity: {
    available,
    capacity_mw_by_carrier: {},
    capex_meur_by_carrier: {},
    new_capex_meur_by_carrier: {},
    // "Built" generator capacity — OverviewCapacityTable's built column.
    new_capacity_mw_by_carrier: available ? { solar: { total: 987.6, by_period: {} } } : {},
    // OverviewStorageTable reads ALL its columns from this same block.
    storage_mw_by_carrier: available ? { battery: { total: 234.5, by_period: {} } } : {},
    storage_mwh_by_carrier: available ? { battery: { total: 345.6, by_period: {} } } : {},
    new_storage_mw_by_carrier: {},
    new_storage_mwh_by_carrier: {},
    // OverviewLinksTable reads ALL its columns from this same block — links
    // have no compare-state equivalent at all (unlike generator capacity).
    link_capacity_mw_by_carrier: available ? { electrolysis: { total: 456.7, by_period: {} } } : {},
    new_link_capacity_mw_by_carrier: {},
  },
})

describe('OverviewTab distinguishes unavailable from zero', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders alpha\'s real capacity/storage/link figures beside beta\'s marker — never a fabricated 0.0 MW, while structural compare-state totals stay unmarked on both sides', async () => {
    vi.mocked(projectsApi.compareState).mockImplementation(
      (project: string) => Promise.resolve(overviewCompareState(project) as never),
    )
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (project: string) => Promise.resolve(overviewSummary(project === 'alpha', project) as never),
    )
    renderOverviewTab()
    // alpha's real "built" generator capacity, storage MW/MWh, and link
    // capacity all render as real numbers.
    expect(await screen.findByText('987.6 MW')).toBeTruthy()
    expect(await screen.findByText('234.5 MW')).toBeTruthy()
    expect(await screen.findByText('345.6 MWh')).toBeTruthy()
    expect(await screen.findByText('456.7 MW')).toBeTruthy()
    // beta's capacity block never resolved — several cells (built generator
    // capacity, all of storage, all of links) read the marker.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText('0.0 MW')).toBeNull()
    expect(screen.queryByText('0.0 MWh')).toBeNull()
    // Both sides' structural compare-state total (200 MW, identical fixture
    // for alpha and beta) render as real numbers — NOT gated by the
    // results-summary capacity block's availability. This is the Overview
    // judgement call: per-cell marking, scoped to the capacity-block-sourced
    // columns only, not a whole-tab banner that would incorrectly blank out
    // always-valid structural data.
    expect(await screen.findAllByText('200.0 MW')).toHaveLength(2)
  })
})
