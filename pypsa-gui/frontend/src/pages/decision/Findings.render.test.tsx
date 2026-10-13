// "Why and how" and "How robust" (plan S8 acceptance; gate S6/S7 carries):
// the waterfall is labelled by `value_streams_basis`; tornado bars sort by
// swing; `robustness.pending` and the skip codes are shown in words.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import WhyHow from './WhyHow'
import Robust from './Robust'
import { findings, findingsPv, optionCase } from './__fixtures__/payloads'
import { HELP } from '../../utils/decisionVocabulary'

afterEach(() => cleanup())

describe('why and how', () => {
  it('labels the waterfall as the full saving against the baseline', () => {
    render(<WhyHow findings={findings} optionCase={optionCase} onExpert={vi.fn()} />)
    const wf = screen.getByTestId('waterfall')
    expect(wf.textContent).toContain('Full saving against the grid-only baseline')
    expect(wf.textContent).toContain('Energy time-shift')
  })

  it('labels a bess_pv waterfall as the battery increment over PV-only', () => {
    render(<WhyHow findings={findingsPv} optionCase={null} onExpert={vi.fn()} />)
    const wf = screen.getByTestId('waterfall')
    expect(wf.textContent).toContain('Battery increment over PV-only')
    expect(wf.textContent).not.toContain('Full saving')
  })

  it('shows the cumulative cash flow and marks the payback year', () => {
    render(<WhyHow findings={findings} optionCase={optionCase} onExpert={vi.fn()} />)
    const cf = screen.getByTestId('cash-flow')
    expect(cf.querySelector('polyline')).not.toBeNull()
    expect(cf.textContent).toContain('Discounted payback')
  })

  it('lists every option with its status and an Expert view entry for a solved fork', () => {
    const onExpert = vi.fn()
    render(<WhyHow findings={findings} optionCase={optionCase} onExpert={onExpert} />)
    const table = screen.getByTestId('option-table')
    for (const o of findings.options) expect(table.querySelector(`[data-option="${o.option_id}"]`)).not.toBeNull()
    screen.getAllByRole('button', { name: /Open in the Expert view/ })[0].click()
    expect(onExpert).toHaveBeenCalled()
  })
})

describe('how robust', () => {
  it('sorts the bars by swing, largest first', () => {
    const shuffled = { ...findings, robustness: { ...findings.robustness, tornado: [...findings.robustness.tornado].reverse() } }
    render(<Robust findings={shuffled} tornado={null} error={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false} />)
    const keys = screen.getAllByTestId('tornado-bar').map(el => el.getAttribute('data-key'))
    const bySwing = [...findings.robustness.tornado].sort((a, b) => (b.swing ?? 0) - (a.swing ?? 0)).map(r => r.key)
    expect(keys).toEqual(bySwing)
  })

  it('names the drivers never reached and the skipped ones with their reasons', () => {
    const rob = { ...findings.robustness, status: 'not_established' as const, note: 'tornado_aborted',
      pending: ['energy_price_level', 'discount_rate'],
      skipped: { demand_charge_price: 'tariff_has_no_demand_charge' } }
    render(<Robust findings={{ ...findings, robustness: rob }} tornado={null} error={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false} />)
    expect(screen.getByTestId('robust-note').textContent).toContain(HELP.tornado_aborted)
    const pending = screen.getByTestId('robust-pending')
    expect(pending.textContent).toContain('Energy price level')
    expect(pending.textContent).toContain('Discount rate')
    const skipped = screen.getByTestId('robust-skipped')
    expect(skipped.textContent).toContain(HELP.tariff_has_no_demand_charge)
    expect(skipped.textContent).toContain('tariff_has_no_demand_charge')
  })

  it('says how the tornado is charged before it starts', () => {
    render(<Robust findings={{ ...findings, robustness: { ...findings.robustness, status: 'not_established', tornado: [], note: 'tornado_not_run' } }}
      tornado={null} error={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false} />)
    expect(screen.getByTestId('robust-budget').textContent).toMatch(/charged/)
  })
})
