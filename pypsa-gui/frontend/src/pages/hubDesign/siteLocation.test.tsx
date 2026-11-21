// P5: the create forms ask for the site location and write it as the site anchor.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/network', () => ({
  networkApi: { getBuses: vi.fn(), bulkUpdate: vi.fn() },
}))
vi.mock('../../api/siteScene', () => ({
  siteSceneApi: { getAnchor: vi.fn(), putAnchor: vi.fn() },
}))
import { siteSceneApi } from '../../api/siteScene'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { validateLocation, writeCreatedLocation } from './siteLocation'
import { DcLoadForm } from './cards/DcLoadForm'

beforeEach(() => {
  useUIStore.setState({ currentProject: 'p1', projectMismatch: null })
  vi.mocked(siteSceneApi.getAnchor).mockResolvedValue({ revision: 3, anchor: null } as never)
  vi.mocked(siteSceneApi.putAnchor).mockResolvedValue({} as never)
  vi.mocked(networkApi.getBuses).mockResolvedValue([{ name: 'b1' }, { name: 'b2' }] as never)
  vi.mocked(networkApi.bulkUpdate).mockResolvedValue({} as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('validateLocation', () => {
  it('accepts a blank pair, a valid pair; names the missing half and bad ranges', () => {
    expect(validateLocation({ lat: '', lng: '' })).toEqual({})
    expect(validateLocation({ lat: '47.4', lng: '8.5' })).toEqual({})
    expect(validateLocation({ lat: '47.4', lng: '' }).lng).toMatch(/Longitude/)
    expect(validateLocation({ lat: '95', lng: '8' }).lat).toMatch(/Latitude/)
    expect(validateLocation({ lat: '47', lng: '200' }).lng).toMatch(/Longitude/)
  })
})

describe('writeCreatedLocation', () => {
  it('writes a confirmed manual anchor on the current revision', async () => {
    expect(await writeCreatedLocation('p1', { lat: '47.4', lng: '8.5' })).toBeNull()
    expect(siteSceneApi.putAnchor).toHaveBeenCalledWith('p1',
      { lat: 47.4, lng: 8.5, source: 'manual', confirmed: true }, 3)
    expect(networkApi.bulkUpdate).toHaveBeenCalledWith({ component_class: 'Bus', names: ['b1', 'b2'],
      updates: { x: 8.5, y: 47.4 } })
  })
  it('waits for a slow project open instead of failing', async () => {
    useUIStore.setState({ currentProject: 'other' })
    const w = writeCreatedLocation('p1', { lat: '47.4', lng: '8.5' })
    await new Promise(r => setTimeout(r, 30))
    expect(networkApi.bulkUpdate).not.toHaveBeenCalled()
    useUIStore.setState({ currentProject: 'p1' })
    expect(await w).toBeNull()
    expect(networkApi.bulkUpdate).toHaveBeenCalled()
  })
  it('does not touch the live network when another project stays open', async () => {
    vi.useFakeTimers()
    useUIStore.setState({ currentProject: 'other' })
    const w = writeCreatedLocation('p1', { lat: '47.4', lng: '8.5' })
    await vi.advanceTimersByTimeAsync(20001)
    vi.useRealTimers()
    expect(await w).toMatch(/not saved/)
    expect(networkApi.getBuses).not.toHaveBeenCalled()
    expect(networkApi.bulkUpdate).not.toHaveBeenCalled()
  })
  it('writes nothing for a skipped pair', async () => {
    expect(await writeCreatedLocation('p1', { lat: '', lng: '' })).toBeNull()
    expect(siteSceneApi.putAnchor).not.toHaveBeenCalled()
  })
  it('reports a failed write', async () => {
    vi.mocked(siteSceneApi.putAnchor).mockRejectedValue(new Error('x'))
    expect(await writeCreatedLocation('p1', { lat: '1', lng: '2' })).toMatch(/not saved/)
  })
})

describe('DcLoadForm location', () => {
  const mount = () => {
    const create = { mutate: vi.fn(), isPending: false }
    render(<DcLoadForm templateId="eh_datacenter" templateName="Data Center" create={create}
      disabledReason={null} onCancel={() => {}} />)
    return create
  }
  it('is optional: Create works with it blank', () => {
    mount()
    expect((screen.getByTestId('dc-form-create') as HTMLButtonElement).disabled).toBe(false)
  })
  it('blocks Create on a bad value and saves a good one after the project exists', async () => {
    const create = mount()
    await userEvent.type(screen.getByLabelText('Latitude (°)'), '95')
    await userEvent.type(screen.getByLabelText('Longitude (°)'), '8.5')
    expect((screen.getByTestId('dc-form-create') as HTMLButtonElement).disabled).toBe(true)
    await userEvent.clear(screen.getByLabelText('Latitude (°)'))
    await userEvent.type(screen.getByLabelText('Latitude (°)'), '47.4')
    await userEvent.click(screen.getByTestId('dc-form-create'))
    create.mutate.mock.calls[0][0].afterCreate('dc1')
    await waitFor(() => expect(siteSceneApi.putAnchor).toHaveBeenCalledWith('dc1',
      { lat: 47.4, lng: 8.5, source: 'manual', confirmed: true }, 3))
  })
})
