import { beforeEach, expect, it, vi } from 'vitest'
import { networkApi } from './network'
import client from './client'
import { sitesApi } from './sites'
import { useUIStore } from '../store/uiStore'
import { useSitesStore, flushPendingSitesToServer } from '../site3d/sitesStore'
vi.mock('./client', () => ({ default: { get: vi.fn(), delete: vi.fn() }, client: { get: vi.fn(), put: vi.fn() } }))
vi.mock('./sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'P', readOnly: false, readOnlyReason: 'writable' })
  useSitesStore.getState().replaceDocument('P', { version: 1, sites: [], asset_footprints: {
    'Generator:G': { width_m: 12, depth_m: 6 }, 'Store:T': { width_m: 4, depth_m: 2 },
  } })
  vi.mocked(sitesApi.getSites).mockResolvedValue({ version: 1, sites: [] })
  vi.mocked(client.delete).mockResolvedValue({ status: 204 })
  vi.mocked(sitesApi.putSites).mockResolvedValue({ saved: 'P', sites: 0 })
})

it('clears cached dimensions before a successful delete resolves, retaining unrelated dirty edits', async () => {
  await networkApi.deleteGenerator('G')
  expect(useSitesStore.getState().docFor('P').asset_footprints).toEqual({ 'Store:T': { width_m: 4, depth_m: 2 } })
  await flushPendingSitesToServer('P')
  expect(sitesApi.putSites).toHaveBeenLastCalledWith('P', { version: 1, sites: [], asset_footprints: { 'Store:T': { width_m: 4, depth_m: 2 } } })
})

it('does not remove dimensions after a rejected delete', async () => {
  vi.mocked(client.delete).mockRejectedValue(new Error('locked'))
  await expect(networkApi.deleteGenerator('G')).rejects.toThrow('locked')
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:G']).toEqual({ width_m: 12, depth_m: 6 })
})

it('a complete successful native list prunes missing targets in that class only', async () => {
  vi.mocked(client.get).mockResolvedValue({ data: [] })
  await networkApi.getGenerators()
  expect(useSitesStore.getState().docFor('P').asset_footprints).toEqual({ 'Store:T': { width_m: 4, depth_m: 2 } })
})

it('does not apply an old-project list response to current metadata', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(client.get).mockReturnValue(new Promise(r => { resolve = r }) as never)
  const pending = networkApi.getGenerators()
  useUIStore.setState({ currentProject: 'Q' })
  resolve({ data: [] })
  await pending
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:G']).toBeTruthy()
})

it('cascade delete clears cached envelopes of connected equipment, not other buses', async () => {
  vi.mocked(client.get).mockImplementation(async url => ({ data: String(url).endsWith('/generators') ? [{name:'G',bus:'B'}] : [{name:'T',bus:'OTHER'}] }) as never)
  await networkApi.deleteBusCascade('B')
  expect(useSitesStore.getState().docFor('P').asset_footprints).toEqual({ 'Store:T': { width_m: 4, depth_m: 2 } })
})

it('a failed complete list leaves cached metadata intact', async () => {
  vi.mocked(client.get).mockRejectedValue(new Error('offline'))
  await expect(networkApi.getGenerators()).rejects.toThrow('offline')
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:G']).toBeTruthy()
})

it('a late list cannot drop a footprint edited after that read began', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(client.get).mockReturnValue(new Promise(r => {resolve=r}) as never)
  const pending = networkApi.getGenerators()
  useSitesStore.getState().setAssetFootprint('P', 'Generator:G', {width_m: 20, depth_m: 10})
  resolve({data: []})
  await pending
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:G']).toEqual({width_m:20, depth_m:10})
})


it('imports the exact native renamed footprint before unrelated dirty state is flushed', async () => {
  vi.mocked(client.get).mockResolvedValue({data: [{name: 'renamed'}]})
  vi.mocked(sitesApi.getSites).mockResolvedValue({version: 1, sites: [], asset_footprints: {
    'Generator:renamed': {width_m: 12, depth_m: 6}, 'Store:T': {width_m: 4, depth_m: 2},
  }})
  await networkApi.getGenerators()
  expect(useSitesStore.getState().docFor('P').asset_footprints).toEqual({
    'Generator:renamed': {width_m: 12, depth_m: 6}, 'Store:T': {width_m: 4, depth_m: 2},
  })
  await flushPendingSitesToServer('P')
  expect(sitesApi.putSites).toHaveBeenLastCalledWith('P', useSitesStore.getState().docFor('P'))
})

it('native rename preserves an unrelated same-class pending footprint', async () => {
  useSitesStore.getState().setAssetFootprint('P', 'Generator:H', {width_m: 20, depth_m: 10})
  vi.mocked(client.get).mockResolvedValue({data: [{name:'renamed'}, {name:'H'}]})
  vi.mocked(sitesApi.getSites).mockResolvedValue({version:1,sites:[],asset_footprints:{
    'Generator:renamed':{width_m:12,depth_m:6}, 'Generator:H':{width_m:4,depth_m:2},
  }})
  await networkApi.getGenerators()
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:H']).toEqual({width_m:20,depth_m:10})
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:renamed']).toEqual({width_m:12,depth_m:6})
  await flushPendingSitesToServer('P')
  expect(sitesApi.putSites).toHaveBeenLastCalledWith('P', useSitesStore.getState().docFor('P'))
})

it('native rename does not undo an unrelated pending reset to the library estimate', async () => {
  useSitesStore.getState().setAssetFootprint('P', 'Generator:H', {width_m:20,depth_m:10})
  useSitesStore.getState().setAssetFootprint('P', 'Generator:H', null)
  vi.mocked(client.get).mockResolvedValue({data:[{name:'renamed'},{name:'H'}]})
  vi.mocked(sitesApi.getSites).mockResolvedValue({version:1,sites:[],asset_footprints:{
    'Generator:renamed':{width_m:12,depth_m:6}, 'Generator:H':{width_m:4,depth_m:2},
  }})
  await networkApi.getGenerators()
  expect(useSitesStore.getState().docFor('P').asset_footprints!['Generator:H']).toBeUndefined()
  await flushPendingSitesToServer('P')
  expect(sitesApi.putSites).toHaveBeenLastCalledWith('P', useSitesStore.getState().docFor('P'))
})
