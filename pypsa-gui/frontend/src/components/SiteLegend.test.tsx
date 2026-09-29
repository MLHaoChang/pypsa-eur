// Phase 2 plan Task 6.3: the 3D legend — asset types always; the loading and
// SoC bands, with their thresholds as numbers, only while results show.
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SiteLegend from './SiteLegend'
import { createResultsStore } from '../site3d/resultsStore'

const entries = [{ id: 'pv', label: 'PV field', color: '#16a34a' }, { id: 'bess', label: 'Battery storage', color: '#7c3aed' }]

describe('SiteLegend', () => {
  it('lists the types; the bands appear with their thresholds only while results show', () => {
    const store = createResultsStore()
    render(<SiteLegend entries={entries} results={store} />)
    expect(screen.getByText('PV field')).toBeTruthy()
    expect(screen.queryByText(/Loading/)).toBeNull()
    act(() => store.set({ idx: 0, iso: 't', states: new Map([['Generator:PV', { kind: 'output', mw: 1, cap: 2 }]]) }))
    const loading = screen.getByTestId('legend-loading').textContent!
    expect(loading).toMatch(/< ?50 %/); expect(loading).toMatch(/50.90 %/); expect(loading).toMatch(/≥ ?90 %/)
    const soc = screen.getByTestId('legend-soc').textContent!
    expect(soc).toMatch(/< ?20 %/); expect(soc).toMatch(/20.80 %/); expect(soc).toMatch(/≥ ?80 %/)
    act(() => store.set({ idx: 0, iso: '', states: new Map() }))
    expect(screen.queryByTestId('legend-loading')).toBeNull()
  })
})
