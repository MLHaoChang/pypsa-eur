import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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
    expect(rows[0].textContent).toContain('power factor and load damping: PF 0.98')
    expect(rows[1].textContent).toContain('Simulation runs per check: 500')
    expect(rows[1].textContent).toContain('Assumed')
  })
  it('says so when an older study recorded none', () => {
    mount({ ...SITE_TEMPLATE, ledger: [] }, { status: 'done' })
    expect(screen.getByTestId('results-assumptions-none').textContent).toMatch(/did not record/)
  })
  it('renders nothing without a template', () => {
    mount(null, null)
    expect(screen.queryByTestId('results-assumptions')).toBeNull()
  })
  it('skips stated ledger rows', () => {
    expect(assumptionRows([{ item: 'x', value: 1, source: 'entered' }], [])).toEqual([])
  })
})
