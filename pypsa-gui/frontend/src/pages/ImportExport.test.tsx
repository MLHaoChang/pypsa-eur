import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ImportZone } from './ImportExport'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { ioApi } from '../api/io'
import { networkApi } from '../api/network'
import { saveProjectQuietly } from '../utils/projectActions'
import toast from 'react-hot-toast'

vi.mock('../api/projects')
vi.mock('../api/io')
vi.mock('../api/network')
vi.mock('../utils/projectActions', () => ({ saveProjectQuietly: vi.fn().mockResolvedValue(true) }))
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

function renderZone() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ImportZone onSuccess={() => {}} />
    </QueryClientProvider>,
  )
}

// The Browse <input> path exercises the same handleFile seam as drop.
async function pickFile(name: string) {
  const file = new File(['x'], name)
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(input, file)
  return file
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(saveProjectQuietly).mockResolvedValue(true)
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false })
  vi.mocked(ioApi.importNetcdf).mockResolvedValue({} as never)
  vi.mocked(projectsApi.importBundle).mockResolvedValue({ imported: 'Alpha', summary: {} } as never)
})

describe('ImportZone guard', () => {
  it('bundle onto a bound project asks before importing', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    renderZone()
    await pickFile('other.pypsaproj.zip')
    expect(projectsApi.importBundle).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toBeTruthy()
    expect(screen.getByText(/replace the contents of 'Alpha'/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(projectsApi.importBundle).toHaveBeenCalled())
  })

  it('raw import with a bound project silently saves first, no dialog', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    renderZone()
    await pickFile('grid.nc')
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
    expect(saveProjectQuietly).toHaveBeenCalledWith('Alpha')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('scratch network with undo depth > 0 asks first', async () => {
    useUIStore.setState({ currentProject: null })
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 3, unsaved: true })
    renderZone()
    await pickFile('grid.nc')
    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
  })

  it('asks first when the dirty check could not be answered (ADR-0001)', async () => {
    // `depth` was initialised to 0 and the failed probe fell through to it, so
    // an unreachable/refusing backend produced the same value as a genuinely
    // clean network — and the destructive import ran with NO confirmation.
    // "We could not check whether you have unsaved work" is not "you have
    // none"; on a destructive path the unknown must fail CLOSED.
    useUIStore.setState({ currentProject: null })
    vi.mocked(networkApi.undoInfo).mockRejectedValue(new Error('backend unreachable'))
    renderZone()
    await pickFile('grid.nc')

    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toMatch(/could not|unsaved/i)

    // Confirming still imports — the guard asks, it does not block.
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
  })

  it('asks when a SOLVE is unsaved, even though undo depth is 0', async () => {
    // The defect this field exists for. Solver results never enter the undo
    // stack, so depth reads 0 on a solved-but-unsaved project and the guard
    // used to import straight over it.
    useUIStore.setState({ currentProject: null })
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: true })
    renderZone()
    await pickFile('grid.nc')

    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
    await screen.findByRole('dialog')
  })

  it('clean scratch network imports without any prompt', async () => {
    useUIStore.setState({ currentProject: null })
    renderZone()
    await pickFile('grid.nc')
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(saveProjectQuietly).not.toHaveBeenCalled()
  })
})

// ── M2/M3: the dialog's pending state, and the save-then-import contract ────

describe('ImportZone pending + save-first behaviour', () => {
  it('keeps the confirm dialog open and pending until the import settles (M2)', async () => {
    // `onConfirm` used to clear `pendingImport` synchronously, which closed the
    // dialog on the same tick — so `pending={importMut.isPending}` had nothing
    // left to render and the "Working…" state never appeared. ScenariosPanel's
    // delete dialog is the reference: confirm only fires the mutation; the
    // mutation's own settle handler closes the dialog.
    useUIStore.setState({ currentProject: 'Alpha' })
    let release: (v: unknown) => void = () => {}
    vi.mocked(projectsApi.importBundle).mockReturnValueOnce(
      new Promise((resolve) => { release = resolve }) as never,
    )
    renderZone()
    await pickFile('other.pypsaproj.zip')
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))

    // Still open, and now showing the pending affordance.
    expect(await screen.findByRole('button', { name: 'Working…' })).toBeTruthy()
    expect(screen.getByRole('dialog')).toBeTruthy()

    release({ imported: 'Alpha', summary: {} })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('closes the dialog when the import FAILS, so the user is not stuck (M2)', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    vi.mocked(projectsApi.importBundle).mockRejectedValueOnce(new Error('boom'))
    renderZone()
    await pickFile('other.pypsaproj.zip')
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('does NOT import when the pre-import save fails (M3)', async () => {
    // The raw-import branch is prompt-less precisely BECAUSE the outgoing
    // project is saved first (D6). If that save is refused — a foreign edit
    // lock is the realistic cause — importing anyway destroys the in-memory
    // network the save was meant to protect.
    useUIStore.setState({ currentProject: 'Alpha' })
    vi.mocked(saveProjectQuietly).mockResolvedValueOnce(false)
    renderZone()
    await pickFile('grid.nc')

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
    expect(vi.mocked(toast.error).mock.calls[0][0]).toContain('Alpha')
    // One clear message, not a pile.
    expect(vi.mocked(toast.error)).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('still imports when the pre-import save succeeds (M3 control)', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    renderZone()
    await pickFile('grid.nc')
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
    expect(toast.error).not.toHaveBeenCalled()
  })
})

// Excel input workbook S3 (UX 27 Q5): the open-network import refuses an input
// workbook with 422 `import_as_new_project`; the zone hands the same file to the
// workbook dialog, with no toast, and leaves the open network as it was.
describe('an input workbook dropped on the Import zone', () => {
  const refusal = {
    response: { status: 422, data: { detail: { error_kind: 'import_as_new_project', message: 'This is a PyPSA Studio input workbook…' } } },
  }

  it('opens the workbook dialog with the same file; the open network is untouched', async () => {
    useUIStore.setState({ currentProject: 'Alpha', projectName: 'Alpha', workbookImportRequest: null })
    vi.mocked(ioApi.importExcel).mockRejectedValue(refusal)
    const onSuccess = vi.fn()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ImportZone onSuccess={onSuccess} /></QueryClientProvider>)
    const file = await pickFile('Hub.xlsx')
    await waitFor(() => expect(useUIStore.getState().workbookImportRequest?.file).toBe(file))
    expect(onSuccess).not.toHaveBeenCalled()
    expect(useUIStore.getState().currentProject).toBe('Alpha')
    expect(useUIStore.getState().projectName).toBe('Alpha')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('a scratch network with unsaved work still asks before the Excel import', async () => {
    useUIStore.setState({ currentProject: null, workbookImportRequest: null })
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 1, unsaved: true })
    vi.mocked(ioApi.importExcel).mockRejectedValue(refusal)
    renderZone()
    await pickFile('Hub.xlsx')
    expect(ioApi.importExcel).not.toHaveBeenCalled()
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(useUIStore.getState().workbookImportRequest).not.toBeNull())
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('any other Excel refusal shows once, in the backend words', async () => {
    useUIStore.setState({ currentProject: 'Alpha', workbookImportRequest: null })
    vi.mocked(ioApi.importExcel).mockRejectedValue({ response: { status: 422, data: { detail: { error_kind: 'import_unreadable', message: 'The file is not a readable Excel workbook.' } } }, message: 'Request failed' })
    renderZone()
    await pickFile('grid.xlsx')
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The file is not a readable Excel workbook.'))
    expect(useUIStore.getState().workbookImportRequest).toBeNull()
  })
})
