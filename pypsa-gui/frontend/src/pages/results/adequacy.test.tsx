import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { AdequacyChips, CoptChips, ensTargetWarning, type AdequacyReportPayload, type CoptPayload } from './adequacy'

afterEach(() => cleanup())

describe('ensTargetWarning — the 99% trap', () => {
  it('is silent for unset / zero / realistic targets', () => {
    expect(ensTargetWarning(null)).toBeNull()
    expect(ensTargetWarning(undefined)).toBeNull()
    expect(ensTargetWarning(0)).toBeNull()
    expect(ensTargetWarning(1)).toBeNull()
    expect(ensTargetWarning(100)).toBeNull()
  })
  it('trips above 100‱ and states the percentage', () => {
    const w = ensTargetWarning(150)
    expect(w).toMatch(/1\.5% of demand/)
    expect(ensTargetWarning(9900)).toMatch(/99%/)
  })
})

function report(overrides: Partial<AdequacyReportPayload['target']> = {},
                extra: Partial<AdequacyReportPayload> = {}): AdequacyReportPayload {
  return {
    engine: 'lp_proxy', fidelity: 'deterministic_scenario',
    target: {
      basis: 'energy',
      system: { cap_mwh: 120, achieved_ens_mwh: 119.9, achieved_shed_hours: 6 },
      zones: [], binding: 'system_cap', zone_field_populated: true,
      ...overrides,
    },
    metrics: { ens_mwh: 119.9, shed_hours: 6 },
    energy: { involuntary_mwh: 119.9, demand_response_mwh: 0 },
    ...extra,
  }
}

describe('AdequacyChips — the binding badge', () => {
  it('renders nothing without a report (endpoint 204)', () => {
    const { container } = render(<AdequacyChips report={null} />)
    expect(container.innerHTML).toBe('')
  })
  it('names the system cap as the standard', () => {
    render(<AdequacyChips report={report()} />)
    expect(screen.getByText(/standard: ENS cap/)).toBeTruthy()
    expect(screen.getByText(/ENS 119\.9 \/ cap 120\.0 MWh/)).toBeTruthy()
    expect(screen.getByText(/shed-hours 6\.0 h/)).toBeTruthy()
  })
  it('names VoLL when the cap did not bind', () => {
    render(<AdequacyChips report={report({ binding: 'voll' })} />)
    expect(screen.getByText(/standard: VoLL/)).toBeTruthy()
  })
  it('names the binding zone', () => {
    render(<AdequacyChips report={report({
      binding: 'zone_cap',
      zones: [{ zone: 'AA', cap_mwh: 10, achieved_ens_mwh: 10, binding: true },
              { zone: 'BB', cap_mwh: 10, achieved_ens_mwh: 2, binding: false }],
    })} />)
    expect(screen.getByText(/standard: zone ceiling AA/)).toBeTruthy()
  })
  it('shows DSR as not-unserved and flags unpopulated zones', () => {
    render(<AdequacyChips report={report(
      { zones: [{ zone: '', cap_mwh: 5, achieved_ens_mwh: 1, binding: false }],
        zone_field_populated: false },
      { energy: { involuntary_mwh: 10, demand_response_mwh: 240 } },
    )} />)
    expect(screen.getByText(/DSR 240\.0 MWh \(not unserved\)/)).toBeTruthy()
    expect(screen.getByText(/zones unpopulated/)).toBeTruthy()
  })
})


function coptPayload(extra: Partial<CoptPayload> = {}): CoptPayload {
  return {
    engine: 'copt', fidelity: 'analytic_convolution',
    metrics: { lole_hours: 1.68, eue_mwh: 33.6, lolp_max: 0.28, time_basis: 'hours_per_year' },
    per_mode: [], fleet: { units: 2, must_take: 1, delta_mw: 1 },
    voll_eur_per_mwh: 0,
    ...extra,
  }
}

describe('CoptChips — the screening row', () => {
  it('renders nothing without a payload (endpoint 204)', () => {
    const { container } = render(<CoptChips copt={null} proxyEnsMwh={null} />)
    expect(container.innerHTML).toBe('')
  })
  it('shows the screening metrics with the fidelity label', () => {
    render(<CoptChips copt={coptPayload()} proxyEnsMwh={null} />)
    expect(screen.getByText(/COPT screening/)).toBeTruthy()
    expect(screen.getByText(/LOLE 1\.7 h/)).toBeTruthy()
    expect(screen.getByText(/EUE 33\.6 MWh/)).toBeTruthy()
  })
  it('flags divergence when the screening EUE dwarfs the LP proxy', () => {
    render(<CoptChips copt={coptPayload()} proxyEnsMwh={2.0} />)
    expect(screen.getByText(/storage\/network carry the adequacy/)).toBeTruthy()
  })
  it('stays quiet when the two roughly agree', () => {
    render(<CoptChips copt={coptPayload()} proxyEnsMwh={30.0} />)
    expect(screen.queryByText(/storage\/network carry the adequacy/)).toBeNull()
  })
})
