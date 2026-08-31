/**
 * The pane hosts TWO independently-gated surfaces now (Task 15):
 * local-settings (`state == null` hides the desktop-only key/diagnostics
 * body — this build isn't the desktop app) and AssistantModelSettings
 * (hides itself when `/chat/settings/llm` is unreachable — a 403 for an
 * ordinary member, or a 404). Either can be present without the other: a web
 * deployment 404s local-settings but a super-admin there still gets the
 * assistant-model section: see `hooks/useLLMSettings.ts`.
 *
 * The loading branch is checked BEFORE the null branch so a slow response
 * never flashes the "hidden" state before content appears.
 *
 * Guards only THIS component in isolation — it renders `<LocalSettings />`
 * directly and never exercises `useCommands`/`CommandPalette`, so it says
 * nothing about the ⌘K entry (`act-settings`) built on the same
 * `useLocalSettingsAvailable` gate. That entry's own visibility test lives in
 * `components/CommandPalette.test.tsx`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LocalSettings from './LocalSettings'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'
import { fetchLLMSettingsOrNull, type LLMSettingsPayload } from '../api/llmSettings'

// Partial mock: keep the real keyFieldPlaceholder/probeMessage/etc (the pane
// imports and calls them), stub only the network call the hook wraps.
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn(),
}))

// Same partial-mock shape, for the AssistantModelSettings section this pane
// now hosts. Defaults (below) resolve null so every pre-existing test in
// this file keeps its old scope — only the web-deployment test opts into a
// reachable payload.
vi.mock('../api/llmSettings', async (orig) => ({
  ...(await orig<typeof import('../api/llmSettings')>()),
  fetchLLMSettingsOrNull: vi.fn(),
}))

const STATE: LocalSettingsState = { key_set: true, key_hint: '7f3a', log_path: '/tmp/app.log' }

const LLM_PAYLOAD: LLMSettingsPayload = {
  active_profile_id: 'anthropic-sonnet',
  profiles: [{
    id: 'anthropic-sonnet', label: 'Claude Sonnet', preset: 'anthropic-sonnet',
    wire: 'anthropic', base_url: null, model: 'claude-sonnet-5',
    tools: true, vision: true, auth: 'bearer', fallback_model: null, max_output_tokens: null,
    key_required: true, key_present: false, key_hint: null,
  }],
  presets: [],
}

const renderPane = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><LocalSettings /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
})

describe('desktop-vs-web visibility', () => {
  it('renders nothing at all once BOTH surfaces resolve to unreachable', async () => {
    // Catches: the `if (state == null) return null` guard being removed,
    // inverted, or bypassed for the local-settings body — this file's own
    // render path only, per the header above. It does not exercise ⌘K's
    // act-settings entry; see components/CommandPalette.test.tsx for that
    // door.
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
    const { container } = renderPane()
    await waitFor(() => expect(container.firstChild).toBeNull())
    expect(screen.queryByText('Anthropic API key')).toBeNull()
  })

  it('on web (local-settings 404s) still renders the assistant section when llm-settings is reachable', async () => {
    // The THIRD sanctioned edit to this pinned test (Task 15): local-settings
    // 404ing no longer means "this pane renders nothing" — the two surfaces
    // are gated independently. A super-admin on a web deployment gets the
    // assistant-model section with none of the desktop-only key/diagnostics
    // body.
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(LLM_PAYLOAD)
    renderPane()

    expect(await screen.findByText('Claude Sonnet')).toBeTruthy()
    expect(screen.queryByText('Anthropic API key')).toBeNull()
    expect(screen.queryByText('Diagnostics')).toBeNull()
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
