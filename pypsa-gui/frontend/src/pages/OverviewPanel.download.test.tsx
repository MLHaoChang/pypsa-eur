// Permanent regression coverage for the bundle export (phase 2a, Task 6).
//
// Two independent reviewers found the same defect in the first version of
// this, and it is the reason this file asserts on FAILURE as hard as on
// success: exporting through a bare `<a href download>` pointed at the API
// saves whatever the server returns. `GET /api/projects/{name}/bundle` answers
// 401/403/404 with a JSON body and no Content-Disposition, and an anchor
// cannot see a status code — so an expired session or a project renamed in
// another tab wrote a ~40-byte file called `MyProject.pypsaproj.zip`
// containing `{"detail":"Project not found"}`. No toast, no console error.
// Indistinguishable from success until the user tries to open the zip.
//
// The export therefore goes through `downloadProjectBundle`, which fetches via
// axios (so the client's error handling and auth redirect apply) and only then
// saves the blob. That is also what AppHeader and the Sidebar modal already
// use — one implementation for one artifact.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import OverviewPanel from './OverviewPanel'

vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return { ...actual, projectsApi: { ...actual.projectsApi, downloadBundle: vi.fn() } }
})

type Click = { href: string; download: string | null; target: string | null }

let clicks: Click[]

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <OverviewPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  clicks = []
  useUIStore.setState({ currentProject: 'Demo' })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicks.push({
      href: this.getAttribute('href') ?? '',
      download: this.getAttribute('download'),
      target: this.getAttribute('target'),
    })
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

async function clickExport() {
  renderPanel()
  await userEvent.click(screen.getByRole('button', { name: /export bundle/i }))
}

it('fetches the bundle before saving anything', async () => {
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(
    new Blob(['zip bytes'], { type: 'application/zip' }),
  )

  await clickExport()

  await waitFor(() => expect(projectsApi.downloadBundle).toHaveBeenCalledWith('Demo'))
  await waitFor(() => expect(clicks).toHaveLength(1))
  expect(clicks[0].download).toBe('Demo.pypsaproj.zip')
  // A blob URL, not the API URL: the bytes are in hand and already known good.
  expect(clicks[0].href.startsWith('blob:')).toBe(true)
})

it('saves NOTHING when the server refuses, and says so', async () => {
  // THE regression. Before this, a 404 body was written to disk as a .zip.
  const err = vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.mocked(projectsApi.downloadBundle).mockRejectedValue(
    new Error('Request failed with status code 404'),
  )

  await clickExport()

  await waitFor(() => expect(err).toHaveBeenCalled())
  expect(clicks, 'an error body was saved to disk as a bundle').toHaveLength(0)
})

it('does not use window.open, which silently does nothing in the desktop shell', async () => {
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  await clickExport()

  await waitFor(() => expect(projectsApi.downloadBundle).toHaveBeenCalled())
  expect(open).not.toHaveBeenCalled()
})

it('sets no target, so the system browser never takes the download', async () => {
  // target=_blank is WKNavigationTypeLinkActivated, which reaches pywebview's
  // OPEN_EXTERNAL_LINKS_IN_BROWSER (default TRUE) and hands the URL to Safari.
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  await clickExport()

  await waitFor(() => expect(clicks).toHaveLength(1))
  expect(clicks[0].target).toBeNull()
})
