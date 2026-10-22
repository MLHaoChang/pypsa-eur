// "Update it there ›" in Guided, against the real navigation and store (UX reviewer,
// PR-C condition 3): the library opens in the field drawer and the mode stays Guided.
import { beforeEach, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EquipmentTableImport from './EquipmentTableImport'
import { useUIStore } from '../store/uiStore'

const SOURCE = { upload_id: 'p1', filename: 'ppa.pdf', sha256: 'b'.repeat(64), pages: 1, truncated: [] }
const TARGETS = { tariff: null, ppa: [{ id: 'ppa1', pinned: true, fields: [{ field: 'price', label: 'Price', unit: '/MWh' }] }] }

vi.mock('../api/documentValues', () => ({
  documentValuesApi: {
    previewCommercial: vi.fn(async () => ({ document: { source: SOURCE }, targets: TARGETS, candidates: {} })),
    previewAssets: vi.fn(), applyAssets: vi.fn(), applyCommercial: vi.fn(),
  },
}))
vi.mock('../api/equipmentTable', () => ({ equipmentTableApi: { preview: vi.fn(), apply: vi.fn() } }))
vi.mock('../api/uploads', () => ({
  uploadFile: vi.fn(),
  listUploads: vi.fn(async () => [{ file_id: 'p1', filename: 'ppa.pdf', kind: 'user_upload' }]),
}))

beforeEach(() => {
  useUIStore.setState({ currentProject: 'P', uiMode: 'guided', fieldDrawer: null })
})

it('opens the library in the field drawer and stays in Guided', async () => {
  const onClose = vi.fn()
  render(<EquipmentTableImport commercial="ppa" onClose={onClose} onApplied={() => {}} />)
  await userEvent.selectOptions(await screen.findByTestId('table-import-upload'), 'p1')
  const link = within(await screen.findByTestId('document-values-pinned')).getByTestId('document-values-library-link')
  expect(link.textContent).toBe('Update it there ›')
  await userEvent.click(link)
  expect(onClose).toHaveBeenCalledTimes(1)
  const ui = useUIStore.getState()
  expect(ui.fieldDrawer?.panel).toBe('library')
  expect(ui.uiMode).toBe('guided')
})
