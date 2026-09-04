import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { AdequacyChips, basisSuffix, CoptChips, ensTargetWarning, type AdequacyReportPayload, type CoptPayload } from './adequacy'

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
  // Phase 12c-pre: a unit that carries both a series and outage data is now
  // MODELLED on its series; the chip says how many, and how many were netted
  // beyond the exact cap, with the payload's sentence as its tooltip.
  it('names the profiled units and the netted remainder when the payload says so', () => {
    render(<CoptChips copt={coptPayload({
      fleet: { units: 12, must_take: 1, delta_mw: 1,
               profile_units: ['h1', 'h2', 'h3'], netted_beyond_cap: ['h3'], k_exact: 2 },
      fidelity_note: '3 unit(s) carry both an availability series and outage data (h1, h2, h3): '
        + 'outages are sampled on the series and the COPT mixes them exactly per hour over their '
        + 'outage states. 1 more beyond the exact cap of 2 (h3) are netted at expected output; '
        + 'their criticality rows understate their outages.',
    })} proxyEnsMwh={null} />)
    const chip = screen.getByTestId('copt-fidelity-note')
    expect(chip.textContent).toBe('3 on a profile, 1 netted beyond the cap')
    expect(chip.getAttribute('title')).toMatch(/mixes them exactly per hour/)
  })
  // Phase 12d: the engines mask by build year / lifetime; the chip counts
  // what was masked per period and carries the payload's sentence.
  it('names the masked units per period when the payload discloses activity', () => {
    render(<CoptChips copt={coptPayload({
      activity: {
        by_period: { '2030': { inactive: ['new', 'late'], partial: ['wind'] },
                     '2035': { inactive: [], partial: [] } },
        note: 'The engines mask assets by build year, lifetime and the active flag, as the LP and the reserve '
          + 'margin do — 2030: 2 inactive (new, late); 1 below nameplate, a later vintage not '
          + 'yet built (wind).',
      },
    })} proxyEnsMwh={null} />)
    const chip = screen.getByTestId('copt-activity-note')
    expect(chip.textContent).toBe('2 inactive in 2030, 1 partial in 2030')
    expect(chip.getAttribute('title')).toMatch(/build year, lifetime and the active flag/)
  })
  it('shows no activity chip when nothing is masked (null note) or on a pre-phase payload', () => {
    render(<CoptChips copt={coptPayload()} proxyEnsMwh={null} />)
    expect(screen.queryByTestId('copt-activity-note')).toBeNull()
    render(<CoptChips copt={coptPayload({
      activity: { by_period: { ALL: { inactive: [], partial: [] } }, note: null } })} proxyEnsMwh={null} />)
    expect(screen.queryByTestId('copt-activity-note')).toBeNull()
  })
  it('says nothing about profiles on a payload without the note (pre-phase or none)', () => {
    render(<CoptChips copt={coptPayload()} proxyEnsMwh={null} />)
    expect(screen.queryByTestId('copt-fidelity-note')).toBeNull()
    render(<CoptChips copt={coptPayload({ fidelity_note: null,
      fleet: { units: 2, must_take: 1, delta_mw: 1, profile_units: [], netted_beyond_cap: [] } })}
      proxyEnsMwh={null} />)
    expect(screen.queryByTestId('copt-fidelity-note')).toBeNull()
  })
})

// ── Multi-period: the summed headline hides which period bound.
//
// The cap is enforced per investment period. With two periods capped at 1800
// MWh each, one exactly on its limit and the other at zero, the sums render
// "ENS 1800 / cap 3600" — 50% headroom, when the binding period has none.
// The chip names the period so the reader is not misled by the sums beside it.
describe('AdequacyChips per-period disclosure', () => {
  const base = {
    engine: 'lp_proxy', fidelity: 'deterministic_scenario',
    metrics: { ens_mwh: 1800, shed_hours: 24 },
    energy: { involuntary_mwh: 1800, demand_response_mwh: 0 },
  }
  const mk = (by_period: Array<Record<string, unknown>>) => ({
    ...base,
    target: {
      basis: 'energy', binding: 'system_cap', zone_field_populated: true, zones: [],
      system: { cap_mwh: 3600, achieved_ens_mwh: 1800, achieved_shed_hours: 24, by_period },
    },
  }) as never

  it('names the binding period on a multi-period report', () => {
    render(<AdequacyChips report={mk([
      { period: '2030', cap_mwh: 1800, achieved_ens_mwh: 1800, binding: true },
      { period: '2040', cap_mwh: 1800, achieved_ens_mwh: 0, binding: false },
    ])} />)
    expect(screen.getByText(/binding period: 2030/i)).toBeTruthy()
  })

  it('says so plainly when several periods bind', () => {
    render(<AdequacyChips report={mk([
      { period: '2030', cap_mwh: 1800, achieved_ens_mwh: 1800, binding: true },
      { period: '2040', cap_mwh: 1800, achieved_ens_mwh: 1800, binding: true },
    ])} />)
    expect(screen.getByText(/binding periods: 2030, 2040/i)).toBeTruthy()
  })

  it('reports the period count when none binds, rather than staying silent', () => {
    render(<AdequacyChips report={mk([
      { period: '2030', cap_mwh: 1800, achieved_ens_mwh: 10, binding: false },
      { period: '2040', cap_mwh: 1800, achieved_ens_mwh: 0, binding: false },
    ])} />)
    expect(screen.getByText(/2 periods, none binding/i)).toBeTruthy()
  })

  it('adds no period chip on a single-period run', () => {
    render(<AdequacyChips report={mk([
      { period: 'ALL', cap_mwh: 1800, achieved_ens_mwh: 1800, binding: true },
    ])} />)
    expect(screen.queryByText(/binding period/i)).toBeNull()
    expect(screen.queryByText(/periods, none binding/i)).toBeNull()
  })
})

// LOLE is quoted per YEAR by convention and every reliability standard is
// written that way, but the engine sums over whatever horizon the model
// spans. A bare "h" beside a sub-annual figure invites exactly the comparison
// that must not be made: 80.86 h on a 168 h week reads as comfortably inside
// a 3 h/yr standard when the annualised truth is ~1400x outside it.
describe('basisSuffix', () => {
  it('says h/yr only when the horizon really is a year', () => {
    expect(basisSuffix({ time_basis: 'hours_per_year', horizon_years: 1 })).toBe('h/yr')
  })

  it('names the actual horizon instead of implying a year', () => {
    expect(basisSuffix({ time_basis: 'hours_per_horizon', horizon_years: 168 / 8760 }))
      .toBe('h / 168 h horizon')
  })

  it('still refuses to imply a year when the horizon length is unknown', () => {
    expect(basisSuffix({ time_basis: 'hours_per_horizon' })).toBe('h / horizon')
    expect(basisSuffix({ time_basis: 'hours_per_horizon', horizon_years: null }))
      .toBe('h / horizon')
    expect(basisSuffix({})).toBe('h / horizon')
  })
})

describe('CoptChips time basis', () => {
  const copt = (metrics: Record<string, unknown>) => ({
    engine: 'copt', fidelity: 'analytic_convolution',
    fleet: { units: 2, must_take: 0, delta_mw: 1 },
    voll_eur_per_mwh: 3000, per_mode: [],
    metrics: { lolp_max: 0.5, ...metrics },
  }) as never

  it('labels a sub-annual LOLE by its horizon, not as h/yr', () => {
    render(<CoptChips copt={copt({
      lole_hours: 80.86, eue_mwh: 28330.9,
      time_basis: 'hours_per_horizon', horizon_years: 168 / 8760,
    })} proxyEnsMwh={null} />)
    expect(screen.getByText(/80\.9 h \/ 168 h horizon/)).toBeTruthy()
    expect(screen.queryByText(/80\.9 h\/yr/)).toBeNull()
  })

  it('labels a genuinely annualised LOLE as h/yr', () => {
    render(<CoptChips copt={copt({
      lole_hours: 4216.05, eue_mwh: 1477252.7,
      time_basis: 'hours_per_year', horizon_years: 1,
    })} proxyEnsMwh={null} />)
    expect(screen.getByText(/4216\.1 h\/yr/)).toBeTruthy()
  })
})
