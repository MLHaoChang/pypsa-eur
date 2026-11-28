import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SITE_TEMPLATE } from '../testFixtures'
import { AssumptionsBehindResult, SYNTHETIC_TEXT, assumptionRows } from './AssumptionsBehindResult'
import * as hub from '../useHubData'

vi.mock('../useHubData', () => ({ useHubTemplate: vi.fn(), useHubStudy: vi.fn() }))

const run = { key: 'mc_draws', label: 'Simulation runs per check', shown: '500', reason: 'default', value: 500,
  source: 'default', section: 'goal', card: '', component: '', entity: '', field: '' }

function mount(template: unknown, study: unknown) {
  vi.mocked(hub.useHubTemplate).mockReturnValue({ template } as never)
  vi.mocked(hub.useHubStudy).mockReturnValue({ study } as never)
  return render(<AssumptionsBehindResult />)
}
afterEach(cleanup)

describe('AssumptionsBehindResult', () => {
  it('lists ledger and run-assumed inputs with the synthetic line (same rows as the report)', () => {
    mount(SITE_TEMPLATE, { status: 'done', assumptions: [run] })
    expect(screen.getByTestId('results-assumptions-synthetic').textContent).toBe(SYNTHETIC_TEXT)
    const rows = screen.getAllByTestId('results-assumptions-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('Power factor and load damping: PF 0.98')
    expect(rows[1].textContent).toContain('Simulation runs per check: 500')
    expect(rows[1].textContent).toContain('Assumed')
  })
  it('says so when an older study recorded none', () => {
    mount({ ...SITE_TEMPLATE, ledger: [] }, { status: 'done' })
    expect(screen.getByTestId('results-assumptions-none').textContent).toMatch(/did not record the defaults it assumed\./)
  })
  it('hides the synthetic line once nothing assumed stands, and names a label-less row plainly', () => {
    mount({ ...SITE_TEMPLATE, ledger: [{ item: 'power_factor', value: 0.9800000001, source: 'entered' },
      { item: 'discount_rate', value: 0.0700000001, source: 'assumed' }] }, { status: 'done', assumptions: [] })
    expect(screen.getByTestId('results-assumptions-synthetic')).toBeTruthy()
    const row = screen.getByTestId('results-assumptions-row').textContent ?? ''
    expect(row).toContain('Discount rate: 0.07')
    expect(row).not.toContain('discount_rate')
    cleanup()
    mount({ ...SITE_TEMPLATE, ledger: [{ item: 'x', value: 1, source: 'entered' }] }, { status: 'done', assumptions: [] })
    expect(screen.queryByTestId('results-assumptions-synthetic')).toBeNull()
  })
  it('folds after five rows', () => {
    const many = Array.from({ length: 7 }, (_, i) => ({ ...run, key: `k${i}`, label: `L${i}` }))
    mount({ ...SITE_TEMPLATE, ledger: [] }, { status: 'done', assumptions: many })
    expect(screen.getAllByTestId('results-assumptions-row')).toHaveLength(5)
    expect(screen.getByTestId('results-assumptions-toggle').textContent).toBe('(2 more) Show all')
    fireEvent.click(screen.getByTestId('results-assumptions-toggle'))
    expect(screen.getAllByTestId('results-assumptions-row')).toHaveLength(7)
  })
  it('renders when the template arrives after a first empty render (hook order)', () => {
    vi.mocked(hub.useHubTemplate).mockReturnValue({ template: null } as never)
    vi.mocked(hub.useHubStudy).mockReturnValue({ study: null } as never)
    const { rerender } = render(<AssumptionsBehindResult />)
    vi.mocked(hub.useHubTemplate).mockReturnValue({ template: SITE_TEMPLATE } as never)
    rerender(<AssumptionsBehindResult />)
    expect(screen.getByTestId('results-assumptions')).toBeTruthy()
  })
  it('reads a percent ledger value with its unit', () => {
    mount({ ...SITE_TEMPLATE, ledger: [{ item: 'discount_rate', value: 7, unit: '%', source: 'assumed' }] }, { status: 'done', assumptions: [] })
    expect(screen.getByTestId('results-assumptions-row').textContent).toContain('Discount rate: 7 %')
  })
  it('renders nothing without a template', () => {
    mount(null, null)
    expect(screen.queryByTestId('results-assumptions')).toBeNull()
  })
  it('skips stated ledger rows', () => {
    expect(assumptionRows([{ item: 'x', value: 1, source: 'entered' }], [])).toEqual([])
  })
})
