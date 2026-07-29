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

afterEach(async () => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
  delete (window as unknown as Record<string, unknown>).showSaveFilePicker
  // `_bundleHandles` is MODULE state and outlives the test that filled it.
  // Leaving it set made one test's assertion depend on another test having
  // run first, which is how a mutation escaped a whole review round.
  const { forgetBundleLocation } = await import('../utils/projectActions')
  forgetBundleLocation('Demo')
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
  // Each pick returns a DISTINCT handle, and every write records which one it
  // landed in. Counting picker calls is not enough: with `skipCache` dropped,
  // the export overwrites the cache with its own handle and a later Save is
  // silently redirected there — without ever showing the picker again. The
  // call count is identical in both worlds; only the destination differs.
  const writes: string[] = []
  let nth = 0
  const picker = vi.fn().mockImplementation(async () => {
    const id = `handle#${++nth}`
    return {
      createWritable: async () => ({
        write: async () => { writes.push(id) },
        close: async () => {},
      }),
      queryPermission: async () => 'granted',
    }
  })
  ;(window as unknown as Record<string, unknown>).showSaveFilePicker = picker
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))
  const { downloadProjectBundle } = await import('../utils/projectActions')

  // SEED the cache the way a Save does, so `askLocation` has something to
  // override. Without this the cache is empty, the export takes the picker
  // path regardless, and the assertion passes whether or not `askLocation` is
  // set — which is how this test previously killed its mutation only by
  // accident, via state an unrelated earlier test left behind.
  await downloadProjectBundle('Demo')
  await downloadProjectBundle('Demo')
  expect(picker, 'the seed did not cache a handle — the premise is broken')
    .toHaveBeenCalledTimes(1)
  expect(writes).toEqual(['handle#1', 'handle#1'])

  renderPanel()
  await userEvent.click(exportButton())
  await waitFor(() =>
    expect(picker, 'the export reused the Save destination instead of asking')
      .toHaveBeenCalledTimes(2))
  await waitFor(() => expect(writes).toHaveLength(3))
  expect(writes[2], 'the export did not write to its own chosen file').toBe('handle#2')

  // The export must NOT have replaced the cache: an ordinary Save still lands
  // in the file Save has always used, not in the one the export just created.
  await waitFor(() => expect(isExportDisabled()).toBe(false))
  await downloadProjectBundle('Demo')
  expect(writes[3], 'the export poisoned the cache — Save now overwrites the export')
    .toBe('handle#1')
})

it('saves NOTHING when the server refuses', async () => {
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

it('records the failure in the log, not only in a toast', async () => {
  // `skipErrorToast` suppresses the interceptor's `appLog` line as well as its
  // toast — one flag gates both. Toasts dismiss themselves; the Log tab is the
  // record, and the other two bundle-export paths both write one.
  // Asserted through the store the log actually lands in, not a spy.
  const { useSimulationStore } = await import('../store/simulationStore')
  useSimulationStore.setState({ logLines: [] })
  vi.mocked(projectsApi.downloadBundle).mockRejectedValue(new Error('boom'))

  await clickExport()

  await waitFor(() =>
    expect(
      useSimulationStore.getState().logLines.some(l => l.includes('Export bundle')),
    ).toBe(true),
  )
})

it("reports a structured FastAPI detail, not just a plain string one", async () => {
  // The mutation this kills: gating on `typeof detail === 'string'`. FastAPI's
  // 422 returns `detail` as a LIST of {loc, msg}, and several project routes
  // raise object details — a string-only gate degrades all of them back to the
  // opaque axios message, which is the bug this helper exists to fix.
  vi.mocked(projectsApi.downloadBundle).mockRejectedValue({
    message: 'Request failed with status code 422',
    response: {
      status: 422,
      data: new Blob([JSON.stringify({
        detail: [{ loc: ['body', 'name'], msg: 'field required' }],
      })]),
    },
  })

  await clickExport()

  await waitFor(() =>
    expect(errors.some(m => m.includes('field required'))).toBe(true),
  )
})

it("reports the server's reason, not axios's status line", async () => {
  // The reason `bundleErrorMessage` exists. `downloadBundle` sets
  // `responseType: 'blob'`, so the error body arrives as a BLOB and every
  // ordinary `data?.detail` lookup — including the interceptor's — misses it.
  // Rejecting with a bare Error (which the test above does) never reaches that
  // code at all: it has no `.response`, so the helper falls straight through
  // to `e.message` and the whole decode path stayed unexercised.
  vi.mocked(projectsApi.downloadBundle).mockRejectedValue({
    message: 'Request failed with status code 404',
    response: {
      status: 404,
      data: new Blob([JSON.stringify({ detail: "Project 'Demo' not found" })]),
    },
  })

  await clickExport()

  await waitFor(() =>
    expect(errors.some(m => m.includes("Project 'Demo' not found"))).toBe(true),
  )
})

it('re-enables the button after a failed export', async () => {
  // Otherwise a single failure disables Export for the life of the mount and
  // the user has to navigate away and back. `setExporting(false)` lives in a
  // `finally` for this reason; moving it to the success path passed every
  // other test in this file.
  vi.mocked(projectsApi.downloadBundle).mockRejectedValue(new Error('nope'))

  await clickExport()

  await waitFor(() => expect(errors.some(m => m.includes('Export failed'))).toBe(true))
  expect(isExportDisabled()).toBe(false)
})

it('still saves the file when the save dialog cannot be shown', async () => {
  // `showSaveFilePicker` needs transient user activation (~5 s in Chrome) and
  // is called AFTER the bundle fetch, which for a large project outlives it.
  // The picker then throws SecurityError — not AbortError — and the bytes are
  // already in hand. Discarding them left the user with a browser-internals
  // string, no file, and a retry that hits the same wall every time.
  const denied = Object.assign(new Error('Must be handling a user gesture'), {
    name: 'SecurityError',
  })
  ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
    .fn()
    .mockRejectedValue(denied)
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  await clickExport()

  await waitFor(() => expect(clicks).toHaveLength(1))
  expect(clicks[0].download).toBe('Demo.pypsaproj.zip')
  expect(errors.filter(m => m.includes('Export failed'))).toHaveLength(0)
  // The RESULT the fallback returns, not just its side effect. Returning
  // 'cancelled' instead makes the export silently show no toast at all in
  // Firefox, Safari and the desktop shell — the platforms this path exists
  // for — and returning 'reused' makes the two sibling callers claim
  // "updated saved file" while the bytes went to the Downloads folder.
  // Both mutations passed all 183 tests before this assertion.
  expect(successes.some(m => m.includes('Exported Demo.pypsaproj.zip'))).toBe(true)
})

it('does NOT claim success when the file cannot be written', async () => {
  // A quota or permission failure during the write leaves a 0-byte file at the
  // location the user chose. Falling back to a download and reporting
  // "Exported" would hide that behind a second doomed write — so the picker
  // and the write get separate try blocks, and only the picker gets a
  // fallback.
  const quota = Object.assign(new Error('The quota has been exceeded.'), {
    name: 'QuotaExceededError',
  })
  ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
    .fn()
    .mockResolvedValue({
      createWritable: async () => ({
        write: async () => { throw quota },
        close: async () => {},
      }),
      queryPermission: async () => 'granted',
    })
  vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))

  await clickExport()

  await waitFor(() =>
    expect(errors.some(m => m.includes('quota'))).toBe(true),
  )
  expect(successes.filter(m => m.includes('Exported'))).toHaveLength(0)
  expect(clicks, 'a second copy was downloaded to hide the failed write')
    .toHaveLength(0)
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
