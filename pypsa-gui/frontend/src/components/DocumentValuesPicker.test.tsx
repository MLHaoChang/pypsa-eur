// "Import values from a file…" with a PDF (data-to-model slice 2, AI-567b): the
// quote picker. An explicit asset choice, one row per field with its candidate
// lines grouped by page, nothing pre-ticked but a lone candidate, the same card
// and receipt; section 7's commercial mode ends on its block, and a
// library-pinned item links to the Commercial library.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EquipmentTableImport from './EquipmentTableImport'
import { useUIStore } from '../store/uiStore'

const previewAssets = vi.fn()
const applyAssets = vi.fn()
const previewCommercial = vi.fn()
const applyCommercial = vi.fn()
const openSection = vi.fn()
const openTarget = vi.fn()

vi.mock('../api/documentValues', () => ({
  documentValuesApi: {
    previewAssets: (b: unknown) => previewAssets(b), applyAssets: (b: unknown) => applyAssets(b),
    previewCommercial: (b: unknown) => previewCommercial(b), applyCommercial: (b: unknown) => applyCommercial(b),
  },
}))
vi.mock('../api/equipmentTable', () => ({ equipmentTableApi: { preview: vi.fn(), apply: vi.fn() } }))
vi.mock('../api/uploads', () => ({
  uploadFile: vi.fn(),
  listUploads: vi.fn(async () => [
    { file_id: 'u1', filename: 'lines.csv', kind: 'user_upload' },
    { file_id: 'p1', filename: 'datasheet.pdf', kind: 'user_upload' },
  ]),
}))
vi.mock('../layout/flowNavigation', () => ({
  openSectionInExpert: (id: string) => openSection(id),
  openFlowTarget: (t: unknown) => openTarget(t),
}))

const SOURCE = { upload_id: 'p1', filename: 'datasheet.pdf', sha256: 'b'.repeat(64), pages: 2, truncated: [] }
const FIELDS = [{ field: 'p_nom', label: 'Capacity', units: ['MW'], currency: null },
  { field: 'efficiency', label: 'Efficiency', units: ['fraction', '%'], currency: null },
  { field: 'overnight_cost', label: 'Overnight cost', units: ['EUR/MW', 'EUR/kW'], currency: 'EUR' },
  { field: 'p_set', label: 'Active power set point', units: ['MW'], currency: null }]
const CANDIDATES = {
  p_nom: { more: 0, candidates: [
    { page: 2, quote: 'Capacity 1.5 MW', value_text: null, value: '1.5', unit: 'MW', label: true },
    { page: 1, quote: 'Nominal power 1.2 MW', value_text: null, value: '1.2', unit: 'MW', label: false }] },
  efficiency: { more: 0, candidates: [
    { page: 1, quote: 'Efficiency 92.5 % at 1.5 MW', value_text: '92.5', value: '92.5', unit: '%', label: true }] },
  overnight_cost: { more: 0, candidates: [] },
  p_set: { more: 0, candidates: [
    { page: 1, quote: 'Nominal power 1.2 MW', value_text: null, value: '1.2', unit: 'MW', label: false }] },
}
const ROW = { component_class: 'Generator', name: 'PV1', field: 'efficiency', label: 'Efficiency', old: 0.9, new: 0.925,
  unit: 'fraction', role: 'typed' as const, source: { filename: 'datasheet.pdf', page: 1, quote: 'Efficiency 92.5 % at 1.5 MW' } }
const CARD = { version: 1, kind: 'parameter_edit', tool: 'apply_document_values', rows: [ROW], says: 'Set PV1 efficiency',
  complete: true, digest: 'd'.repeat(64), section: { id: 'assets', label: 'Assets' } }
const PLAN = { changed_cells: 1, unchanged_cells: 0, unmapped: [], unmapped_total: 0, unmapped_by_kind: {},
  needs: [], missing: [], notes: [], cells: [] }
const RECEIPT = { version: 1, digest: CARD.digest, outcome: 'verified', rows: [{ ...ROW, readback: 0.925 }], says: CARD.says,
  results_stale: false, undo: { tool: 'undo_last', available: true },
  section: { id: 'assets', label: 'Assets', state: 'complete', changed: false } }

beforeEach(() => {
  for (const f of [previewAssets, applyAssets, previewCommercial, applyCommercial, openSection, openTarget]) f.mockReset()
  useUIStore.setState({ currentProject: 'P' })
  previewAssets.mockImplementation(async (b: { citations?: unknown[] }) => b.citations
    ? { document: { source: SOURCE }, fields: FIELDS, plan: PLAN, card: CARD }
    : { document: { source: SOURCE }, fields: FIELDS, candidates: CANDIDATES })
})

const why = () => screen.getByTestId('document-values-why').textContent

describe('DocumentValuesPicker, assets', () => {
  it('needs an explicit asset, pre-ticks only a lone candidate and applies the previewed card', async () => {
    const onApplied = vi.fn(), onClose = vi.fn()
    applyAssets.mockResolvedValue({ receipt: RECEIPT })
    render(<EquipmentTableImport componentClass="Generator" assetsLabel="generators" assetNames={['PV1', 'PV2']}
      onClose={onClose} onApplied={onApplied} />)
    expect(screen.getAllByText('Import values from a file').length).toBeGreaterThan(0)
    const upload = await screen.findByTestId('table-import-upload')
    expect([...upload.querySelectorAll('option')].map(o => o.textContent)).toEqual(['Choose an upload…', 'lines.csv', 'datasheet.pdf'])
    await userEvent.selectOptions(upload, 'p1')
    await waitFor(() => expect(previewAssets).toHaveBeenCalledWith(
      { upload_id: 'p1', component_class: 'Generator', find: true, decimal_mark: null }))
    expect(await screen.findByTestId('document-values-source')).toBeTruthy()

    // Candidates grouped by page; two candidates: none ticked; one: ticked and shown.
    const pNom = screen.getByTestId('document-values-row-p_nom')
    expect(within(pNom).getAllByRole('radio').map(r => (r as HTMLInputElement).checked)).toEqual([false, false])
    expect(within(pNom).getByText('p. 2')).toBeTruthy()
    const eff = screen.getByTestId('document-values-row-efficiency')
    expect((within(eff).getByRole('radio') as HTMLInputElement).checked).toBe(true)
    // A field no line names is folded away and not ticked, even with one candidate.
    const more = screen.getByTestId('document-values-more')
    expect(more.textContent).toContain('1 more field could take a line of this document')
    expect((within(more).getByRole('radio') as HTMLInputElement).checked).toBe(false)
    // A field with no candidate is said, and does not block the others.
    expect(screen.getByTestId('document-values-empty').textContent).toContain('Overnight cost (overnight_cost)')

    expect(why()).toBe('Pick the assets these values are for.')
    expect(screen.getByTestId('document-values-preview-btn')).toHaveProperty('disabled', true)
    await userEvent.click(within(screen.getByTestId('document-values-assets')).getByLabelText('PV1'))
    expect(why()).toBe('Preview the changes first.')

    await userEvent.click(screen.getByTestId('document-values-preview-btn'))
    await waitFor(() => expect(previewAssets).toHaveBeenLastCalledWith({ upload_id: 'p1', component_class: 'Generator',
      citations: [{ names: ['PV1'], field: 'efficiency', page: 1, quote: 'Efficiency 92.5 % at 1.5 MW', unit: '%', value_text: '92.5' }],
      decimal_mark: null, currency: null, money_year: null, confirm: [] }))
    await userEvent.click(await screen.findByTestId('document-values-apply'))
    await waitFor(() => expect(applyAssets).toHaveBeenCalledWith(expect.objectContaining({
      source_sha256: SOURCE.sha256, digest: CARD.digest })))
    expect(onApplied).toHaveBeenCalled()
    expect(await screen.findByTestId('chat-edit-receipt')).toBeTruthy()
    await userEvent.click(screen.getByTestId('document-values-open-section'))
    expect(openSection).toHaveBeenCalledWith('assets')
  })

  it('asks for the currency and money year once a cost is ticked', async () => {
    previewAssets.mockResolvedValue({ document: { source: SOURCE }, fields: FIELDS, candidates: { ...CANDIDATES,
      overnight_cost: { more: 0, candidates: [
        { page: 1, quote: 'Overnight cost 900 EUR/kW', value_text: null, value: '900', unit: 'EUR/kW', label: true }] } } })
    render(<EquipmentTableImport componentClass="Generator" assetsLabel="generators" assetNames={['PV1']}
      onClose={() => {}} onApplied={() => {}} />)
    await userEvent.selectOptions(await screen.findByTestId('table-import-upload'), 'p1')
    await userEvent.click(within(await screen.findByTestId('document-values-assets')).getByLabelText('PV1'))
    expect(why()).toBe('State the currency of the picked values.')
    await userEvent.type(screen.getByLabelText('Currency'), 'eur')
    expect(why()).toBe('State the money year of the picked prices.')
    await userEvent.type(screen.getByLabelText('Money year'), '2024')
    expect(why()).toBe('Preview the changes first.')
  })
})

const TARGETS = { tariff: { pinned: false, items: [{ id: 'energy', unit: '/kWh', fields: ['periods[peak].rate', 'periods[offpeak].rate'] }] },
  ppa: [{ id: 'ppa1', pinned: true, fields: [{ field: 'price', label: 'Price', unit: '/MWh' }] }] }
const TARIFF_CANDIDATES = { 'tariff:energy': { more: 0, candidates: [
  { page: 1, quote: 'Energy charge peak 72.50 EUR/MWh', value_text: null, value: '72.50', unit: '/MWh', label: true },
  { page: 1, quote: 'Off-peak 0.11 EUR/kWh', value_text: null, value: '0.11', unit: '/kWh', label: false }] },
'ppa:price': { more: 0, candidates: [] } }

describe('DocumentValuesPicker, section 7', () => {
  it('offers PDFs only, fills a tariff rate and ends on the receipt, without a section jump', async () => {
    const tariffCard = { ...CARD, tool: 'apply_commercial_document_values', section: { id: 'commercial', label: 'Commercial & parties' } }
    previewCommercial.mockImplementation(async (b: { citations?: unknown[] }) => b.citations
      ? { document: { source: SOURCE }, targets: TARGETS, plan: { ...PLAN, unmapped: [{ kind: 'library_pinned',
        message: 'This tariff comes from the Commercial library (v3); it is not edited here. Update it there.',
        library: { kind: 'tariff', id: 'grid-de', version: 3 } }] }, card: tariffCard }
      : { document: { source: SOURCE }, targets: TARGETS, candidates: TARIFF_CANDIDATES })
    applyCommercial.mockResolvedValue({ receipt: { ...RECEIPT, undo: { tool: 'undo_last', available: false } } })
    const onClose = vi.fn()
    render(<EquipmentTableImport commercial="tariff" onClose={onClose} onApplied={() => {}} />)
    expect(screen.getAllByText('Fill values from a PDF').length).toBeGreaterThan(0)
    const upload = await screen.findByTestId('table-import-upload')
    expect([...upload.querySelectorAll('option')].map(o => o.textContent)).toEqual(['Choose an upload…', 'datasheet.pdf'])
    await userEvent.selectOptions(upload, 'p1')
    await waitFor(() => expect(previewCommercial).toHaveBeenCalledWith({ upload_id: 'p1', find: true, decimal_mark: null }))

    const peak = await screen.findByTestId('document-values-row-energy/periods[peak].rate')
    expect(within(peak).getByText('energy · peak rate')).toBeTruthy()
    await userEvent.click(within(peak).getAllByRole('radio')[0])
    expect(why()).toBe('State the currency of the picked values.')
    await userEvent.type(screen.getByLabelText('Currency'), 'EUR')
    expect(screen.queryByLabelText('Money year')).toBeNull()  // a tariff rate has no money year
    await userEvent.click(screen.getByTestId('document-values-preview-btn'))
    await waitFor(() => expect(previewCommercial).toHaveBeenLastCalledWith({ upload_id: 'p1',
      citations: [{ target: 'tariff', entity: 'energy', field: 'periods[peak].rate', page: 1,
        quote: 'Energy charge peak 72.50 EUR/MWh', unit: '/MWh', value_text: null }],
      decimal_mark: null, currency: 'EUR', money_year: null, confirm: [] }))

    // A library-pinned gap links to the Commercial library through shared navigation.
    const link = within(await screen.findByTestId('document-values-gaps')).getByTestId('document-values-library-link')
    expect(link.textContent).toBe('Update it there ›')
    await userEvent.click(link)
    expect(onClose).toHaveBeenCalledTimes(1)  // the dialog does not stay over the library page
    expect(openTarget).toHaveBeenCalledWith(expect.objectContaining({ section: 'commercial', panel: 'library' }))

    await userEvent.click(screen.getByTestId('document-values-apply'))
    expect(await screen.findByTestId('chat-edit-receipt')).toBeTruthy()
    expect(screen.queryByTestId('chat-edit-undo')).toBeNull()
    expect(screen.getByTestId('document-values-revert').textContent).toContain('set the old values from the card back')
    expect(screen.queryByTestId('document-values-open-section')).toBeNull()
    await userEvent.click(screen.getByTestId('document-values-close'))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('says a library-pinned contract is updated in the library and blocks its preview in place', async () => {
    previewCommercial.mockResolvedValue({ document: { source: SOURCE }, targets: TARGETS, candidates: TARIFF_CANDIDATES })
    render(<EquipmentTableImport commercial="ppa" onClose={() => {}} onApplied={() => {}} />)
    await userEvent.selectOptions(await screen.findByTestId('table-import-upload'), 'p1')
    const pinned = await screen.findByTestId('document-values-pinned')
    expect(within(pinned).getByTestId('document-values-library-link')).toBeTruthy()
    expect(why()).toBe('This one comes from the Commercial library; update it there.')
    expect(screen.getByTestId('document-values-preview-btn')).toHaveProperty('disabled', true)
  })
})
