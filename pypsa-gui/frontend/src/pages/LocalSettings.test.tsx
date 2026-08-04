/**
 * The pane's one job: `state == null` hides everything (this build isn't the
 * desktop app), a real state object renders it, and the loading branch is
 * checked BEFORE the null branch so a slow response never flashes the
 * "hidden" state before content appears.
 *
 * This property had only ever been verified by reading the source — the
 * CommandPalette entry it should have caught (⌘K → Settings opening a blank
 * half-width panel on a web build) shipped anyway, because nothing asserted
 * it. See round-1 review Finding 1 / the "THIRD ITEM" follow-up.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LocalSettings from './LocalSettings'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'

// Partial mock: keep the real keyFieldPlaceholder/probeMessage/etc (the pane
// imports and calls them), stub only the network call the hook wraps.
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn(),
}))

const STATE: LocalSettingsState = { key_set: true, key_hint: '7f3a', log_path: '/tmp/app.log' }

const renderPane = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><LocalSettings /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('desktop-vs-web visibility', () => {
  it('renders nothing once resolved to null (web deployment: routes 404)', async () => {
    // Catches: the `if (state == null) return null` guard being removed,
    // inverted, or bypassed — the exact gap ⌘K's act-settings entry had
    // before Finding 1's fix, reached through a different door than Sidebar.
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    const { container } = renderPane()
    await waitFor(() => expect(container.firstChild).toBeNull())
    expect(screen.queryByText('Anthropic API key')).toBeNull()
  })

  it('renders the pane once resolved to a real state object (desktop app)', async () => {
    // Catches: the null-check swallowing the non-null case too (e.g. a `!=`
    // typo'd to `==`, or the guard firing unconditionally) — the pane must
    // still actually render when the routes DO answer.
    vi.mocked(fetchLocalSettings).mockResolvedValue(STATE)
    renderPane()
    expect(await screen.findByText('Anthropic API key')).toBeTruthy()
  })

  it('shows the loading state before a pending fetch resolves — no empty-state flash', async () => {
    // Catches: checking `state == null` BEFORE `isLoading`. `data` is
    // `undefined` while a query is in flight, and `undefined == null` is
    // true — so without the loading branch running first, a pending fetch
    // would render exactly like the permanent web-mode hide (nothing at
    // all), indistinguishable from "no nav entry" until the real answer
    // arrives. Users would see a blank panel that only sometimes fills in.
    let resolve!: (v: LocalSettingsState | null) => void
    const pending = new Promise<LocalSettingsState | null>(r => { resolve = r })
    vi.mocked(fetchLocalSettings).mockReturnValue(pending)
    const { container } = renderPane()
    expect(await screen.findByText('Loading…')).toBeTruthy()
    // Still showing "loading", not silently empty, right up to resolution.
    expect(container.firstChild).not.toBeNull()
    resolve(null)
    await waitFor(() => expect(screen.queryByText('Loading…')).toBeNull())
  })
})
