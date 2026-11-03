// SC-02d: group editing and detach in the Model part (UX (6) conditions 1–7).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DetachPreview, ModelProposal, SiteScene } from '../api/siteScene'

vi.mock('./sceneActions', async (orig) => ({
  ...(await orig<typeof import('./sceneActions')>()),
  propose: vi.fn(), applyProposal: vi.fn(), discardProposal: vi.fn(),
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getLoads: vi.fn(), getLinks: vi.fn(), getStores: vi.fn(), getGenerators: vi.fn(), getStorageUnits: vi.fn(), getCarriers: vi.fn(),
  },
}))

import { networkApi } from '../api/network'
import { applyProposal, propose } from './sceneActions'
import { useUIStore } from '../store/uiStore'
import { useModelProposal } from './useSiteScene'
import AssociationPanel from './AssociationPanel'
import { UNDO_GONE } from './groupEdit'
import type { Site } from './types'

const site: Site = { id: 'campus', name: 'Campus', buses: [], origin: { lng: 11, lat: 48 }, placements: {},
  boundary: [[10.999, 47.999], [11.001, 47.999], [11.001, 48.001], [10.999, 48.001]] }
const BESS = 'StorageUnit:bess', PV = 'Generator:pv'
type Enabled = Extract<SiteScene, { state: 'enabled' }>
const scene = (): Enabled => ({ state: 'enabled', site_id: 'campus', revision: 3, generation: 'g',
  site: { placements: { [BESS]: { x: 0, y: 0, heading: 0, on: 'ground' }, [PV]: { x: 10, y: 0, heading: 90, on: 'ground' } } },
  buildings: [], asset_footprints: {}, last_receipt: null, unbound_keys: [], associations: [], targets: [],
  groups: [{ id: 'yard', label: 'Yard', members: [BESS] }] })

const view = (canWrite = true) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AssociationPanel site={site} scene={scene()} canWrite={canWrite} blockedReason={canWrite ? null : 'Read-only: ask an editor'} />
  </QueryClientProvider>,
)
const refused = (status: number, detail: Record<string, unknown>) => {
  const headers = new AxiosHeaders()
  return new AxiosError('refused', 'ERR_BAD_REQUEST', { headers }, null,
    { status, statusText: '', headers: {}, config: { headers }, data: { detail } })
}
const btn = (id: string) => screen.getByTestId(id) as HTMLButtonElement
const tick = (key: string) => fireEvent.click(screen.getByTestId(`placed-pick-${key}`))
const proposal = (action: 'detach' | 'undo_detach', preview: DetachPreview) =>
  ({ id: action === 'detach' ? 'p-d' : 'p-u', site_id: 'campus', action, base_revision: 3, input_hash: 'h', status: 'ready', preview }) as ModelProposal
const detachPreview: DetachPreview = { findings: [], items: [
  { key: BESS, label: 'bess · storage unit', kept_in_model: true, groups: [{ id: 'yard', label: 'Yard', left_empty: true }],
    associations: [{ id: 'a1', class: 'Store', label: 'tank · store' }] },
  { key: PV, label: 'pv · generator', kept_in_model: true, groups: [], associations: [] }] }

beforeEach(() => {
  vi.clearAllMocks()
  useModelProposal.setState({ proposal: null, addAssetFrom: null, placementRebind: null, picked: null, lastDetach: null })
  useUIStore.setState({ currentProject: 'p', sitePart: 'model', sitePartSelection: null, readOnly: false, readOnlyReason: 'writable',
    uiMode: 'guided', flowReturn: null, activeSection: null, creationItem: null, activeSlidePanel: null })
  vi.mocked(networkApi.getLoads).mockResolvedValue([] as never)
  vi.mocked(networkApi.getLinks).mockResolvedValue([] as never)
  vi.mocked(networkApi.getStores).mockResolvedValue([] as never)
  vi.mocked(networkApi.getGenerators).mockResolvedValue([{ name: 'pv', bus: 'e', carrier: 'solar' }] as never)
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ name: 'bess', bus: 'e', carrier: 'battery' }] as never)
  vi.mocked(networkApi.getCarriers).mockResolvedValue([] as never)
})

describe('Placed equipment', () => {
  it('lists the placed items by name, never by key, with a hint until one is picked', () => {
    view()
    const list = screen.getByTestId('placed-list')
    expect(list.textContent).not.toMatch(/StorageUnit:|Generator:/)
    expect(list.textContent).toMatch(/bess/)
    expect(screen.getByTestId('placed-hint')).toBeTruthy()
  })

  it('edits two items as one: "Edit 2 items", axis words reused, one place_existing preview', async () => {
    vi.mocked(propose).mockResolvedValue({ id: 'p-m', site_id: 'campus', action: 'place_existing', base_revision: 3,
      input_hash: 'h', status: 'ready', preview: { placements: {}, findings: [] } } as never)
    view()
    tick(BESS); tick(PV)
    expect(screen.getByTestId('placed-count').textContent).toBe('2 selected')
    expect(screen.getByTestId('group-edit-title').textContent).toBe('Edit 2 items')
    expect(screen.getByText('Move east')).toBeTruthy()
    expect(screen.getByText('Move north')).toBeTruthy()
    expect(screen.getByText('Rotate by')).toBeTruthy()
    expect(screen.getByTestId('group-rotate-hint').textContent).toMatch(/about their centre/)
    expect(btn('group-preview').disabled).toBe(true)
    expect(screen.getByTestId('group-preview-reason').textContent).toBe('Enter a distance or an angle first')
    fireEvent.change(screen.getByTestId('group-east'), { target: { value: '5' } })
    fireEvent.click(btn('group-preview'))
    await waitFor(() => expect(propose).toHaveBeenCalledTimes(1))
    expect(vi.mocked(propose).mock.calls[0].slice(3)).toEqual(['place_existing', { placements: {
      [BESS]: { x: 5, y: 0, heading: 0, on: 'ground' }, [PV]: { x: 15, y: 0, heading: 90, on: 'ground' } } }])
  })

  it('with one item picked, its placement comes back in the usual fields; group values do not carry over', async () => {
    view()
    tick(BESS); tick(PV)
    fireEvent.change(screen.getByTestId('group-east'), { target: { value: '7' } })
    tick(BESS)
    expect(screen.queryByTestId('group-edit')).toBeNull()
    expect(((await screen.findByTestId('place-x')) as HTMLInputElement).value).toBe('10')
    expect((screen.getByTestId('place-heading') as HTMLInputElement).value).toBe('90')
    tick(BESS)
    expect((screen.getByTestId('group-east') as HTMLInputElement).value).toBe('0')
  })

  it('Esc clears the picks only while the list has focus, and lets other Esc handlers run', () => {
    const outer = vi.fn()
    window.addEventListener('keydown', outer)
    view()
    tick(BESS)
    fireEvent.keyDown(document.body, { key: 'Escape' })                 // focus elsewhere: picks stay
    expect(screen.getByTestId('placed-count').textContent).toBe('1 selected')
    fireEvent.keyDown(screen.getByTestId(`placed-pick-${BESS}`), { key: 'Escape' })
    expect(screen.queryByTestId('placed-count')).toBeNull()
    expect(outer).toHaveBeenCalledTimes(2)                               // never swallowed
    window.removeEventListener('keydown', outer)
  })

  it('Clear keeps the count in sync with the store the canvas reads', () => {
    view()
    tick(BESS); tick(PV)
    expect(useModelProposal.getState().picked).toEqual({ siteId: 'campus', keys: [BESS, PV] })
    fireEvent.click(screen.getByTestId('placed-clear'))
    expect(useModelProposal.getState().picked).toBeNull()
  })

  it('read-only: Detach is disabled with its reason', () => {
    view(false)
    tick(BESS)
    expect(btn('detach-preview').disabled).toBe(true)
    expect(screen.getByTestId('detach-preview-reason').textContent).toBe('Read-only: ask an editor')
  })

  it('a refused group move names the item', async () => {
    vi.mocked(propose).mockRejectedValue(refused(422, { error_kind: 'invalid_input', code: 'off_roof', key: PV, message: 'x' }))
    view()
    tick(BESS); tick(PV)
    fireEvent.change(screen.getByTestId('group-north'), { target: { value: '1' } })
    fireEvent.click(btn('group-preview'))
    await waitFor(() => expect(screen.getByTestId('assoc-error').textContent).toMatch(/^pv/i))
    expect(screen.getByTestId('assoc-error').textContent).not.toMatch(/Generator:/)
  })
})

describe('Detach and undo', () => {
  it('previews a detach by name, applies it, and offers Undo', async () => {
    vi.mocked(propose).mockResolvedValue(proposal('detach', detachPreview) as never)
    vi.mocked(applyProposal).mockResolvedValue(undefined)
    view()
    tick(BESS); tick(PV)
    fireEvent.click(btn('detach-preview'))
    await waitFor(() => expect(screen.getByTestId('proposal-title').textContent)
      .toBe('Detach 2 items from the scene. They stay in the model.'))
    expect(vi.mocked(propose).mock.calls[0].slice(3)).toEqual(['detach', { keys: [BESS, PV] }])
    const card = screen.getByTestId('assoc-proposal').textContent ?? ''
    expect(card).toMatch(/Leaves group Yard/)
    expect(card).toMatch(/Group Yard will be empty \(kept\)\./)
    expect(card).toMatch(/Its attachment to tank goes/)
    expect(card).not.toMatch(/StorageUnit:|Generator:/)
    fireEvent.click(btn('assoc-apply'))
    await waitFor(() => expect(screen.getByTestId('detach-receipt').textContent).toMatch(/^Detached 2 items/))
    expect(btn('detach-undo').tagName).toBe('BUTTON')
    expect(btn('detach-undo').disabled).toBe(false)
    expect(useModelProposal.getState().picked).toBeNull()
  })

  it('Undo opens the undo card; a group deleted since is a warning line by name', async () => {
    useModelProposal.setState({ lastDetach: { siteId: 'campus', proposalId: 'p-d', count: 1, blocked: null } })
    vi.mocked(propose).mockResolvedValue(proposal('undo_detach', { items: [{ key: BESS, label: 'bess · storage unit', groups: [], associations: [] }],
      findings: [{ code: 'membership_not_restored', severity: 'warn', objects: [BESS, 'yard'], group: 'Yard', message: 'x' }] }) as never)
    view()
    fireEvent.click(btn('detach-undo'))
    await waitFor(() => expect(screen.getByTestId('proposal-title').textContent).toBe('Undo detach: restore 1 item'))
    expect(vi.mocked(propose).mock.calls[0].slice(3)).toEqual(['undo_detach', { proposal: 'p-d' }])
    expect(screen.getByTestId('assoc-finding-0').textContent).toMatch(/bess .*not put back in group Yard: the group was deleted/)
  })

  it('when the server can no longer undo it, Undo stays visible, disabled, with the reason', async () => {
    useModelProposal.setState({ lastDetach: { siteId: 'campus', proposalId: 'p-d', count: 2, blocked: null } })
    vi.mocked(propose).mockRejectedValue(refused(404, { error_kind: 'not_found', code: 'detach_not_found', message: UNDO_GONE }))
    view()
    fireEvent.click(btn('detach-undo'))
    await waitFor(() => expect(btn('detach-undo').disabled).toBe(true))
    expect(screen.getByTestId('detach-undo-reason').textContent).toBe(UNDO_GONE)
  })

  it('an undo refused because the item was placed again names it', async () => {
    useModelProposal.setState({ lastDetach: { siteId: 'campus', proposalId: 'p-d', count: 1, blocked: null } })
    vi.mocked(propose).mockRejectedValue(refused(422, { error_kind: 'invalid_input', code: 'already_placed', key: BESS, message: 'x' }))
    view()
    fireEvent.click(btn('detach-undo'))
    await waitFor(() => expect(screen.getByTestId('assoc-error').textContent)
      .toMatch(/bess.*: it was placed again since the detach, so it can’t be restored/))
    expect(btn('detach-undo').disabled).toBe(true)                      // it stays true: Undo is disabled with that reason
    expect(screen.getByTestId('detach-undo-reason').textContent).toMatch(/placed again since the detach/)
  })
})
