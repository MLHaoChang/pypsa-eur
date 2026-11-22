// project-io 04 Slice B: Import file accepts every registry extension and hands model files
// (and project files) to the preview dialog; nothing is created from the tab itself.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import NewProjectWizard from './NewProjectWizard'
import { projectsApi } from '../api/projects'
import { modelImportApi } from '../api/modelImport'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))
vi.mock('../api/gridspine', () => ({ gridspineApi: { createStudy: vi.fn() } }))
vi.mock('../api/io', () => ({ ioApi: { importNetcdf: vi.fn(), importMatpower: vi.fn() } }))
vi.mock('../api/modelImport', async (orig) => {
  const actual = await orig<typeof import('../api/modelImport')>()
  return { ...actual, modelImportApi: { formats: vi.fn(), preview: vi.fn(), import: vi.fn() } }
})

afterEach(() => { vi.clearAllMocks(); useUIStore.setState({ modelImportRequest: null }) })

async function mountFileTab() {
  vi.mocked(projectsApi.list).mockResolvedValue([])
  vi.mocked(modelImportApi.formats).mockResolvedValue([
    { id: 'psse', label: 'PSS/E RAW', extensions: ['.raw', '.dyr'] },
    { id: 'plexos', label: 'PLEXOS', extensions: ['.xml'] },
  ])
  const onClose = vi.fn()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}><MemoryRouter>
      <NewProjectWizard existingProjects={[]} onConfirm={() => {}} onClose={onClose} isPending={false} initialTab="file" />
    </MemoryRouter></QueryClientProvider>,
  )
  await screen.findByText(/PSS\/E RAW/)
  return { onClose, input: document.querySelector('input[type=file]') as HTMLInputElement }
}

describe('Import file takes model files', () => {
  it('accepts the registry extensions and .m, and a .raw opens the preview dialog', async () => {
    const { onClose, input } = await mountFileTab()
    expect(input.accept.split(',')).toEqual(expect.arrayContaining(['.raw', '.dyr', '.xml', '.m', '.nc']))
    const file = new File(['x'], 'case14.raw')
    await userEvent.upload(input, file)
    expect(onClose).toHaveBeenCalled()
    expect(useUIStore.getState().modelImportRequest?.files).toEqual([file])
    expect(projectsApi.importBundle).not.toHaveBeenCalled()
  })

  it('a project file goes to the same preview, not straight into the app', async () => {
    const { input } = await mountFileTab()
    const file = new File(['x'], 'Grid.pypsaproj.zip')
    await userEvent.upload(input, file)
    expect(useUIStore.getState().modelImportRequest?.files).toEqual([file])
    expect(projectsApi.importBundle).not.toHaveBeenCalled()
  })

  it('a model in several files (a .raw and its .dyr) goes to the preview as one set', async () => {
    const { input } = await mountFileTab()
    const files = [new File(['x'], 'case.raw'), new File(['x'], 'case.dyr')]
    await userEvent.upload(input, files)
    expect(useUIStore.getState().modelImportRequest?.files).toEqual(files)
  })
})
