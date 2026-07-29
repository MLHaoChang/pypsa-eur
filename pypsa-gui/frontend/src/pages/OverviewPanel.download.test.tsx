// Permanent regression coverage for the bundle export (phase 2a, Task 6).
//
// `saveFromUrl` having its own unit tests does not prove this button uses it —
// that is the gap the whole task exists inside, so it gets a real render and a
// real click rather than an assertion about a helper nobody may be calling.
//
// What this catches: `window.open(url, '_blank')` returns null and downloads
// nothing inside pywebview, measured on a real cocoa WKWebView
// (backend/smoke/audit_downloads.py). The button would look like it worked and
// do nothing at all — no error, no file, no message.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import OverviewPanel from './OverviewPanel'

type Click = { href: string; download: string | null; target: string | null }

let clicks: Click[]

function renderPanel() {
  // retry:false so the unmocked API calls fail once and the page renders its
  // empty/default state instead of hanging on retries. The header — which is
  // all this test touches — does not depend on any of that data.
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

it('exports the bundle through an anchor, not window.open', async () => {
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  renderPanel()

  await userEvent.click(screen.getByRole('button', { name: /export bundle/i }))

  expect(open).not.toHaveBeenCalled()
  expect(clicks).toHaveLength(1)
  expect(clicks[0].href).toContain('/api/projects/Demo/bundle')
  expect(clicks[0].download).toBeTruthy()
})

it('encodes a project name that would otherwise break the URL', async () => {
  // The original `window.open` call encoded the name, and dropping that while
  // rewriting the line is the easy mistake: a project called "A/B" would
  // request a different path entirely and 404.
  useUIStore.setState({ currentProject: 'A/B & C' })
  renderPanel()

  await userEvent.click(screen.getByRole('button', { name: /export bundle/i }))

  expect(clicks[0].href).toContain(encodeURIComponent('A/B & C'))
})

it('does not open the bundle in the system browser', async () => {
  // target=_blank is WKNavigationTypeLinkActivated, which reaches pywebview's
  // OPEN_EXTERNAL_LINKS_IN_BROWSER (default TRUE) and hands the URL to Safari.
  // The zip would land in the real ~/Downloads and the app would show no sign.
  renderPanel()

  await userEvent.click(screen.getByRole('button', { name: /export bundle/i }))

  expect(clicks[0].target).toBeNull()
})
