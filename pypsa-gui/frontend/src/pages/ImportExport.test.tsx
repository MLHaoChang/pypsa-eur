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
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0 })
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
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 3 })
    renderZone()
    await pickFile('grid.nc')
    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
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
