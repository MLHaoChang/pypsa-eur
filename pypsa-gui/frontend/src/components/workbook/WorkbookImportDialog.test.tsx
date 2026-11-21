// The input workbook import dialog (excel-input-workbook S3, UX 27 GO): the
// preview rows in sheet words, folds after five, the name's visible reasons,
// the posted fields, each refusal once with its action on the same line, the
// switch and "Created ‘X’, not opened", and Esc / focus / the discard guard (#1405).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { workbookApi, type WorkbookPreview } from '../../api/workbook'
import { projectsApi } from '../../api/projects'
import { switchToProject } from '../../utils/projectActions'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { WorkbookImportDialog, WorkbookImportHost } from './WorkbookImportDialog'

vi.mock('../../api/workbook', async (orig) => {
  const actual = await orig<typeof import('../../api/workbook')>()
  return { ...actual, workbookApi: { preview: vi.fn(), import: vi.fn(), template: vi.fn(), project: vi.fn() } }
})
vi.mock('../../api/projects', () => ({ projectsApi: { list: vi.fn() }, bundleErrorMessage: vi.fn() }))
vi.mock('../../utils/projectActions', () => ({ switchToProject: vi.fn() }))
vi.mock('../../api/simulation', () => ({ resultsApi: { getEhStudy: vi.fn() } }))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const FILE = new File(['x'], 'Hub.xlsx')
const preview = (over: Partial<WorkbookPreview> = {}): WorkbookPreview => ({
  sha256: 'f'.repeat(64), suggested_name: 'Hub',
  summary: { buses: 2, generators: 1, lines: 1, links: 0, storage_units: 0, stores: 0, loads: 1, transformers: 0, snapshots: 24 },
  report: {
    source_format: 'excel', source_files: ['Hub.xlsx'],
    counts: { Nodes: 2, Lines: 1, Generators: 1, Stores: 0 },
    unmapped: [{ source_item: 'Lines › column Colour', reason: 'not an input column' }],
    assumed: [{ asset: 'PV', parameter: 'Capital cost (€/MW)', value: 350000, why: 'left empty: the model default' }],
    warnings: [], suggestions: [],
  },
  rederived: [], not_carried: ['Map and single-line layout'],
  ...over,
})
const err = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

let onClose: ReturnType<typeof vi.fn<() => void>>
function mount(file: File | null = FILE) {
  onClose = vi.fn<() => void>()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><WorkbookImportDialog initialFile={file} onClose={onClose} /></QueryClientProvider>)
  return userEvent.setup()
}

beforeEach(() => {
  vi.mocked(workbookApi.preview).mockResolvedValue(preview())
  vi.mocked(workbookApi.import).mockResolvedValue({ ...preview(), project_id: 'p1', name: 'Hub' })
  vi.mocked(projectsApi.list).mockResolvedValue([{ name: 'Other' }] as never)
  vi.mocked(switchToProject).mockResolvedValue({ status: 'switched' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
  useUIStore.setState({ currentProject: 'Open one', workbookImportRequest: null })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('preview', () => {
  it('shows the rows in the workbook’s sheet words, money in the money() style and no hash', async () => {
    mount()
    const counts = await screen.findByTestId('workbook-counts')
    expect(counts.textContent).toContain('Nodes 2 · Lines 1 · Generators 1')
    expect(counts.textContent).not.toContain('Stores')
    expect(screen.getByTestId('workbook-unmapped').textContent).toContain('Lines › column Colour: not an input column')
    expect(screen.getByTestId('workbook-assumed').textContent).toContain('PV · Capital cost (€/MW) · € 350 k · left empty')
    expect(screen.getByTestId('workbook-not-carried').textContent).toContain('Map and single-line layout')
    expect(screen.getByTestId('workbook-import-dialog').textContent).not.toContain('ffff')
    expect((screen.getByTestId('workbook-name') as HTMLInputElement).value).toBe('Hub')
  })

  it('folds a long list after five with "(N more) Show all"', async () => {
    const unmapped = Array.from({ length: 7 }, (_, i) => ({ source_item: `Nodes row ${i + 2}`, reason: 'the row has values but no name' }))
    vi.mocked(workbookApi.preview).mockResolvedValue(preview({ report: { ...preview().report, unmapped } }))
    const user = mount()
    const list = await screen.findByTestId('workbook-unmapped-list')
    expect(list.querySelectorAll('li')).toHaveLength(5)
    const toggle = screen.getByTestId('workbook-unmapped-list-toggle')
    expect(toggle.textContent).toBe('(2 more) Show all')
    await user.click(toggle)
    expect(list.querySelectorAll('li')).toHaveLength(7)
  })

  it('shows a busy status region while the preview runs', async () => {
    let resolve!: (p: WorkbookPreview) => void
    vi.mocked(workbookApi.preview).mockReturnValue(new Promise(r => { resolve = r }))
    mount()
    const busy = await screen.findByTestId('workbook-previewing')
    expect(busy.getAttribute('role')).toBe('status')
    expect(busy.getAttribute('aria-busy')).toBe('true')
    await act(async () => { resolve(preview()) })
    expect(screen.queryByTestId('workbook-previewing')).toBeNull()
  })

  it('starts empty from the Start tile and previews the chosen file', async () => {
    const user = mount(null)
    expect(screen.queryByTestId('workbook-import-btn')).toBeNull()
    await user.upload(screen.getByTestId('workbook-file-input') as HTMLInputElement, FILE)
    await screen.findByTestId('workbook-counts')
    expect(workbookApi.preview).toHaveBeenCalledWith(FILE)
  })
})

describe('the name', () => {
  it('an empty name disables Import with its reason as visible text', async () => {
    const user = mount()
    const name = await screen.findByTestId('workbook-name')
    await user.clear(name)
    expect((screen.getByTestId('workbook-import-btn') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('workbook-import-why').textContent).toBe('Name the project first.')
  })

  it('a taken name says so and offers the next free name', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([{ name: 'Hub' }, { name: 'Hub 2' }] as never)
    const user = mount()
    await waitFor(() => expect(screen.getByTestId('workbook-import-why').textContent).toContain('A project named “Hub” already exists.'))
    expect((screen.getByTestId('workbook-import-btn') as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByTestId('workbook-use-free-name'))
    expect((screen.getByTestId('workbook-name') as HTMLInputElement).value).toBe('Hub 3')
    expect((screen.getByTestId('workbook-import-btn') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('import', () => {
  it('posts the file, the preview hash and the name, then opens the new project', async () => {
    const user = mount()
    const name = await screen.findByTestId('workbook-name')
    await user.clear(name); await user.type(name, 'My hub')
    await user.click(screen.getByTestId('workbook-import-btn'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(workbookApi.import).toHaveBeenCalledWith(FILE, 'f'.repeat(64), 'My hub')
    expect(switchToProject).toHaveBeenCalledWith('Hub', expect.anything())
    expect(toast.success).toHaveBeenCalledWith('Created and opened ‘Hub’')
  })

  it('created but not opened: a status line, and Try again retries the open only', async () => {
    vi.mocked(switchToProject).mockResolvedValueOnce({ status: 'busy-solve' })
    const user = mount()
    await user.click(await screen.findByTestId('workbook-import-btn'))
    const line = await screen.findByTestId('workbook-not-opened')
    expect(line.getAttribute('role')).toBe('status')
    expect(line.textContent).toContain('Created ‘Hub’, not opened: a solve is running.')
    await user.click(screen.getByTestId('workbook-try-open'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(switchToProject).toHaveBeenCalledTimes(2)
    expect(workbookApi.import).toHaveBeenCalledTimes(1)
  })
})

describe('a study running on the open project', () => {
  it('creates the project and says why it is not opened, without waiting on a switch', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValueOnce({ status: 'running' } as never)
    const user = mount()
    await user.click(await screen.findByTestId('workbook-import-btn'))
    const line = await screen.findByTestId('workbook-not-opened')
    expect(line.textContent).toContain('Created ‘Hub’, not opened: a study is still running. Wait for it to finish or abort it, then try again.')
    expect(switchToProject).not.toHaveBeenCalled()
    await user.click(screen.getByTestId('workbook-try-open'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(workbookApi.import).toHaveBeenCalledTimes(1)
  })
})

describe('refusals: once, inline, the action on the same line, no toast', () => {
  it('a changed file offers Preview again', async () => {
    vi.mocked(workbookApi.import).mockRejectedValueOnce(err(409, { error_kind: 'import_file_changed', message: 'The file is not the one that was previewed: preview it again before importing.' }))
    const user = mount()
    await user.click(await screen.findByTestId('workbook-import-btn'))
    const line = await screen.findByTestId('workbook-refusal')
    expect(within(line).getByTestId('workbook-refusal-action').textContent).toBe('Preview again')
    await user.click(within(line).getByTestId('workbook-refusal-action'))
    await waitFor(() => expect(workbookApi.preview).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('workbook-refusal')).toBeNull()
  })

  it.each([
    [err(413, 'Upload exceeds the 200 MB limit.'), 'The file is larger than the 200 MB upload limit. Nothing was read.'],
    [err(422, { error_kind: 'import_unreadable', message: 'The file is not a readable Excel workbook.' }), 'The file is not a readable Excel workbook.'],
    [err(413, { error_kind: 'import_expansion_too_large', message: 'The workbook is too large to read.' }), 'The workbook is too large to read.'],
  ])('a refused preview offers Choose another file (%#)', async (e, message) => {
    vi.mocked(workbookApi.preview).mockRejectedValueOnce(e)
    mount()
    const line = await screen.findByTestId('workbook-refusal')
    expect(line.textContent).toBe(`${message}Choose another file`)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    // The refusal's own action is the one way to choose again (UX 27 @06eb610b).
    expect(screen.getAllByRole('button', { name: 'Choose another file' })).toHaveLength(1)
  })

  it('a write failure offers Try again, which imports again', async () => {
    vi.mocked(workbookApi.import).mockRejectedValueOnce(err(500, { error_kind: 'import_write_failed', message: 'x' }))
    const user = mount()
    await user.click(await screen.findByTestId('workbook-import-btn'))
    const line = await screen.findByTestId('workbook-refusal')
    expect(line.textContent).toBe('The project could not be written; nothing was created.Try again')
    await user.click(within(line).getByTestId('workbook-refusal-action'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(workbookApi.import).toHaveBeenCalledTimes(2)
  })

  it('a name taken since the list loaded offers the next name', async () => {
    vi.mocked(workbookApi.import).mockRejectedValueOnce(err(409, "Project 'Hub' already exists"))
    const user = mount()
    await user.click(await screen.findByTestId('workbook-import-btn'))
    const action = await screen.findByTestId('workbook-refusal-action')
    expect(action.textContent).toBe('Use “Hub 2”')
    expect((screen.getByTestId('workbook-import-btn') as HTMLButtonElement).disabled).toBe(true)
    await user.click(action)
    expect((screen.getByTestId('workbook-name') as HTMLInputElement).value).toBe('Hub 2')
    expect(screen.queryByTestId('workbook-refusal')).toBeNull()
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('Esc, focus and the discard guard (#1405)', () => {
  it('Esc over a preview asks first; Esc again keeps it; Discard closes', async () => {
    const user = mount()
    await screen.findByTestId('workbook-counts')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByTestId('workbook-discard-confirm').textContent).toContain('Nothing has been created.')
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('workbook-discard-confirm')).toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    await user.click(screen.getByRole('button', { name: 'Discard' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('Esc with nothing previewed closes at once', async () => {
    mount(null)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('the host traps focus inside and returns it to the opener', async () => {
    const qc = new QueryClient()
    const opener = document.createElement('button'); document.body.appendChild(opener); opener.focus()
    render(<QueryClientProvider client={qc}><WorkbookImportHost /></QueryClientProvider>)
    act(() => { useUIStore.getState().requestWorkbookImport() })
    const dialog = await screen.findByRole('dialog')
    expect(dialog.contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })
})
