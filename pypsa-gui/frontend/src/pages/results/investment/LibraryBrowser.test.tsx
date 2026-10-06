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
      .mockRejectedValueOnce(Object.assign(new Error('422'), { response: { status: 422, data: { detail: {
        code: 'urdb_refused', message: 'refused', refusals: [{ field: 'mincharge', reason: 'not supported' }] } } } }))
      .mockResolvedValueOnce({ ref: { ...REF, id: 'imp', version: 1 }, notes: ['n1'], refusals: [],
                               unsupported_fields: ['mincharge'] })
    renderBrowser()
    await screen.findByTestId('library-browser')
    fireEvent.change(screen.getByLabelText('URDB or OpenEI JSON file'), { target: { files: [
      file('rates.json', JSON.stringify({ items: [{ name: 'Old', startdate: 1672531200 },
                                                  { name: 'New', startdate: 1704067200 }] }))] } })
    const rate = await screen.findByLabelText('Rate') as HTMLSelectElement
    expect([...rate.options].map(o => o.textContent)).toEqual(
      ['choose…', 'Old (from 2023-01-01)', 'New (from 2024-01-01)'])
    fireEvent.change(rate, { target: { value: '0' } })
    // The choice stays visible and changeable after a pick (review #2).
    const again = await screen.findByLabelText('Rate') as HTMLSelectElement
    expect(again.value).toBe('0')
    fireEvent.change(again, { target: { value: '1' } })
    await waitFor(() => expect((screen.getByLabelText('Rate') as HTMLSelectElement).value).toBe('1'))
    fireEvent.change(await screen.findByLabelText('Library name'), { target: { value: 'imp' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    const refusals = await screen.findByTestId('lib-urdb-refusals')
    expect(refusals.textContent).toContain('mincharge: not supported')
    expect(lib.importUrdb.mock.calls[0][0]).toMatchObject({ urdb_response: { name: 'New' },
      accept_partial: false, name: 'imp' })
    // Another rate is another import: the refusals shown were for 'New' (R2-1).
    fireEvent.change(screen.getByLabelText('Rate'), { target: { value: '0' } })
    await waitFor(() => expect(screen.queryByTestId('lib-urdb-refusals')).toBeNull())
    fireEvent.change(screen.getByLabelText('Rate'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(lib.importUrdb).toHaveBeenCalledTimes(2))
    fireEvent.click(within(await screen.findByTestId('lib-urdb-refusals'))
      .getByRole('button', { name: /Import without them/ }))
    expect((await screen.findByTestId('lib-urdb-done')).textContent).toContain('not imported: mincharge')
    expect(lib.importUrdb.mock.calls[1][0]).toMatchObject({ urdb_response: { name: 'New' }, accept_partial: false })
    expect(lib.importUrdb.mock.calls[2][0]).toMatchObject({ urdb_response: { name: 'New' }, accept_partial: true })
  })

  it('attaches without asking on a switch between Library tariffs (the inline copy is the ref\'s)', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import', import_tariff: TARIFF,
      import_tariff_ref: { ...REF, id: 'de', hash: 'hd' } } as never)
    api.saveCommercial.mockResolvedValue({} as never)
    renderBrowser()
    fireEvent.click(await screen.findByRole('button', { name: 'nl' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Attach v2 as the import tariff' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says so when the project cannot be read before attaching (review #4)', async () => {
    renderBrowser()
    fireEvent.click(await screen.findByRole('button', { name: 'nl' }))
    const attach = await screen.findByRole('button', { name: 'Attach v2 as the import tariff' })
    api.getCommercial.mockRejectedValue(Object.assign(new Error('500'), { response: { status: 500,
      data: { detail: 'the project is locked' } } }))
    fireEvent.click(attach)
    await waitFor(() => expect(screen.getByRole('status').textContent)
      .toContain('could not be attached: the project is locked'))
    expect(api.saveCommercial).not.toHaveBeenCalled()
  })

  it('shows a FastAPI validation list as its messages (review #5)', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import' } as never)
    api.saveCommercial.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: [{ loc: ['body', 'import_tariff_ref'], msg: 'unknown version' }] } } }))
    renderBrowser()
    fireEvent.click(await screen.findByRole('button', { name: 'nl' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Attach v2 as the import tariff' }))
    await waitFor(() => expect(screen.getByRole('status').textContent)
      .toContain('import_tariff_ref: unknown version'))
  })

  it('shows a contract with its summary and the versions its contracts pin (review #3)', async () => {
    const CREF = { kind: 'contract' as const, id: 'ppa', version: 3, hash: 'c3' }
    lib.listItems.mockImplementation(async k => (k === 'contract' ? [CREF] : [REF]))
    lib.getItem.mockImplementation(async (_k, name, version) => ({
      ref: { ...CREF, id: name, version: version ?? 3 },
      payload: { type: 'ppa', id: 'ppa', kind: 'baseload', seller: 'dev', buyer: 'site', price: 55,
                 tenor_years: 10 } as never, meta: {} }))
    api.getCommercial.mockResolvedValue({ poc_link: 'import', contracts: [
      { type: 'ppa', id: 'p1', library_ref: { ...CREF, version: 2 } }] } as never)
    renderBrowser()
    await screen.findByTestId('library-browser')
    fireEvent.click(screen.getByLabelText('Contracts'))
    fireEvent.click(await screen.findByRole('button', { name: 'ppa' }))
    const view = await screen.findByTestId('lib-item-view')
    expect((await within(view).findByTestId('lib-pinned')).textContent)
      .toContain('v2 (a newer version exists)')
    expect((await within(view).findByTestId('lib-item-summary')).textContent)
      .toBe('PPA (baseload): seller dev, buyer site, price 55; 10 years')
    expect(within(view).queryByRole('button', { name: /Attach/ })).toBeNull()
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
