// The model-file import dialog (project-io 04 Slice B): a preview in plain names, the
// lists in full behind "Show all", warnings shown, no hash, and Create project posting
// the files with the preview's hash.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { modelImportApi, type ModelPreview } from '../../api/modelImport'
import { projectsApi } from '../../api/projects'
import { switchToProject } from '../../utils/projectActions'
import { resultsApi } from '../../api/simulation'
import { ModelImportDialog } from './ModelImportDialog'

vi.mock('../../api/modelImport', async (orig) => {
  const actual = await orig<typeof import('../../api/modelImport')>()
  return { ...actual, modelImportApi: { formats: vi.fn(), preview: vi.fn(), import: vi.fn() } }
})
vi.mock('../../api/projects', () => ({ projectsApi: { list: vi.fn(), importBundle: vi.fn() }, bundleErrorMessage: vi.fn() }))
vi.mock('../../utils/projectActions', () => ({ switchToProject: vi.fn() }))
vi.mock('../../api/simulation', () => ({ resultsApi: { getEhStudy: vi.fn() } }))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const FILE = new File(['x'], 'case14.raw')
const preview = (over: Partial<ModelPreview> = {}): ModelPreview => ({
  sha256: 'a'.repeat(64), format: 'psse', format_label: 'PSS/E RAW', suggested_name: 'case14', files: ['case14.raw'],
  bus_count: 14, snapshot_count: 1, counts: { Buses: 14, Lines: 17 },
  unmapped: Array.from({ length: 7 }, (_, i) => ({ source_item: `Area ${i + 1}`, reason: 'areas are not modelled' })),
  assumed: [{ asset: 'Line 1-2', parameter: 'Rating (MVA)', value: 9999, why: 'the file gives no rating' }],
  warnings: ['The first Model by name was used.'], suggestions: [], stored_files: [],
  ...over,
})
const mount = () => {
  const onClose = vi.fn()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ModelImportDialog initialFiles={[FILE]} onClose={onClose} /></QueryClientProvider>)
  return userEvent.setup()
}

beforeEach(() => {
  vi.mocked(modelImportApi.formats).mockResolvedValue([{ id: 'psse', label: 'PSS/E RAW', extensions: ['.raw'] }])
  vi.mocked(modelImportApi.preview).mockResolvedValue(preview())
  vi.mocked(modelImportApi.import).mockResolvedValue({ ...preview(), project_id: 'p1', name: 'case14' })
  vi.mocked(projectsApi.list).mockResolvedValue([])
  vi.mocked(switchToProject).mockResolvedValue({ status: 'switched' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('model import preview', () => {
  it('shows counts, Not carried, Assumed and warnings in plain words, with no hash', async () => {
    mount()
    expect((await screen.findByTestId('model-report')).textContent).toContain('Buses 14 · Lines 17')
    expect(screen.getByTestId('workbook-unmapped').textContent).toContain('Not carried (7)')
    expect(screen.getByTestId('workbook-unmapped').textContent).toContain('Area 1: areas are not modelled')
    expect(screen.getByTestId('workbook-assumed').textContent).toContain('Line 1-2 · Rating (MVA) · 9,999 · the file gives no rating')
    expect(screen.getByTestId('workbook-warnings').textContent).toContain('The first Model by name was used.')
    expect(screen.getByTestId('model-import-dialog').textContent).not.toContain('aaaa')
    expect((screen.getByTestId('model-name') as HTMLInputElement).value).toBe('case14')
  })

  it('has every unmapped row behind "Show all"', async () => {
    const user = mount()
    await screen.findByTestId('model-report')
    expect(screen.getByTestId('workbook-unmapped-list').textContent).not.toContain('Area 7')
    await user.click(screen.getByTestId('workbook-unmapped-list-toggle'))
    expect(screen.getByTestId('workbook-unmapped-list').textContent).toContain('Area 7: areas are not modelled')
  })

  it('Create project posts the files with the preview’s hash and the chosen name, then opens it', async () => {
    const user = mount()
    await screen.findByTestId('model-report')
    await user.click(screen.getByTestId('model-import-btn'))
    await waitFor(() => expect(switchToProject).toHaveBeenCalled())
    expect(modelImportApi.import).toHaveBeenCalledWith([FILE], 'a'.repeat(64), 'case14')
  })

  it('a refusal shows once in the backend’s words and creates nothing', async () => {
    vi.mocked(modelImportApi.preview).mockRejectedValue({
      response: { status: 422, data: { detail: { error_kind: 'import_unreadable', message: 'The file is not a PSS/E RAW file.' } } },
    })
    mount()
    expect((await screen.findByTestId('model-refusal')).textContent).toContain('The file is not a PSS/E RAW file.')
    expect(modelImportApi.import).not.toHaveBeenCalled()
  })

  it('an unreachable app or a bare network error shows a plain sentence, never the library’s text', async () => {
    vi.mocked(modelImportApi.preview).mockRejectedValue(new Error('Network Error: AxiosError ECONNRESET'))
    mount()
    const said = (await screen.findByTestId('model-refusal')).textContent ?? ''
    expect(said).toContain('The app did not answer: nothing was created.')
    expect(said).not.toMatch(/axios|ECONNRESET|Network Error/i)
  })

  it('a server error with no plain message of its own shows a plain sentence, not its status text', async () => {
    vi.mocked(modelImportApi.preview).mockRejectedValue({ response: { status: 500, data: { detail: 'Traceback (most recent call last): KeyError' } } })
    mount()
    const said = (await screen.findByTestId('model-refusal')).textContent ?? ''
    expect(said).not.toMatch(/Traceback|KeyError/)
  })
})
