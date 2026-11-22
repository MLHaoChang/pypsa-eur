// Excel input workbook S3 (UX 27 GO): the Export chooser's two input workbook
// rows. The project's row always says it holds the project as last saved, with
// "Save first" on that line only while edits are unsaved; with no saved project
// it says why Download is off. The blank template keeps its own file name.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { workbookApi } from '../api/workbook'
import { saveProjectQuietly } from '../utils/projectActions'
import Sidebar from './Sidebar'

vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }),
    undoInfo: vi.fn(),
  },
}))
vi.mock('../api/io', () => ({
  ioApi: { resultsWorkbookViews: vi.fn().mockResolvedValue({ views: [], run: null }) },
}))
vi.mock('../api/workbook', () => ({ workbookApi: { project: vi.fn(), template: vi.fn() } }))
vi.mock('../api/projects', () => ({
  projectsApi: { list: vi.fn().mockResolvedValue([]), save: vi.fn() },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { preflight: vi.fn().mockResolvedValue({ errors: 0, warnings: 0 }) },
}))
vi.mock('../api/solveQueue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/solveQueue')>()
  return { ...actual, solveQueueApi: { list: vi.fn().mockResolvedValue({ jobs: [] }) } }
})
vi.mock('../api/localSettings', () => ({
  fetchLocalSettings: vi.fn().mockResolvedValue({ api_key_hint: null, log_path: '/tmp/app.log' }),
}))
vi.mock('../pages/ImportExport', () => ({ ImportZone: () => <div /> }))
vi.mock('./NewProjectWizard', () => ({ default: () => <div /> }))
vi.mock('../components/ProjectPicker', () => ({ default: () => <div /> }))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return {
    ...actual,
    invalidateNetworkQueries: vi.fn(),
    saveProjectQuietly: vi.fn().mockResolvedValue(true),
    resetBackendNetwork: vi.fn(),
    downloadProjectBundle: vi.fn(),
    abortRunningSim: vi.fn(),
    switchToProject: vi.fn(),
  }
})

async function openChooser(mode: 'guided' | 'expert', project: string | null) {
  useUIStore.setState({ uiMode: mode, currentProject: project, projectName: project ?? 'Untitled' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Sidebar /></MemoryRouter></QueryClientProvider>)
  act(() => { useUIStore.getState().requestIoModal('export') })
  return screen.findByTestId('io-modal')
}
const pick = (modal: HTMLElement, value: string) => fireEvent.click(within(modal).getByDisplayValue(value))
const download = (modal: HTMLElement) => within(modal).getByRole('button', { name: /Download File/ }) as HTMLButtonElement

beforeEach(() => {
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false } as never)
  vi.mocked(workbookApi.project).mockResolvedValue({ blob: new Blob(['x']), filename: 'oj-grid.xlsx' })
  vi.mocked(workbookApi.template).mockResolvedValue({ blob: new Blob(['x']), filename: 'PyPSA Studio input workbook.xlsx' })
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks(); useUIStore.setState({ ioModalRequest: null, ioModalFormat: null }) })

describe('the Export chooser’s input workbook rows', () => {
  for (const mode of ['guided', 'expert'] as const) {
    it(`a saved project: the note, no Save first while saved, and its workbook downloads (${mode})`, async () => {
      const modal = await openChooser(mode, 'oj-grid')
      expect(within(modal).getByText('Input workbook (Excel)')).toBeTruthy()
      expect(within(modal).getByText('Blank input workbook (Excel)')).toBeTruthy()
      // No import entry here: import has one entry per mode (Start tile, wizard From file).
      expect(within(modal).queryByText(/Import a workbook/)).toBeNull()
      pick(modal, 'input')
      expect(within(modal).getByTestId('input-workbook-note').textContent)
        .toBe("Holds the project as last saved: unsaved edits aren't included.")
      await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
      expect(within(modal).queryByTestId('input-workbook-save-first')).toBeNull()
      fireEvent.click(download(modal))
      await waitFor(() => expect(workbookApi.project).toHaveBeenCalledWith('oj-grid'))
    })

    it(`no saved project: Download is off and the row says why (${mode})`, async () => {
      const modal = await openChooser(mode, null)
      pick(modal, 'input')
      expect(within(modal).getByTestId('input-workbook-why').textContent).toBe('Save the project first.')
      expect(within(modal).queryByTestId('input-workbook-note')).toBeNull()
      expect(download(modal).disabled).toBe(true)
    })
  }

  it('unsaved edits: "Save first" on the note’s line saves the project', async () => {
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 2, unsaved: true } as never)
    const modal = await openChooser('expert', 'oj-grid')
    pick(modal, 'input')
    const save = await within(modal).findByTestId('input-workbook-save-first')
    expect(within(modal).getByTestId('input-workbook-note').contains(save)).toBe(true)
    fireEvent.click(save)
    await waitFor(() => expect(saveProjectQuietly).toHaveBeenCalledWith('oj-grid', true))
    expect(workbookApi.project).not.toHaveBeenCalled()
  })

  it('the blank template downloads under its own name, with no file name field', async () => {
    const modal = await openChooser('guided', 'oj-grid')
    pick(modal, 'template')
    expect(within(modal).queryByText('File name')).toBeNull()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    let name = ''
    click.mockImplementation(function (this: HTMLAnchorElement) { name = this.download })
    fireEvent.click(download(modal))
    await waitFor(() => expect(name).toBe('PyPSA Studio input workbook.xlsx'))
    click.mockRestore()
  })
})
