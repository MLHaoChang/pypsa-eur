// Permanent regression coverage for the bundle export (phase 2a, Task 6).
//
// Every assertion here exists because a review found the export doing
// something wrong, and each one is written to fail for its OWN reason:
//
//  1. A bare `<a href download>` at the API URL saved 401/403/404 JSON bodies
//     to disk as a valid-looking `.pypsaproj.zip` — an anchor cannot see a
//     status code. So: fetch first, save second.
//  2. Fetching through `downloadProjectBundle` with DEFAULT options reused the
//     Save-destination handle cache. Export then silently rewrote the file the
//     user had chosen for Save, with no picker; and an Export destination got
//     cached, so a later Save overwrote the user's archived copy. So:
//     `askLocation: true, skipCache: true` — the options "Save a Copy" passes,
//     for the same reason.
//  3. `expect(toast.error).toHaveBeenCalled()` passed with the export's error
//     handling DELETED, because this page's other queries fail in jsdom and
//     the axios interceptor fires ~18 ambient `toast.error('Network Error')`
//     calls before the click. Assertions here name their message.
//  4. Double-clicking ran two exports: two save panels, two server-side zip
//     builds, two success toasts.
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
let errors: string[]
let successes: string[]

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
  errors = []
  successes = []
  // `vi.restoreAllMocks()` restores `vi.spyOn` spies but does NOT reset a
  // `vi.fn()` created inside a `vi.mock` factory — its call count accumulates
  // across every test in the file. The double-click test read 7 calls instead
  // of 1 before this line existed.
  vi.mocked(projectsApi.downloadBundle).mockReset()
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
  vi.spyOn(toast, 'error').mockImplementation((m) => { errors.push(String(m)); return '' })
  vi.spyOn(toast, 'success').mockImplementation((m) => { successes.push(String(m)); return '' })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
  delete (window as unknown as Record<string, unknown>).showSaveFilePicker
})

function exportButton() {
  return screen.getByRole('button', { name: /export bundle/i })
}

// Plain DOM, not `toBeDisabled` — this suite does not load jest-dom matchers.
function isExportDisabled() {
  return (exportButton() as HTMLButtonElement).disabled
}

async function clickExport() {
  renderPanel()
  await userEvent.click(exportButton())
}

it('fetches the bundle before saving anything', async () => {
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(
    new Blob(['zip bytes'], { type: 'application/zip' }),
  )

  await clickExport()

  await waitFor(() => expect(clicks).toHaveLength(1))
  expect(clicks[0].download).toBe('Demo.pypsaproj.zip')
  // A blob URL, not the API URL: the bytes are in hand and already known good.
  expect(clicks[0].href.startsWith('blob:')).toBe(true)
})

it('always asks where to put it, and never poisons the Save destination', async () => {
  // Asserted through the REAL helper's observable behaviour, not by reading
  // back an options object.
  //
  //   askLocation:true -> the picker is consulted on EVERY export, so an
  //                       export cannot silently rewrite the file the user
  //                       picked for Save.
  //   skipCache:true   -> the export's destination is NOT remembered, so a
  //                       later Save cannot silently overwrite the archive
  //                       the user just exported.
  const picker = vi.fn().mockResolvedValue({
    createWritable: async () => ({ write: async () => {}, close: async () => {} }),
    queryPermission: async () => 'granted',
  })
  ;(window as unknown as Record<string, unknown>).showSaveFilePicker = picker
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  renderPanel()
  await userEvent.click(exportButton())
  await waitFor(() => expect(picker).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(isExportDisabled()).toBe(false))
  await userEvent.click(exportButton())
  await waitFor(() => expect(picker).toHaveBeenCalledTimes(2))

  // And nothing was cached under this project: a default-options call — which
  // is what an ordinary Save does — still has to ask.
  const { downloadProjectBundle } = await import('../utils/projectActions')
  await downloadProjectBundle('Demo')
  expect(picker, 'the export cached its handle; a later Save would overwrite it')
    .toHaveBeenCalledTimes(3)
})

it('saves NOTHING when the server refuses, and says WHY', async () => {
  vi.mocked(projectsApi.downloadBundle).mockRejectedValue(
    new Error('Request failed with status code 404'),
  )

  await clickExport()

  // Named, so the page's ~18 ambient "Network Error" toasts cannot satisfy it.
  await waitFor(() =>
    expect(errors.some(m => m.includes('Export failed'))).toBe(true),
  )
  expect(clicks, 'an error body was saved to disk as a bundle').toHaveLength(0)
  expect(successes.filter(m => m.includes('Export'))).toHaveLength(0)
})

it('claims nothing when the user cancels the save dialog', async () => {
  // Exercises the REAL helper's picker branch by giving jsdom the API it
  // lacks. Without this, `downloadProjectBundle` can only ever return
  // 'download' in tests and the cancellation branch is unreachable.
  const abort = Object.assign(new Error('The user aborted a request.'), {
    name: 'AbortError',
  })
  ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
    .fn()
    .mockRejectedValue(abort)
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  await clickExport()

  await waitFor(() => expect(projectsApi.downloadBundle).toHaveBeenCalled())
  expect(successes.filter(m => m.includes('Export'))).toHaveLength(0)
  expect(errors.filter(m => m.includes('Export failed'))).toHaveLength(0)
})

it('cannot be double-clicked into two exports', async () => {
  let release: (b: Blob) => void = () => {}
  vi.mocked(projectsApi.downloadBundle).mockReturnValue(
    new Promise<Blob>((res) => { release = res }),
  )
  renderPanel()

  await userEvent.click(exportButton())
  await waitFor(() => expect(isExportDisabled()).toBe(true))
  await userEvent.click(exportButton())

  expect(projectsApi.downloadBundle).toHaveBeenCalledTimes(1)
  release(new Blob(['zip']))
  await waitFor(() => expect(isExportDisabled()).toBe(false))
})

it('does not use window.open, which silently does nothing in the desktop shell', async () => {
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  await clickExport()

  await waitFor(() => expect(clicks).toHaveLength(1))
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
