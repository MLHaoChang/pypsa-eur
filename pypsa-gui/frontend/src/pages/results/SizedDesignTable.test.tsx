// Walkthrough asset-investment 03: the sizing section of a stored EH report
// renders as a table of the sized assets.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { EhReferenceDesignReport, EhSizedAsset } from '../../api/simulation'
import type { FlowHeld } from '../../api/flow'
import { SizedDesignTable, sizedAssets, sizedBuilt, sizedRange } from './SizedDesignTable'

// Section 5's rows carry each asset's unit from its bus (`flow_service._size_unit`).
let holds: Partial<FlowHeld>[] = []
vi.mock('../../hooks/useProjectFlow', () => ({
  useProjectFlow: () => ({ flow: { sections: [{ section: 'assets', holds }] } }),
}))

afterEach(() => { cleanup(); holds = [] })

const DIESEL: EhSizedAsset = { component: 'Generator', name: 'diesel_new', carrier: 'diesel',
  built_mw: 4.2, min_mw: 0, max_mw: 12 }
const BESS: EhSizedAsset = { component: 'StorageUnit', name: 'bess_new', carrier: 'battery',
  built_mw: 2.5, min_mw: 0, max_mw: 15, max_hours: 4, built_mwh: 10 }

const report = (sizing: unknown): EhReferenceDesignReport =>
  ({ sections: { sizing } } as unknown as EhReferenceDesignReport)

describe('SizedDesignTable', () => {
  it('renders each sized asset from a stored report', () => {
    holds = [{ component: 'Generator', entity: 'diesel_new', unit: 'MW' },
      { component: 'StorageUnit', entity: 'bess_new', unit: 'MW' }]
    render(<SizedDesignTable testId="t" mode="guided"
      report={report({ status: 'ok', payload: { by_carrier: {}, total_p_nom_mw: 0, assets: [DIESEL, BESS] } })} />)
    expect(screen.getByTestId('t-row-diesel_new').textContent).toBe('diesel_newGenerator, diesel4.2 MW0–12 MW')
    expect(screen.getByTestId('t-row-bess_new').textContent)
      .toContain('2.5 MW · 10 MWh (4 h)')
  })

  it('renders nothing for an older report, a failed section or no sized asset', () => {
    expect(sizedAssets(report({ status: 'ok', payload: { by_carrier: {} } }))).toEqual([])
    expect(sizedAssets(report({ status: 'failed', payload: { assets: [DIESEL] } }))).toEqual([])
    expect(sizedAssets(null)).toEqual([])
    const { container } = render(<SizedDesignTable testId="t" mode="expert" report={null} />)
    expect(container.textContent).toBe('')
  })

  it('words the size and the range', () => {
    expect(sizedBuilt({ ...DIESEL, built_mw: null }, 'MW')).toBe('not reported')
    expect(sizedBuilt({ component: 'Store', name: 'h2', carrier: 'H2', built_mwh: 30 }, 'MWh')).toBe('30 MWh')
    expect(sizedRange({ ...DIESEL, max_mw: null }, 'MW')).toBe('no limit')
    // The solver's -0 is "none built", never "-0 MW".
    expect(sizedBuilt({ ...DIESEL, built_mw: -0 }, 'MW')).toBe('none built')
    expect(sizedBuilt({ ...DIESEL, built_mw: 1e-9 }, 'MW')).toBe('none built')
    expect(sizedRange({ ...DIESEL, min_mw: -0 }, 'MW')).toBe('0–12 MW')
    expect(sizedRange({ ...DIESEL, min_mw: 2, max_mw: null }, 'MW')).toBe('at least 2 MW')
    // No lower bound is not a lower bound of 0 (GK B3 (3) 4).
    expect(sizedRange({ ...DIESEL, min_mw: null }, 'MW')).toBe('up to 12 MW')
  })

  it('a tonne bus sizes in t/h and t, never MW (GK B3 (3) 1)', () => {
    const nh3: EhSizedAsset = { component: 'Store', name: 'nh3_tank', carrier: 'NH3', built_mwh: 500, min_mwh: 0, max_mwh: 900 }
    const synth: EhSizedAsset = { component: 'StorageUnit', name: 'nh3_buffer', carrier: 'NH3',
      built_mw: 2, min_mw: 0, max_mw: 5, max_hours: 24, built_mwh: 48 }
    expect(sizedBuilt(nh3, 't')).toBe('500 t')
    expect(sizedRange(nh3, 't')).toBe('0–900 t')
    expect(sizedBuilt(synth, 't/h')).toBe('2 t/h · 48 t (24 h)')
    holds = [{ component: 'Store', entity: 'nh3_tank', unit: 't' }]
    render(<SizedDesignTable testId="t" mode="expert"
      report={report({ status: 'ok', payload: { assets: [nh3] } })} />)
    expect(screen.getByTestId('t-row-nh3_tank').textContent).toBe('nh3_tankStore, NH3500 t0–900 t')
  })

  it('an asset whose unit is not known shows the figure without one', () => {
    expect(sizedBuilt(DIESEL)).toBe('4.2')
    expect(sizedBuilt(BESS, null)).toBe('2.5 · 10 (4 h)')
    expect(sizedRange(DIESEL)).toBe('0–12')
    // A unit the frontend has no stock form for keeps the power unit only.
    expect(sizedBuilt(BESS, 'kW')).toBe('2.5 kW · 10 (4 h)')
    render(<SizedDesignTable testId="t" mode="expert"
      report={report({ status: 'ok', payload: { assets: [DIESEL] } })} />)
    expect(screen.getByTestId('t-row-diesel_new').textContent).toBe('diesel_newGenerator, diesel4.20–12')
  })
  it("a row's own unit wins; an older row without one takes section 5's (issue 20)", () => {
    const ore: EhSizedAsset = { component: 'Store', name: 'ore', carrier: 'ore', built_mwh: 30000,
      min_mwh: 0, max_mwh: 60000, unit: 't' }
    const pv: EhSizedAsset = { component: 'Generator', name: 'pv', carrier: 'solar', built_mw: 5,
      min_mw: 0, max_mw: 10, unit: null }
    // Section 5 lists neither row with that unit (past its 200-row cap, or a stale unit).
    holds = [{ component: 'Store', entity: 'ore', unit: 'MWh' }, { component: 'Generator', entity: 'pv', unit: 'MW' },
      { component: 'Generator', entity: 'diesel_new', unit: 'MW' }]
    render(<SizedDesignTable testId="t" mode="guided"
      report={report({ status: 'ok', payload: { assets: [ore, pv, DIESEL] } })} />)
    expect(screen.getByTestId('t-row-ore').textContent).toBe('oreStore, ore30,000 t0–60,000 t')
    expect(screen.getByTestId('t-row-pv').textContent).toBe('pvGenerator, solar50–10')
    expect(screen.getByTestId('t-row-diesel_new').textContent).toBe('diesel_newGenerator, diesel4.2 MW0–12 MW')
  })

  it('a staged plan leads with the period and lists by it', () => {
    const late: EhSizedAsset = { ...DIESEL, name: 'diesel_2035', stage: 2035, unit: 'MW' }
    const early: EhSizedAsset = { ...DIESEL, name: 'diesel_2027', stage: 2027, unit: 'MW' }
    render(<SizedDesignTable testId="t" mode="expert"
      report={report({ status: 'ok', payload: { assets: [late, early] } })} />)
    expect(screen.getByTestId('t').querySelector('th')?.textContent).toBe('Period')
    expect([...screen.getByTestId('t').querySelectorAll('tbody tr')].map(r => r.firstChild?.textContent))
      .toEqual(['2027', '2035'])
  })
})
