import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../../store/uiStore'
import type { ReadinessRow } from '../../api/profiles'
import { MissingCoordinatesBanner, readinessBadge } from './RenewableReadiness'

const row = (o: Partial<ReadinessRow>): ReadinessRow => ({
  generator: 'g_wind', technology: 'wind', bus: 'b1', lon: 8, lat: 50,
  coord_source: 'bus', profile: 'none', ...o,
})

afterEach(() => cleanup())
beforeEach(() => useUIStore.setState({ uiMode: 'guided', fieldDrawer: null }))

describe('readinessBadge', () => {
  it('says what is missing or where the profile came from', () => {
    expect(readinessBadge(row({ coord_source: 'missing', lon: null, lat: null })).text).toBe('No location')
    expect(readinessBadge(row({})).text).toBe('Ready to generate')
    expect(readinessBadge(row({ profile: 'generated', provenance: { source: 'atlite', weather_year: 2013 } })).text)
      .toBe('ERA5 · 2013')
    expect(readinessBadge(row({ profile: 'generated', stale_reason: 'bus_moved',
      provenance: { source: 'atlite', weather_year: 2013 } })).text)
      .toBe('ERA5 · 2013 · out of date: the bus moved')
  })
})

describe('MissingCoordinatesBanner', () => {
  it('names the generator and opens the bus coordinates in place in Guided', async () => {
    render(<MissingCoordinatesBanner rows={[row({}), row({ generator: 'g_nocoord', bus: 'b2', coord_source: 'missing', lon: null, lat: null })]} />)
    expect(screen.getByTestId('missing-coordinates-banner').textContent).toContain('g_nocoord')
    expect(screen.queryByText('g_wind')).toBeNull()
    await userEvent.click(screen.getByTestId('set-coordinates-g_nocoord'))
    expect(useUIStore.getState().fieldDrawer).toMatchObject({ component: 'Bus', entity: 'b2', field: 'x' })
  })

  it('renders nothing when every generator has a location', () => {
    render(<MissingCoordinatesBanner rows={[row({})]} />)
    expect(screen.queryByTestId('missing-coordinates-banner')).toBeNull()
  })
})
