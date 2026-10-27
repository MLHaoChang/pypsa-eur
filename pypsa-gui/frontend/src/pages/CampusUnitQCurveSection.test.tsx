import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { IbrQCurveRow } from '../api/campusElectrical'
import CampusUnitQCurveSection, { orderRows } from './CampusUnitQCurveSection'

afterEach(() => cleanup())

// Rows as gridspine's ibr_q_curve writes them: BESS1 (P_n 20 MW, S 21 MVA) against a generic curve that asks
// 0.95 pu at half load (short) and 0.3 pu at rated P (met); PV1 without an inverter rating.
const row = (over: Partial<IbrQCurveRow> = {}): IbrQCurveRow => ({
  unit: 'BESS1', kind: 'bess', p_pu: 1, p_mw: 20, s_mva: 21, q_min_req_mvar: -6, q_max_req_mvar: 6,
  available_mvar: 6.403, shortfall_mvar: 0, status: 'pass',
  detail: 'BESS1 at P 1 pu (20.00 MW) covers -6.00 to 6.00 Mvar with ±6.40 Mvar',
  clause: 'generic test clause', source: 'code', basis: 'screening at unit terminals', ...over,
})
const fail = row({
  p_pu: 0.5, p_mw: 10, q_min_req_mvar: -19, q_max_req_mvar: 19, available_mvar: 18.466, shortfall_mvar: 0.534,
  status: 'fail',
  detail: 'BESS1 at P 0.5 pu (10.00 MW) has ±18.47 Mvar for -19.00 to 19.00 Mvar: 0.53 Mvar short to deliver and 0.53 Mvar short to absorb',
})
const reported = row({
  unit: 'PV1', kind: 'pv', p_pu: null, p_mw: null, s_mva: null, q_min_req_mvar: null, q_max_req_mvar: null,
  available_mvar: null, shortfall_mvar: null, status: 'reported',
  detail: 'capability unknown: PV1 has no rating (P_n and the inverter S are needed)',
})

describe('CampusUnitQCurveSection', () => {
  it('is absent without a table, and says so for a run from before it was kept', () => {
    const { container } = render(<CampusUnitQCurveSection rows={null} />)
    expect(container.innerHTML).toBe('')
    render(<CampusUnitQCurveSection rows={null} notComputed />)
    expect(screen.getByTestId('qcurve-not-computed').textContent)
      .toBe('Not computed for this run. Rerun the study to add the check.')
  })

  it('shows a fail as Fail with its shortfall and its detail as visible text', () => {
    render(<CampusUnitQCurveSection rows={[row(), fail]} />)
    const tr = screen.getByTestId('qcurve-BESS1-0.5')
    expect(tr.dataset.status).toBe('fail')
    expect(within(tr).getByTestId('gr-verdict').dataset.verdict).toBe('fail')
    expect(tr.textContent).toContain('0.5 pu · 10 MW')
    expect(tr.textContent).toContain('−19 to +19 Mvar')
    expect(tr.textContent).toContain('±18.5 Mvar')
    expect(tr.textContent).toContain('0.534 Mvar')
    expect(screen.getByText(/Basis: screening at unit terminals: √\(S² − P²\)/)).toBeTruthy()
    expect(screen.getByTestId('qcurve-detail-BESS1-0.5').textContent)
      .toBe('±18.47 Mvar available for −19.00 to +19.00 Mvar: 0.53 Mvar short')
  })

  it('shows a pass with no shortfall as a dash and its detail only as a tooltip', () => {
    render(<CampusUnitQCurveSection rows={[row()]} />)
    const tr = screen.getByTestId('qcurve-BESS1-1')
    expect(within(tr).getByTestId('gr-verdict').dataset.verdict).toBe('pass')
    expect(tr.querySelectorAll('td')[4].textContent).toBe('—')
    expect(tr.getAttribute('title')).toBe(row().detail)
    expect(screen.queryByTestId('qcurve-detail-BESS1-1')).toBeNull()
  })

  it('shows a reported row as not checked, never pass, with its reason', () => {
    render(<CampusUnitQCurveSection rows={[reported]} />)
    const tr = screen.getByTestId('qcurve-PV1-none')
    expect(within(tr).queryByTestId('gr-verdict')).toBeNull()
    expect(tr.textContent).toContain('Not checked · reported only')
    expect(screen.getByTestId('qcurve-detail-PV1-none').textContent).toBe(reported.detail)
  })

  it('counts the checks and the short ones, orders rows by unit then P, and tags the curve source', () => {
    render(<CampusUnitQCurveSection rows={[row(), reported, fail]} />)
    expect(screen.getByText('3 checks · 1 short')).toBeTruthy()
    expect(orderRows([row(), reported, fail]).map(r => [r.unit, r.p_pu])).toEqual(
      [['BESS1', 0.5], ['BESS1', 1], ['PV1', null]])
    expect(screen.getByTestId('qcurve-clause').textContent).toBe('codeCurve: generic test clause')
  })

  it('tags a row whose curve is assumed', () => {
    render(<CampusUnitQCurveSection rows={[row({ source: 'assumed' })]} />)
    expect(within(screen.getByTestId('qcurve-BESS1-1')).getByText('assumed')).toBeTruthy()
  })
})
