import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

it('shows the estimated-height hint only while height is blank', async () => {
  render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
  await open()
  expect(screen.getByText(/Height omitted: library height estimate/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Height (m, optional)'), { target: { value: '3' } })
  expect(screen.queryByText(/Height omitted: library height estimate/)).toBeNull()
  expect(screen.getByText(/Clearances and safety compliance require separate checks/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Height (m, optional)'), { target: { value: '' } })
  expect(screen.getByText(/Height omitted: library height estimate/)).toBeTruthy()
})

it('uses a colon to explain the library fallback', async () => {
  render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
  await open()
  expect(screen.getByText('Library estimate: detailed footprint not provided; actual dimensions may differ.')).toBeTruthy()
})

it('rounds displayed envelope area without exposing floating-point noise', async () => {
  render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
  await open()
  fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '8.9' } })
  fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '6.7' } })
  expect(screen.getByText('Envelope area: 59.6 m²')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '0.1' } })
  fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '0.1' } })
  expect(screen.getByText('Envelope area: <0.1 m²')).toBeTruthy()
})

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
  expect((await screen.findByRole('alert')).textContent).toBe('Width, depth and height must be positive numbers of metres, up to 100000 m.')
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

it('clears saved status and dims both actions when editing becomes read-only', async () => {
  render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
  await open()
  fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '12' } })
  fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '6' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save footprint' }))
  await screen.findByText('Saved to project')
  act(() => useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' }))
  expect(screen.queryByText('Saved to project')).toBeNull()
  for (const name of ['Save footprint', 'Use library estimate']) {
    const button = screen.getByRole('button', { name }) as HTMLButtonElement
    expect(button.matches(':disabled')).toBe(true)
    expect(button.classList.contains('disabled:opacity-50')).toBe(true)
  }
})

it('does not restore saved status when a pending save finishes after read-only starts', async () => {
  let finish!: (value: { saved: string; sites: number }) => void
  vi.mocked(sitesApi.putSites).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
  await open()
  fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '12' } })
  fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '6' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save footprint' }))
  await waitFor(() => expect(sitesApi.putSites).toHaveBeenCalled())
  act(() => useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' }))
  await act(async () => finish({ saved: 'P', sites: 0 }))
  expect(screen.queryByText('Saved to project')).toBeNull()
  expect(screen.queryByText(/Local draft/)).toBeNull()
})

for (const change of ['project', 'selection'] as const) {
  it(`clears saved status when the ${change} changes`, async () => {
    const { rerender } = render(<AssetFootprintEditor componentClass="StorageUnit" name="BESS" />)
    await open()
    fireEvent.change(screen.getByLabelText('Width (m)'), { target: { value: '12' } })
    fireEvent.change(screen.getByLabelText('Depth (m)'), { target: { value: '6' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save footprint' }))
    await screen.findByText('Saved to project')
    if (change === 'project') act(() => useUIStore.setState({ currentProject: 'Q' }))
    else rerender(<AssetFootprintEditor componentClass="StorageUnit" name="Other" />)
    expect(screen.queryByText('Saved to project')).toBeNull()
    expect((screen.getByLabelText('Width (m)') as HTMLInputElement).value).toBe('')
  })
}

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
  expect(await screen.findByText('Local draft: not saved to project')).toBeTruthy()
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
