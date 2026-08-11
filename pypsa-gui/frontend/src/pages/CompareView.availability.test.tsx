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
  projectsApi: { resultsSummary: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import { EconomicsTab, EmissionsTab, StorageCyclingTab } from './CompareView'

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
