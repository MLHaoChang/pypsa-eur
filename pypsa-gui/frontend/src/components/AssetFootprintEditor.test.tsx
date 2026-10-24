import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AssetFootprintEditor from './AssetFootprintEditor'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { sitesApi } from '../api/sites'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn() } }))
beforeEach(() => {
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'P', readOnly: false, readOnlyReason: 'writable' })
  vi.mocked(sitesApi.getSites).mockReset().mockResolvedValue({ version: 1, sites: [] })
  vi.mocked(sitesApi.putSites).mockReset().mockResolvedValue({ saved: 'P', sites: 0 })
})

async function open() {
  fireEvent.click(screen.getByText('Footprint (optional)'))
  await screen.findByLabelText('Width (m)')
  await waitFor(() => expect((screen.getByRole('button', { name: 'Save footprint' }) as HTMLButtonElement).matches(':disabled')).toBe(false))
}

for (const uiMode of ['guided', 'expert'] as const) {
  it(`saves and resets the same optional footprint in ${uiMode} mode`, async () => {
    useUIStore.setState({ uiMode })
    render(<AssetFootprintEditor componentClass="Generator" name="G" />)
    expect(sitesApi.getSites).not.toHaveBeenCalled()
    await open()
    fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '12' } })
    fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '6' } })
    expect(screen.getByText('Envelope area: 72 m²')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Save footprint' }))
    await screen.findByText('Saved to project')
    expect(sitesApi.putSites).toHaveBeenLastCalledWith('P', { version: 1, sites: [], asset_footprints: {
      'Generator:G': { width_m: 12, depth_m: 6 },
    } })
    fireEvent.click(screen.getByRole('button', { name: 'Use library estimate' }))
    await waitFor(() => expect(useSitesStore.getState().docFor('P').asset_footprints).toEqual({}))
  })
}

it('rejects incomplete dimensions and never sends a network-model edit', async () => {
  render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
  await open()
  fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '12' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save footprint' }))
  expect((await screen.findByRole('alert')).textContent).toContain('positive finite metres')
  expect(sitesApi.putSites).not.toHaveBeenCalled()
})

it('retains viewing but refuses edits while read-only', async () => {
  useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
  render(<AssetFootprintEditor componentClass="Transformer" name="T" />)
  fireEvent.click(screen.getByText('Footprint (optional)'))
  expect((await screen.findByLabelText('Width (m)') as HTMLInputElement).disabled).toBe(false)
  expect((screen.getByLabelText('Width (m)') as HTMLInputElement).closest('fieldset')!.disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Save footprint' }) as HTMLButtonElement).matches(':disabled')).toBe(true)
})

it('does not offer a rectangle for a line', () => {
  render(<AssetFootprintEditor componentClass="Line" name="L" />)
  expect(screen.queryByText('Footprint (optional)')).toBeNull()
})

it('does not claim server persistence when the PUT fails', async () => {
  vi.mocked(sitesApi.putSites).mockRejectedValue(new Error('offline'))
  render(<AssetFootprintEditor componentClass="Store" name="Tank" />)
  await open()
  fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '4' } })
  fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save footprint' }))
  expect((await screen.findByText(/Local draft/)).textContent).toContain('Local draft')
  expect(screen.queryByText('Saved to project')).toBeNull()
})

it('blocks editing after a failed metadata load', async () => {
  vi.mocked(sitesApi.getSites).mockRejectedValue(new Error('unavailable'))
  render(<AssetFootprintEditor componentClass="Generator" name="G" />)
  fireEvent.click(screen.getByText('Footprint (optional)'))
  await screen.findByText(/Could not load site metadata/)
  expect((screen.getByRole('button', { name: 'Save footprint' }) as HTMLButtonElement).matches(':disabled')).toBe(true)
  expect(sitesApi.putSites).not.toHaveBeenCalled()
})
