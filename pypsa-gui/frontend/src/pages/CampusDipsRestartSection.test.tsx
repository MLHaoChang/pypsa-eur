// Grid-to-process 12b-3: "Dips and restarts" in the campus results.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { DipsRestart, DipsRestartInputs, DipTripRow, RestartRow } from '../api/campusElectrical'
import { useUIStore } from '../store/uiStore'
import fixture from '../test/fixtures/assetSchema.json'

const nav = vi.hoisted(() => ({ inPlace: vi.fn(), expert: vi.fn() }))
vi.mock('../layout/flowNavigation', () => ({
  openFlowTargetInPlace: nav.inPlace, openFlowTarget: nav.expert,
}))

import CampusDipsRestartSection, { DIPS_TITLE, fieldLinkLabel } from './CampusDipsRestartSection'

const NONE: DipsRestartInputs = {
  ride_through_curve: null, restart_time_h: null, motor_kw: null, start_method: null, i_start_pu: null,
}

const dipUnit = (unit: string, over: Partial<DipTripRow> = {}): DipTripRow => ({
  level: 'unit', unit, trips_per_year: 20.45, restart_time_h: 4, rate_tph: 4, mass_pull: 1, lost_t_per_trip: 16,
  lost_t_per_year: 327.2, verdict: 'known', dip_stats_source: 'demo', note: null, ...over,
})

const restartUnit = (transition: 'trip' | 'island', unit: string, start: string, over: Partial<RestartRow> = {}): RestartRow => ({
  level: 'unit', transition, unit, start_verdict: start, verdict: start, du_pct: start === 'unknown' ? null : 1.62,
  lost_t: 16.07, note: null, ...over,
})

const total = (transition: 'trip' | 'island', over: Partial<RestartRow> = {}): RestartRow => ({
  level: 'total', transition, lost_t: 32.1, fail_count: 0, unknown_count: 0, lower_bound: false, ...over,
})

function payload(over: Partial<Extract<DipsRestart, { inputs: unknown }>> = {}): DipsRestart {
  return {
    inputs: {
      mill: { ride_through_curve: '20:0.5', restart_time_h: 4, motor_kw: 1500, start_method: 'vfd', i_start_pu: 1.2 },
      crusher: { ride_through_curve: '20:0.5', restart_time_h: 2, motor_kw: 9000, start_method: 'dol', i_start_pu: 6 },
      pump: NONE,
    },
    dip_label: 'screening estimate', restart_label: 'event model, not dynamics', basis: ['demo dip statistics'],
    dip_trips: [
      dipUnit('mill'), dipUnit('crusher', { restart_time_h: 2, lost_t_per_year: 163.6 }),
      dipUnit('pump', { verdict: 'unknown', trips_per_year: null, restart_time_h: null, lost_t_per_year: null }),
      { level: 'total', lost_t_per_year: 490.8, incomplete: true, unknown_count: 1, known_count: 2, dip_stats_source: 'demo' },
    ],
    restart: [
      restartUnit('trip', 'pump', 'unknown'), restartUnit('trip', 'mill', 'pass'),
      restartUnit('trip', 'crusher', 'fail', { du_pct: 29.63 }),
      { level: 'event', transition: 'trip', unit: 'mill', t_s: 61, event: 'pickup', du_pct: 1.62, verdict: 'pass', note: 'motor start on the PoC' },
      total('trip', { fail_count: 1, unknown_count: 1, lower_bound: true }),
      restartUnit('island', 'pump', 'unknown', { note: 'no genset on the island: its short-circuit power is unknown; no input: restart time not set; motor start on the island unknown' }),
      restartUnit('island', 'mill', 'unknown', { note: 'no genset on the island: its short-circuit power is unknown; motor start on the island unknown' }),
      restartUnit('island', 'crusher', 'unknown', { note: 'no genset on the island: its short-circuit power is unknown; motor start on the island unknown' }),
      { level: 'event', transition: 'island', unit: 'mill', t_s: 0, event: 'island', note: 'mill trips' },
      total('island', { unknown_count: 3, lower_bound: true }),
    ],
    out_of_date: [],
    ...over,
  }
}

beforeEach(() => { nav.inPlace.mockReset(); nav.expert.mockReset() })
afterEach(() => { cleanup(); useUIStore.setState({ uiMode: 'expert' }) })

const show = (mode: 'guided' | 'expert', dips: DipsRestart | null = payload()) => {
  useUIStore.setState({ uiMode: mode })
  return render(<CampusDipsRestartSection dips={dips} />)
}

describe('CampusDipsRestartSection', () => {
  it('shows each unit with its ok, fail and unknown verdicts, the ΔU in % ΔU', () => {
    show('expert')
    expect(screen.getByText(DIPS_TITLE)).toBeTruthy()
    const verdict = (id: string) => within(screen.getByTestId(id)).getByTestId('gr-verdict').dataset.verdict
    expect(verdict('dips-trip-mill')).toBe('pass')
    expect(verdict('dips-trip-crusher')).toBe('fail')
    expect(verdict('dips-trip-pump')).toBe('unknown')
    expect(screen.getByTestId('dips-trip-crusher').textContent).toContain('29.6 % ΔU')
    expect(screen.getByTestId('dips-unit-mill').textContent).toContain('4 h')
    expect(screen.getByTestId('dips-unit-mill').textContent).toContain('327.2 t/yr')
  })

  it('reads the dip total incomplete and the restart totals lower bound, each with a reason, plurals written out', () => {
    show('guided')
    const dip = screen.getByTestId('dips-total').textContent!
    expect(dip).toContain('490.8 t/yr')
    expect(dip).toContain('incomplete')
    expect(dip).toContain('1 unit has no ride-through curve')
    const trip = screen.getByTestId('dips-restart-total-trip').textContent!
    expect(trip).toContain('lower bound')
    expect(trip).toContain('1 motor start fails the voltage-change limit; 1 restart is not known')
    expect(screen.getByTestId('dips-restart-total-island').textContent).toContain(
      '3 restarts are not known (1 unit is missing an input; 2 have no verdict: no genset on the island: its short-circuit power is unknown)')
  })

  it('labels demo dip statistics demo', () => {
    show('guided')
    expect(screen.getByTestId('dips-demo').textContent).toContain(
      "The dip counts are demo statistics, not this site's. Ask the grid operator for this site's dip statistics.")
  })

  it('a total with nothing unknown has no tag', () => {
    const p = payload() as Extract<DipsRestart, { inputs: unknown }>
    p.dip_trips = p.dip_trips.map(r => (r.level === 'total' ? { ...r, incomplete: false, unknown_count: 0 } : r))
    show('expert', p)
    expect(screen.getByTestId('dips-total').textContent).not.toContain('incomplete')
  })

  it('links an unknown row to its missing field through the row-target path, in place in Guided', () => {
    show('guided')
    const link = screen.getByTestId('dips-fix-pump-ride_through_curve')
    expect(link.textContent).toBe('Ride-through curve ›')
    fireEvent.click(link)
    expect(nav.inPlace).toHaveBeenCalledWith(
      { section: 'assets', panel: null, component: 'Link', entity: 'pump', field: 'ride_through_curve' })
    expect(nav.expert).not.toHaveBeenCalled()
    // The restart's missing input is its restart time first.
    fireEvent.click(within(screen.getByTestId('dips-trip-pump')).getByText('Restart time ›'))
    expect(nav.inPlace).toHaveBeenLastCalledWith(expect.objectContaining({ entity: 'pump', field: 'restart_time_h' }))
  })

  it('opens the field in Expert from Expert', () => {
    show('expert')
    fireEvent.click(screen.getByTestId('dips-fix-pump-ride_through_curve'))
    expect(nav.expert).toHaveBeenCalledWith(expect.objectContaining({ entity: 'pump', field: 'ride_through_curve' }))
    expect(nav.inPlace).not.toHaveBeenCalled()
  })

  it('says in words why a restart is unknown when no field of the unit is missing, with no link', () => {
    show('guided')
    expect(within(screen.getByTestId('dips-island-mill')).queryByRole('button')).toBeNull()
    expect(screen.getByTestId('dips-reason-island-mill').textContent)
      .toBe('No genset on the island: its short-circuit power is unknown')
    // A unit missing its own input gets the link, not the reason.
    expect(screen.queryByTestId('dips-reason-island-pump')).toBeNull()
  })

  it('writes the PoC as the grid connection in the timeline', () => {
    show('expert')
    expect(screen.getByTestId('dips-timeline-trip').textContent).toContain('motor start on the grid connection')
    expect(screen.getByTestId('dips-timeline-trip').textContent).not.toContain('PoC')
  })

  it('names each link with the form field\'s own label (parity with the asset schema)', () => {
    type Field = { technical_name: string; label: string }
    const fields = (fixture as Record<string, { groups: { fields: Field[] }[] }>).Link.groups.flatMap(g => g.fields)
    for (const f of ['ride_through_curve', 'restart_time_h', 'motor_kw', 'start_method', 'i_start_pu'] as const) {
      const label = fields.find(x => x.technical_name === f)?.label
      expect(label, f).toBeTruthy()
      expect(fieldLinkLabel(f)).toBe(`${label} ›`)
    }
  })

  it('marks a unit out of date when its field changed after the run, and only that unit', () => {
    show('guided', payload({ out_of_date: ['mill'] }))
    expect(screen.getByTestId('dips-stale-mill').textContent).toBe('Out of date')
    expect(screen.queryByTestId('dips-stale-pump')).toBeNull()
    expect(screen.getByTestId('dips-stale-note').textContent).toContain('1 unit has a restart, ride-through or motor field changed')
  })

  it('shows the timeline in Expert only; the table and verdicts are the same in both modes', () => {
    show('expert')
    expect(screen.getByTestId('dips-timeline-trip')).toBeTruthy()
    expect(screen.getByTestId('dips-timeline-island')).toBeTruthy()
    const expertRow = screen.getByTestId('dips-unit-crusher').textContent
    cleanup()
    show('guided')
    expect(screen.queryByTestId('dips-timeline-trip')).toBeNull()
    expect(screen.getByTestId('dips-unit-crusher').textContent).toBe(expertRow)
  })

  it('says when the run predates the screens, or they could not run', () => {
    show('guided', null)
    expect(screen.getByTestId('dips-not-computed')).toBeTruthy()
    cleanup()
    show('guided', { error: 'no process unit' })
    expect(screen.getByTestId('dips-error').textContent).toContain('no process unit')
  })
})
