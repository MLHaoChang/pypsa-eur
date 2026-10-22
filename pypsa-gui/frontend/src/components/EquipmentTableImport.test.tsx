// "Import rows from a table…" (data-to-model, AI-567): pick an upload, map
// columns to schema fields, preview the edit card, apply exactly that card,
// and end on the receipt with "Open <section> ›".
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EquipmentTableImport from './EquipmentTableImport'
import { useUIStore } from '../store/uiStore'

const preview = vi.fn()
const apply = vi.fn()
const openSection = vi.fn()

vi.mock('../api/equipmentTable', () => ({
  equipmentTableApi: { preview: (b: unknown) => preview(b), apply: (b: unknown) => apply(b) },
}))
const uploadFile = vi.fn()
vi.mock('../api/uploads', () => ({
  uploadFile: (p: string, f: File) => uploadFile(p, f),
  listUploads: vi.fn(async () => [
    { file_id: 'u1', filename: 'lines.csv', kind: 'user_upload' },
    { file_id: 'u2', filename: 'photo.png', kind: 'user_upload' },
  ]),
}))
vi.mock('../layout/flowNavigation', () => ({ openSectionInExpert: (id: string) => openSection(id) }))

const SOURCE = { upload_id: 'u1', filename: 'lines.csv', sha256: 'a'.repeat(64), sheet: '(csv)', sheets: ['(csv)'] }
const TABLE = { source: SOURCE, headers: ['name', 'rating'], row_count: 2,
  rows: [{ row: 2, cells: { name: 'L1', rating: '500' } }] }
const FIELDS = [{ field: 's_nom', label: 'Rated power', units: ['MVA'], currency: null },
  { field: 'capital_cost', label: 'Annual capital cost', units: ['EUR/MVA'], currency: 'EUR' }]
const ROW = { component_class: 'Line', name: 'L1', field: 's_nom', label: 'Rated power', old: 300, new: 500,
  unit: 'MVA', role: 'typed' as const, source: { filename: 'lines.csv', sheet: '(csv)', cell: 'B2', raw: '500', raw_unit: 'MVA' } }
const CARD = { version: 1, kind: 'parameter_edit', tool: 'apply_equipment_mapping', rows: [ROW],
  says: 'Set Line L1 s_nom', complete: true, digest: 'd'.repeat(64), section: { id: 'connection', label: 'Connection & grid' } }
const PLAN = { changed_cells: 1, unchanged_cells: 0, unmapped: [], unmapped_total: 0, unmapped_by_kind: {},
  needs: [], missing: [], notes: [] }

beforeEach(() => {
  preview.mockReset(); apply.mockReset(); openSection.mockReset()
  useUIStore.setState({ currentProject: 'P' })
  preview.mockImplementation(async (b: { name_column?: string }) => b.name_column
    ? { table: TABLE, fields: FIELDS, plan: PLAN, card: CARD }
    : { table: TABLE, fields: FIELDS })
})

describe('EquipmentTableImport', () => {
  it('offers only table uploads, maps a column, previews the card and applies its digest', async () => {
    const onApplied = vi.fn(), onClose = vi.fn()
    apply.mockResolvedValue({ written: { L1: { s_nom: 500 } }, receipt: {
      version: 1, digest: CARD.digest, outcome: 'verified', rows: [{ ...ROW, readback: 500 }], says: CARD.says,
      results_stale: false, undo: { tool: 'undo_last', available: true },
      section: { id: 'connection', label: 'Connection & grid', state: 'complete', changed: false } } })
    render(<EquipmentTableImport componentClass="Line" assetsLabel="lines" onClose={onClose} onApplied={onApplied} />)

    const upload = await screen.findByTestId('table-import-upload')
    expect([...upload.querySelectorAll('option')].map(o => o.textContent)).toEqual(['Choose an upload…', 'lines.csv'])
    await userEvent.selectOptions(upload, 'u1')
    await waitFor(() => expect(preview).toHaveBeenCalledWith({ upload_id: 'u1', sheet: null, component_class: 'Line' }))

    expect(screen.getByTestId('table-import-apply')).toHaveProperty('disabled', true)
    await userEvent.selectOptions(await screen.findByLabelText('Field for rating'), 's_nom')
    // The table's unit is the user's statement: never pre-filled, and no preview without it.
    expect((screen.getByLabelText('Unit for rating') as HTMLSelectElement).value).toBe('')
    expect(screen.getByTestId('table-import-preview-btn')).toHaveProperty('disabled', true)
    await userEvent.selectOptions(screen.getByLabelText('Unit for rating'), 'MVA')
    expect(screen.queryByTestId('table-import-money')).toBeNull()
    await userEvent.click(screen.getByTestId('table-import-preview-btn'))
    expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({
      name_column: 'name', columns: { rating: { field: 's_nom', unit: 'MVA' } }, currency: null, money_year: null }))
    expect(await screen.findByTestId('chat-edit-card')).toBeTruthy()
    expect(screen.getByTestId('chat-edit-source').textContent).toBe('lines.csv › B2 (500 MVA)')

    await userEvent.click(screen.getByTestId('table-import-apply'))
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ source_sha256: SOURCE.sha256, digest: CARD.digest }))
    expect(await screen.findByTestId('chat-edit-receipt')).toBeTruthy()
    expect(onApplied).toHaveBeenCalledTimes(1)
    const open = screen.getByTestId('table-import-open-section')
    expect(open.textContent).toBe('Open Connection & grid ›')
    await userEvent.click(open)
    expect(openSection).toHaveBeenCalledWith('connection')
    expect(onClose).toHaveBeenCalled()
  })

  it('a cost field asks for the table currency and money year', async () => {
    render(<EquipmentTableImport componentClass="Line" assetsLabel="lines" onClose={() => {}} onApplied={() => {}} />)
    await userEvent.selectOptions(await screen.findByTestId('table-import-upload'), 'u1')
    await userEvent.selectOptions(await screen.findByLabelText('Field for rating'), 'capital_cost')
    await userEvent.selectOptions(screen.getByLabelText('Unit for rating'), 'EUR/MVA')
    expect(screen.getByTestId('table-import-money')).toBeTruthy()
    // Not assumed: the user states the table's currency.
    expect((screen.getByLabelText('Currency') as HTMLInputElement).value).toBe('')
    await userEvent.type(screen.getByLabelText('Currency'), 'eur')
    await userEvent.type(screen.getByLabelText('Money year'), '2024')
    await userEvent.click(screen.getByTestId('table-import-preview-btn'))
    expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({ currency: 'EUR', money_year: 2024 }))
  })

  it('a refused preview shows the server reason and offers no apply', async () => {
    render(<EquipmentTableImport componentClass="Line" assetsLabel="lines" onClose={() => {}} onApplied={() => {}} />)
    await userEvent.selectOptions(await screen.findByTestId('table-import-upload'), 'u1')
    await userEvent.selectOptions(await screen.findByLabelText('Field for rating'), 's_nom')
    await userEvent.selectOptions(screen.getByLabelText('Unit for rating'), 'MVA')
    preview.mockRejectedValueOnce({ response: { data: { detail: { error_kind: 'source_changed',
      message: 'The upload’s bytes no longer match its recorded digest.' } } } })
    await userEvent.click(screen.getByTestId('table-import-preview-btn'))
    expect((await screen.findByTestId('table-import-error')).textContent).toContain('no longer match')
    expect(screen.getByTestId('table-import-apply')).toHaveProperty('disabled', true)
    expect(apply).not.toHaveBeenCalled()
  })

  it('a table can be uploaded from the form itself and is then selected', async () => {
    uploadFile.mockResolvedValue({ file_id: 'u3', filename: 'trafos.xlsx', kind: 'user_upload' })
    render(<EquipmentTableImport componentClass="Line" assetsLabel="lines" onClose={() => {}} onApplied={() => {}} />)
    const file = new File(['x'], 'trafos.xlsx')
    await userEvent.upload(await screen.findByTestId('table-import-file'), file)
    expect(uploadFile).toHaveBeenCalledWith('P', file)
    await waitFor(() => expect((screen.getByTestId('table-import-upload') as HTMLSelectElement).value).toBe('u3'))
    await waitFor(() => expect(preview).toHaveBeenCalledWith({ upload_id: 'u3', sheet: null, component_class: 'Line' }))
  })
})
