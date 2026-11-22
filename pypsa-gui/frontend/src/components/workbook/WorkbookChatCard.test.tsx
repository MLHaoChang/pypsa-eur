// Excel input workbook issue 06: chat's import card shows the preview before
// anything is created (IMPORT-FLOW-1), and its download card fetches the file.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ConfirmationCard } from '../ConfirmationCard'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import { DOWNLOAD_FAILED, WorkbookDownloadCard, workbookCardOf, workbookDownloadOf } from './WorkbookChatCard'

vi.mock('../../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))
const template = vi.fn()
const project = vi.fn()
vi.mock('../../api/workbook', () => ({ workbookApi: {
  template: (...a: unknown[]) => template(...a), project: (...a: unknown[]) => project(...a),
} }))

const CARD = {
  version: 1, kind: 'workbook_import', file_id: 'f1', filename: 'Grid West.xlsx', sha256: 'a'.repeat(64),
  name: 'Grid West', says: 'Import Grid West.xlsx as a new project.',
  summary: { buses: 2, generators: 1, lines: 1, links: 0, storage_units: 0, stores: 0, loads: 1, transformers: 0, snapshots: 24 },
  report: { source_format: 'input_workbook', source_files: ['Grid West.xlsx'], counts: { Nodes: 2, Generators: 1 },
    unmapped: [{ source_item: 'Sheet9', reason: 'not a workbook sheet' }], assumed: [],
    warnings: ['w1'], warnings_total: 3, suggestions: [] },
  rederived: [], not_carried: ['Results'],
}

function setPending(workbook_card?: unknown) {
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'call-1', tool_name: 'import_input_workbook',
    args: { file_id: 'f1', expected_sha256: 'a'.repeat(64) }, safety_tier: 'destructive',
    confirmation_token: 'token-1', ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300_000, workbook_card,
  } })
}

beforeEach(() => {
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
  template.mockReset(); project.mockReset()
})
afterEach(cleanup)

it.each(['expert', 'guided'] as const)('shows what the import creates before it is approved, in %s', (uiMode) => {
  useUIStore.setState({ uiMode })
  setPending(CARD)
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-workbook-card-title').textContent).toBe('New project “Grid West” from Grid West.xlsx')
  expect(screen.getByTestId('chat-workbook-card').textContent).toContain('nothing is overwritten')
  expect(screen.getByTestId('workbook-counts').textContent).toContain('Nodes 2')
  expect(screen.getByTestId('workbook-unmapped').textContent).toContain('Sheet9')
  expect(screen.getByTestId('chat-workbook-card-more').textContent).toBe('2 more lines are in the file but not listed here.')
  expect((screen.getByTestId('chat-confirm-approve') as HTMLButtonElement).disabled).toBe(false)
})

it.each([[undefined], [{ ...CARD, report: { counts: {} } }], [{ ...CARD, kind: 'sweep' }]])(
  'an import without a well-formed preview cannot be approved (%#)', (card) => {
    setPending(card)
    render(<ConfirmationCard />)
    expect(screen.queryByTestId('chat-workbook-card')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('The preview of this workbook is unavailable')
    expect((screen.getByTestId('chat-confirm-approve') as HTMLButtonElement).disabled).toBe(true)
  })

it('reads only its own results', () => {
  expect(workbookCardOf(CARD)?.name).toBe('Grid West')
  expect(workbookCardOf({ ...CARD, name: '' })).toBeNull()
  expect(workbookDownloadOf({ workbook_download: 'template', filename: 't.xlsx' })).not.toBeNull()
  expect(workbookDownloadOf({ workbook_download: 'project', filename: 'p.xlsx' })).toBeNull()
  expect(workbookDownloadOf({ file_id: 'x', filename: 'n.nc' })).toBeNull()
})

it("downloads a project's workbook through the workbook route and says it is as last saved", async () => {
  project.mockResolvedValue({ blob: new Blob(['x']), filename: 'Alpha.xlsx' })
  URL.createObjectURL = vi.fn(() => 'blob:1'); URL.revokeObjectURL = vi.fn()
  render(<WorkbookDownloadCard result={{ workbook_download: 'project', project: 'Alpha', filename: 'Alpha.xlsx' }} />)
  expect(screen.getByTestId('chat-workbook-download').textContent).toContain("unsaved edits aren't included")
  fireEvent.click(screen.getByTestId('chat-workbook-download-button'))
  await waitFor(() => expect(screen.getByTestId('chat-workbook-download-button').textContent).toBe('Download again'))
  expect(project).toHaveBeenCalledWith('Alpha')
  expect(template).not.toHaveBeenCalled()
})

it('shows a failed template download in the backend words', async () => {
  template.mockRejectedValue(new Error('Authentication required'))
  render(<WorkbookDownloadCard result={{ workbook_download: 'template', filename: 'PyPSA Studio input workbook.xlsx' }} />)
  fireEvent.click(screen.getByTestId('chat-workbook-download-button'))
  await waitFor(() => expect(screen.getByTestId('chat-workbook-download-error').textContent).toBe('Authentication required'))
})

it.each(['Request failed with status code 500', 'Network Error', 'timeout of 180000ms exceeded'])(
  'a transport failure (%s) reads as a plain sentence, not the axios message', async (message) => {
    template.mockRejectedValue(new Error(message))
    render(<WorkbookDownloadCard result={{ workbook_download: 'template', filename: 'PyPSA Studio input workbook.xlsx' }} />)
    fireEvent.click(screen.getByTestId('chat-workbook-download-button'))
    await waitFor(() => expect(screen.getByTestId('chat-workbook-download-error').textContent).toBe(DOWNLOAD_FAILED))
    expect(screen.getByTestId('chat-workbook-download-button').textContent).toBe('Download')
  })
