// The Library browser (IC P3 WP3.7a): items with versions and pins, the item
// view, attaching a tariff (a confirm dialog before a hand-made inline tariff is
// replaced), the URDB import with its refusals and rate choice, and series /
// meter uploads with the meter conflict shown.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { commercialApi, libraryApi } from '../../../api/commercial'
import LibraryBrowser from './LibraryBrowser'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return { ...actual,
    commercialApi: { ...actual.commercialApi, getCommercial: vi.fn(), saveCommercial: vi.fn() },
    libraryApi: { ...actual.libraryApi, listItems: vi.fn(), getItem: vi.fn(), importUrdb: vi.fn(),
                  listSeries: vi.fn(), uploadSeries: vi.fn(), uploadMeterData: vi.fn() } }
})
const api = vi.mocked(commercialApi)
const lib = vi.mocked(libraryApi)
const REF = { kind: 'tariff' as const, id: 'nl', version: 2, hash: 'h2' }
const TARIFF = { id: 'nl-tou', name: 'NL TOU', jurisdiction: 'NL', valid_from: '2030-01-01',
                 items: [{ id: 'energy', kind: 'energy', unit: 'per_kwh', periods: [{ name: 'all', rate: 0.2 }] }] }

function renderBrowser() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><LibraryBrowser /></QueryClientProvider>)
}
function file(name: string, body: string, type = 'application/json') {
  const f = new File([body], name, { type })
  Object.defineProperty(f, 'text', { value: () => Promise.resolve(body) })
  return f
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  lib.listItems.mockResolvedValue([REF])
  lib.getItem.mockImplementation(async (_k, name, version) => ({
    ref: { ...REF, id: name, version: version ?? 2 }, payload: TARIFF as never, meta: { source: 'urdb' } }))
  lib.listSeries.mockResolvedValue([{ id: 'da_prices', version: 1, hash: 'x', source: 'upload' }])
  api.getCommercial.mockResolvedValue({ poc_link: 'import', import_tariff_ref: { ...REF, version: 1 } } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('LibraryBrowser', () => {
  it('lists items and shows one with its versions and the project pin', async () => {
    const { container } = renderBrowser()
    fireEvent.click(await screen.findByRole('button', { name: 'nl' }))
    const view = await screen.findByTestId('lib-item-view')
    expect((await within(view).findByTestId('lib-pinned')).textContent).toContain('v1 (a newer version exists)')
    const versions = within(view).getByLabelText('Version of nl') as HTMLSelectElement
    expect([...versions.options].map(o => o.value)).toEqual(['2', '1'])
    await within(view).findByText(/NL TOU — NL, from 2030-01-01/)
    fireEvent.change(versions, { target: { value: '1' } })
    await waitFor(() => expect(lib.getItem).toHaveBeenCalledWith('tariff', 'nl', 1))
    expect(screen.getByText('da_prices v1 (upload)')).toBeTruthy()
    expectAllButtonsNamed(container)
  })

  it('attaches a tariff without asking when nothing hand-made is replaced', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import' } as never)
    api.saveCommercial.mockResolvedValue({} as never)
    renderBrowser()
    fireEvent.click(await screen.findByRole('button', { name: 'nl' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Attach v2 as the import tariff' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledWith(
      { import_tariff_ref: REF, import_tariff: null, import_tariff_id: null }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('asks before replacing an inline tariff, and cancel changes nothing', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import', import_tariff: { ...TARIFF, id: 'mine' } } as never)
    api.saveCommercial.mockResolvedValue({} as never)
    renderBrowser()
    fireEvent.click(await screen.findByRole('button', { name: 'nl' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Attach v2 as the import tariff' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(api.saveCommercial).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Attach v2 as the import tariff' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Replace it' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledTimes(1))
  })

  it('imports a URDB rate: a choice among several, refusals, then a partial import', async () => {
    lib.importUrdb
      .mockRejectedValueOnce(Object.assign(new Error('422'), { response: { status: 422, data: { detail: {
        code: 'urdb_refused', message: 'refused', refusals: [{ field: 'mincharge', reason: 'not supported' }] } } } }))
      .mockResolvedValueOnce({ ref: { ...REF, id: 'imp', version: 1 }, notes: ['n1'], refusals: [],
                               unsupported_fields: ['mincharge'] })
    renderBrowser()
    await screen.findByTestId('library-browser')
    fireEvent.change(screen.getByLabelText('URDB file'), { target: { files: [
      file('rates.json', JSON.stringify({ items: [{ name: 'Old', startdate: 1 }, { name: 'New', startdate: 2 }] }))] } })
    const rate = await screen.findByLabelText('Rate')
    fireEvent.change(rate, { target: { value: '1' } })
    fireEvent.change(await screen.findByLabelText('Tariff name in the Library'), { target: { value: 'imp' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    const refusals = await screen.findByTestId('lib-urdb-refusals')
    expect(refusals.textContent).toContain('mincharge: not supported')
    expect(lib.importUrdb.mock.calls[0][0]).toMatchObject({ urdb_response: { name: 'New' },
      accept_partial: false, name: 'imp' })
    fireEvent.click(within(refusals).getByRole('button', { name: /Import without them/ }))
    expect((await screen.findByTestId('lib-urdb-done')).textContent).toContain('not imported: mincharge')
    expect(lib.importUrdb.mock.calls[1][0]).toMatchObject({ accept_partial: true })
  })

  it('needs a meter unit and shows a meter conflict', async () => {
    lib.uploadMeterData.mockRejectedValue(Object.assign(new Error('409'), { response: { status: 409,
      data: { detail: { code: 'meter_meta_conflict', message: "series 'm' already holds this data" } } } }))
    renderBrowser()
    await screen.findByTestId('library-browser')
    fireEvent.change(screen.getByLabelText('Series kind'), { target: { value: 'meter' } })
    fireEvent.change(screen.getByLabelText('Series file'), { target: { files: [file('m.csv', 'a,b', 'text/csv')] } })
    fireEvent.change(screen.getByLabelText('Series name'), { target: { value: 'm' } })
    const upload = screen.getByRole('button', { name: 'Upload' })
    expect(upload.hasAttribute('disabled')).toBe(true)                     // no unit yet
    fireEvent.change(screen.getByLabelText('Meter unit'), { target: { value: 'kW' } })
    fireEvent.click(upload)
    expect((await screen.findByRole('status')).textContent).toContain('already stored under that name')
    expect(lib.uploadMeterData).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({
      name: 'm', unit: 'kW', settlement: '15min', label: 'start' }))
  })
})
