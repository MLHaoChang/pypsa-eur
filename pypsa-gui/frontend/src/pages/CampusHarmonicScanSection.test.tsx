import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { HarmonicResonance, HarmonicScan } from '../api/campusElectrical'
import CampusHarmonicScanSection, { characteristicOrders } from './CampusHarmonicScanSection'

afterEach(() => cleanup())

const resonance = (over: Partial<HarmonicResonance> = {}): HarmonicResonance => ({
  case: 'max', cap_state: 'all capacitors in', h_r: 7.3, f_hz: 365, z_peak_ohm: 41.2, prominence: 3,
  nearest_order: 7, order_set: '6k±1', margin_h: 0.3, limit_h: 0.5, verdict: 'fail', assumed: false, note: null,
  ...over,
})

const scan = (over: Partial<Extract<HarmonicScan, { limit_h: number }>> = {}): HarmonicScan => ({
  limit_h: 0.5, limit_source: 'code', clause: 'Test code §1', notes: [], basis: ['the grid is an equivalent source'],
  label: 'screening; filter design is a harmonic study',
  resonance: [resonance()],
  envelope: [{ h: 1, z_max_ohm: 2, x_min_ohm: 1, x_max_ohm: 2 }, { h: 50, z_max_ohm: 3, x_min_ohm: -1, x_max_ohm: 3 }],
  ...over,
})

describe('CampusHarmonicScanSection', () => {
  it('lists the characteristic orders 6k±1 up to h max', () => {
    expect(characteristicOrders(26)).toEqual([5, 7, 11, 13, 17, 19, 23, 25])
  })

  it('shows a resonance inside the clearance as a fail, with its margin against the limit', () => {
    render(<CampusHarmonicScanSection scan={scan()} />)
    const row = screen.getByTestId('scan-resonance')
    expect(within(row).getByTestId('gr-verdict').dataset.verdict).toBe('fail')
    expect(within(row).getByTestId('scan-clearance').textContent).toBe('0.3 h · limit ±0.5 h')
    expect(row.textContent).toContain('Maximum short circuit')
    expect(row.textContent).toContain('h 7 (6k±1)')
    expect(screen.getByTestId('scan-caption').textContent).toContain('6k±1 (5, 7, 11, 13 …)')
    expect(screen.getByTestId('scan-limit').textContent).toContain('Clearance ±0.5 h: Test code §1')
    expect(screen.getByTestId('scan-chart')).toBeTruthy()
  })

  it('says when no resonance is found', () => {
    render(<CampusHarmonicScanSection scan={scan({ resonance: [] })} />)
    expect(screen.getByTestId('scan-none').textContent).toBe('No parallel resonance up to h 50 in any case.')
    expect(screen.queryByTestId('scan-resonance')).toBeNull()
  })

  it('says the clearance is the screen\'s default when the grid code sets none', () => {
    render(<CampusHarmonicScanSection scan={scan({ limit_source: null, clause: null,
      resonance: [resonance({ verdict: 'pass', margin_h: 0.8, assumed: true })] })} />)
    expect(screen.getByTestId('scan-limit').textContent).toContain(
      'The grid code sets no resonance clearance, so the screen takes ±0.5 h.')
    expect(within(screen.getByTestId('scan-resonance')).getByText('assumed')).toBeTruthy()
  })

  it('shows why the scan could not run, never an empty chart', () => {
    render(<CampusHarmonicScanSection scan={{ error: 'harmonic scan: the campus has no grid connection' }} />)
    expect(screen.getByTestId('scan-error').textContent).toContain('the campus has no grid connection')
    expect(screen.queryByTestId('scan-chart')).toBeNull()
  })

  it('has an empty state for a run from before the scan', () => {
    render(<CampusHarmonicScanSection scan={null} />)
    expect(screen.getByTestId('scan-not-computed').textContent).toBe(
      'Not computed for this run. Rerun the study to add the scan.')
    expect(screen.queryByTestId('scan-chart')).toBeNull()
  })
})
