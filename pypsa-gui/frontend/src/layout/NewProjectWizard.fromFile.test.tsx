// Excel input workbook S3: the wizard's From file tab is Expert's one entry for
// importing a workbook as a new project. An .xlsx closes the wizard and hands the
// file to the workbook dialog, which previews before anything is created.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import NewProjectWizard from './NewProjectWizard'
import { projectsApi } from '../api/projects'
import { ioApi } from '../api/io'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))
vi.mock('../api/gridspine', () => ({ gridspineApi: { createStudy: vi.fn() } }))
vi.mock('../api/io', () => ({ ioApi: { importNetcdf: vi.fn() } }))

afterEach(() => { vi.clearAllMocks(); useUIStore.setState({ workbookImportRequest: null }) })

describe('From file accepts an input workbook', () => {
  it('an .xlsx closes the wizard and opens the workbook dialog with that file', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([])
    const onClose = vi.fn()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}><MemoryRouter>
        <NewProjectWizard existingProjects={[]} onConfirm={() => {}} onClose={onClose} isPending={false} initialTab="file" />
      </MemoryRouter></QueryClientProvider>,
    )
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    expect(input.accept.split(',')).toContain('.xlsx')
    const file = new File(['x'], 'Hub.xlsx')
    await userEvent.upload(input, file)
    expect(onClose).toHaveBeenCalled()
    expect(useUIStore.getState().workbookImportRequest?.file).toBe(file)
    expect(projectsApi.importBundle).not.toHaveBeenCalled()
    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
  })
})
