// Walkthrough asset-investment 03: the sizing section of a stored EH report
// renders as a table of the sized assets.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { EhReferenceDesignReport, EhSizedAsset } from '../../api/simulation'
import { SizedDesignTable, sizedAssets, sizedBuilt, sizedRange } from './SizedDesignTable'

afterEach(cleanup)

const DIESEL: EhSizedAsset = { component: 'Generator', name: 'diesel_new', carrier: 'diesel',
  built_mw: 4.2, min_mw: 0, max_mw: 12 }
const BESS: EhSizedAsset = { component: 'StorageUnit', name: 'bess_new', carrier: 'battery',
  built_mw: 2.5, min_mw: 0, max_mw: 15, max_hours: 4, built_mwh: 10 }

const report = (sizing: unknown): EhReferenceDesignReport =>
  ({ sections: { sizing } } as unknown as EhReferenceDesignReport)

describe('SizedDesignTable', () => {
  it('renders each sized asset from a stored report', () => {
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
    expect(sizedBuilt({ ...DIESEL, built_mw: null })).toBe('not reported')
    expect(sizedBuilt({ component: 'Store', name: 'h2', carrier: 'H2', built_mwh: 30 })).toBe('30 MWh')
    expect(sizedRange({ ...DIESEL, max_mw: null })).toBe('no limit')
    // The solver's -0 is "none built", never "-0 MW".
    expect(sizedBuilt({ ...DIESEL, built_mw: -0 })).toBe('none built')
    expect(sizedBuilt({ ...DIESEL, built_mw: 1e-9 })).toBe('none built')
    expect(sizedRange({ ...DIESEL, min_mw: -0 })).toBe('0–12 MW')
    expect(sizedRange({ ...DIESEL, min_mw: 2, max_mw: null })).toBe('at least 2 MW')
  })
})
