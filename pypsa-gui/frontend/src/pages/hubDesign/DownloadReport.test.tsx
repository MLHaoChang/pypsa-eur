// Guided "Download report (.docx)" (owner decision A): build, render, hand to the browser.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import DownloadReport from './DownloadReport'

const api = vi.hoisted(() => ({ create: vi.fn(), exp: vi.fn() }))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, createEvidenceOnlyReport: api.create, exportReport: api.exp }
})

const mount = () => render(<QueryClientProvider client={new QueryClient()}><DownloadReport project="Hub A" /></QueryClientProvider>)
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks() })

describe('DownloadReport', () => {
  it('builds the evidence report, exports it as .docx and downloads the file', async () => {
    api.create.mockResolvedValue({ report_id: 'r1' })
    api.exp.mockResolvedValue({ file_id: 'f1', filename: 'report_r1_v1.docx' })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Download report (.docx)' }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(api.create).toHaveBeenCalledWith('Hub A')
    expect(api.exp).toHaveBeenCalledWith('Hub A', 'r1', { format: 'docx' })
  })

  it('shows a refusal instead of failing silently', async () => {
    api.create.mockRejectedValue(new Error('Project is locked by another session'))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Download report (.docx)' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/locked|could not be built/i)
  })
})
