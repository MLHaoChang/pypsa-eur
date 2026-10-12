// The map legend (M5) draws the shared voltage tiers, the link carriers on
// the map and the asset-type rows the schematic and 3D legends share.
import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MapLegend from './MapLegend'
import { VOLTAGE_TIERS, lineColor } from '../utils/voltageTiers'
import { legendFor } from '../utils/assetTypes'

beforeEach(() => { localStorage.clear() })

describe('MapLegend', () => {
  it('lists every voltage tier, the carriers and the asset types present', () => {
    const rows = legendFor([{ kind: 'pv', carrier: 'solar' }, { kind: 'bess', carrier: 'battery' }, { kind: 'thermal', carrier: 'gas' }])
    render(<MapLegend linkCarriers={['H2', 'heat']} assetRows={rows} />)
    expect(screen.getAllByTestId('legend-voltage')).toHaveLength(VOLTAGE_TIERS.length)
    expect(screen.getByText('> 300 kV')).toBeTruthy()
    const carriers = screen.getAllByTestId('legend-carrier').map(e => e.textContent ?? '')
    expect(carriers).toHaveLength(2)
    expect(carriers[0]).toContain('H2')
    expect(carriers[1]).toContain('heat')
    const types = screen.getAllByTestId('legend-asset-type').map(e => e.textContent)
    // Table order (generators, then storage), the same order the schematic lists them in.
    expect(types).toEqual(['PV field', 'Engine gensets (gas)', 'Battery storage'])
  })

  it('folds to a toggle and remembers it', async () => {
    render(<MapLegend linkCarriers={[]} assetRows={[]} />)
    expect(screen.getByText('No assets')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Hide legend' }))
    expect(screen.queryByTestId('map-legend')).toBeNull()
    expect(localStorage.getItem('pypsa.mapLegendOpen')).toBe('false')
    await userEvent.click(screen.getByTestId('map-legend-toggle'))
    expect(screen.getByTestId('map-legend')).toBeTruthy()
  })

  it('the tiers are the colours the lines use', () => {
    expect(lineColor(380)).toBe(VOLTAGE_TIERS[0].color)
    expect(lineColor(220)).toBe(VOLTAGE_TIERS[1].color)
    expect(lineColor(110)).toBe(VOLTAGE_TIERS[2].color)
    expect(lineColor(33)).toBe(VOLTAGE_TIERS[3].color)
  })
})
