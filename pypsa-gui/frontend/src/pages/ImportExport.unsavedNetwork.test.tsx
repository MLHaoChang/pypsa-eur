// A raw network import into the Project Data modal leaves a network that belongs to no
// project: the store says so (the sidebar offers "Save as project") and names it after the file.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ImportZone } from './ImportExport'
import { ioApi } from '../api/io'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/io', () => ({ ioApi: { importNetcdf: vi.fn(), importExcel: vi.fn(), importCsv: vi.fn(), importMatpower: vi.fn() } }))
vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn().mockResolvedValue({ unsaved: false, depth: 0 }) } }))

afterEach(() => { cleanup(); vi.clearAllMocks(); useUIStore.setState({ currentProject: null, unsavedNetwork: null }) })

describe('raw network import', () => {
  it('names the loaded network after its file and marks it as not in a project', async () => {
    vi.mocked(ioApi.importNetcdf).mockResolvedValue({ buses: 3 } as never)
    useUIStore.setState({ currentProject: null, unsavedNetwork: null })
    const qc = new QueryClient()
    const { container } = render(<QueryClientProvider client={qc}><ImportZone onSuccess={() => {}} /></QueryClientProvider>)
    const input = container.querySelector('input[type=file]') as HTMLInputElement
    await userEvent.setup().upload(input, new File(['x'], 'my_grid.nc'))
    await waitFor(() => expect(useUIStore.getState().unsavedNetwork).toBe('my_grid'))
    expect(useUIStore.getState().projectName).toBe('my_grid')
  })
})
