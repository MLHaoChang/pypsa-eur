import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../../store/uiStore'
import type { ReadinessRow } from '../../api/profiles'
import { readyRows, notReadyReason } from './renewableGeneration'
import { MissingCoordinatesBanner, UnrecordedSourceBanner, readinessBadge } from './RenewableReadiness'

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

  it('sets the bus coordinates inline and refuses 0/0 or out-of-range values', async () => {
    const save = vi.fn().mockResolvedValue(undefined)
    render(<MissingCoordinatesBanner onSetCoordinates={save}
      rows={[row({ generator: 'g_nocoord', bus: 'b2', coord_source: 'missing', lon: null, lat: null })]} />)
    await userEvent.click(screen.getByTestId('set-coordinates-g_nocoord'))
    await userEvent.type(screen.getByTestId('coord-x-b2'), '0')
    await userEvent.type(screen.getByTestId('coord-y-b2'), '0')
    expect((screen.getByTestId('coord-save-b2') as HTMLButtonElement).disabled).toBe(true)
    await userEvent.clear(screen.getByTestId('coord-x-b2'))
    await userEvent.type(screen.getByTestId('coord-x-b2'), '8.5')
    await userEvent.clear(screen.getByTestId('coord-y-b2'))
    await userEvent.type(screen.getByTestId('coord-y-b2'), '95')
    expect((screen.getByTestId('coord-save-b2') as HTMLButtonElement).disabled).toBe(true)
    await userEvent.clear(screen.getByTestId('coord-y-b2'))
    await userEvent.type(screen.getByTestId('coord-y-b2'), '50.1')
    await userEvent.click(screen.getByTestId('coord-save-b2'))
    expect(save).toHaveBeenCalledWith('b2', 8.5, 50.1)
    expect(screen.queryByTestId('coord-form-b2')).toBeNull()
  })

  it('keeps the form and shows the refusal when the save fails', async () => {
    const save = vi.fn().mockRejectedValue(new Error('bus is locked'))
    render(<MissingCoordinatesBanner onSetCoordinates={save}
      rows={[row({ generator: 'g_nocoord', bus: 'b2', coord_source: 'missing', lon: null, lat: null })]} />)
    await userEvent.click(screen.getByTestId('set-coordinates-g_nocoord'))
    await userEvent.type(screen.getByTestId('coord-x-b2'), '8')
    await userEvent.type(screen.getByTestId('coord-y-b2'), '50')
    await userEvent.click(screen.getByTestId('coord-save-b2'))
    expect((await screen.findByTestId('coord-error-b2')).textContent).toContain('bus is locked')
  })

  it('renders nothing when every generator has a location', () => {
    render(<MissingCoordinatesBanner rows={[row({})]} />)
    expect(screen.queryByTestId('missing-coordinates-banner')).toBeNull()
  })
})

describe('unrecorded series (RP-18)', () => {
  const un = (g: string, digest?: string) => row({ generator: g, profile: 'unrecorded', digest })

  it('is badged as kept, never as the network’s own, even with no location', () => {
    expect(readinessBadge(un('a', 'd1')).text).toBe('Source not recorded · kept')
    expect(readinessBadge({ ...un('a', 'd1'), coord_source: 'missing', lon: null, lat: null }).text)
      .toBe('Source not recorded · kept')
  })

  it('is never ready to generate over, and the reason counts it', () => {
    expect(readyRows([un('a', 'd1')], true)).toEqual([])
    expect(notReadyReason([un('a', 'd1')])).toContain('no recorded source')
  })

  it('names each series with its technology, never a hash', () => {
    render(<UnrecordedSourceBanner rows={[un('a', 'd1'), { ...un('b', 'd2'), technology: 'solar' }]} onConfirm={vi.fn()} />)
    const text = screen.getByTestId('unrecorded-source-banner').textContent ?? ''
    expect(text).toContain('a· wind availability')
    expect(text).toContain('b· solar availability')
    expect(text).not.toContain('d1')
  })

  it('marks them with a verb, singular and plural, sending the digests', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const { unmount } = render(<UnrecordedSourceBanner rows={[un('a', 'd1')]} onConfirm={onConfirm} />)
    const one = screen.getByTestId('confirm-unrecorded')
    expect(one.textContent).toBe('Mark this one as the network’s own')
    await userEvent.click(one)
    expect(onConfirm).toHaveBeenCalledWith([{ component: 'a', digest: 'd1' }])
    unmount()
    render(<UnrecordedSourceBanner rows={[un('a', 'd1'), un('b', 'd2'), un('c', 'd3')]} onConfirm={onConfirm} />)
    expect(screen.getByTestId('confirm-unrecorded').textContent).toBe('Mark these 3 as the network’s own')
  })

  it('folds past five rows and keeps the button off until every row is shown', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const rows = Array.from({ length: 12 }, (_, i) => un(`g${i}`, `d${i}`))
    render(<UnrecordedSourceBanner rows={rows} onConfirm={onConfirm} />)
    const text = () => screen.getByTestId('unrecorded-source-banner').textContent ?? ''
    expect(text()).toContain('g4')
    expect(text()).not.toContain('g5·')
    expect(text()).toContain('(7 more) Show all')
    const btn = screen.getByTestId('confirm-unrecorded') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.getByTestId('confirm-unrecorded-why').textContent).toBe('Show all 12 first, then mark them.')
    await userEvent.click(screen.getByTestId('unrecorded-toggle'))
    for (let i = 0; i < 12; i++) expect(text()).toContain(`g${i}·`)
    expect(btn.disabled).toBe(false)
    await userEvent.click(btn)
    expect(onConfirm).toHaveBeenCalledWith(rows.map(r => ({ component: r.generator, digest: r.digest })))
  })

  it('says why the button is off when a series has not been read', () => {
    render(<UnrecordedSourceBanner rows={[un('a', 'd1'), un('b')]} onConfirm={vi.fn()} />)
    expect((screen.getByTestId('confirm-unrecorded') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('confirm-unrecorded-why').textContent)
      .toBe('Can’t mark them yet: some series have not been read.')
  })

  it('puts a stale refusal on the button line as a status, and clears it when the list re-reads', async () => {
    const stale = { response: { data: { detail: { error_kind: 'unrecorded_stale', message: 'x' } } } }
    const { rerender } = render(<UnrecordedSourceBanner rows={[un('a', 'd1')]} onConfirm={vi.fn().mockRejectedValue(stale)} />)
    await userEvent.click(screen.getByTestId('confirm-unrecorded'))
    const note = await screen.findByTestId('confirm-unrecorded-error')
    expect(note.getAttribute('role')).toBe('status')
    expect(note.textContent).toBe('The series changed since this list was shown: reload to see them.')
    rerender(<UnrecordedSourceBanner rows={[un('a', 'd9')]} onConfirm={vi.fn()} />)
    expect(screen.getByTestId('confirm-unrecorded-error').textContent).toBe('')
  })

  it('renders nothing without unrecorded rows', () => {
    const { container } = render(<UnrecordedSourceBanner rows={[row({})]} onConfirm={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })
})
