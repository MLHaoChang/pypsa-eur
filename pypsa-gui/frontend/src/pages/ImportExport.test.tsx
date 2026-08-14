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

vi.mock('../api/projects')
vi.mock('../api/io')
vi.mock('../api/network')
vi.mock('../utils/projectActions', () => ({ saveProjectQuietly: vi.fn().mockResolvedValue(true) }))

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
