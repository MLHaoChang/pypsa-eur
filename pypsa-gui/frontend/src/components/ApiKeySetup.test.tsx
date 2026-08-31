// U-1 — the in-app API key form.
//
// The packaged app ships no `backend/.env` on purpose, so "API key missing"
// was every user's entire experience of the assistant: a red banner with no
// next step. These tests pin the three states that banner can now be in, and
// the one it must never be in.
//
// The route is super-admin only (one ANTHROPIC_API_KEY is shared by every
// organisation), and the 403 an ordinary member gets is an expected STATE
// rather than a failure — so the "ask an administrator" case is a first-class
// assertion here, not an error path.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import {
  deleteApiKeySettings,
  getApiKeySettings,
  getChatHealth,
  putApiKeySettings,
  type ApiKeySettings,
  type ChatHealth,
} from '../api/chat'
import { useUIStore } from '../store/uiStore'
import ApiKeySetup from './ApiKeySetup'

// FULL-FACTORY mock (no importOriginal): every function ApiKeySetup calls at
// runtime MUST be listed here, or it resolves to `undefined` and calling it
// throws. `getChatHealth` joined this list in Task 15 — ApiKeySetup now
// reads it to decide inline-form vs. deep-link — and the default resolution
// below (a builtin Anthropic active profile) is what keeps every
// pre-existing test in this file on its original path unmodified.
vi.mock('../api/chat', () => ({
  getApiKeySettings: vi.fn(),
  putApiKeySettings: vi.fn(),
  deleteApiKeySettings: vi.fn(),
  getChatHealth: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const STORAGE_PATH = '/Users/example/Library/Application Support/PyPSA Studio/user.env'

function settings(over: Partial<ApiKeySettings> = {}): ApiKeySettings {
  return {
    configured: false,
    source: null,
    hint: null,
    overridden_by_environment: false,
    storage_path: STORAGE_PATH,
    ...over,
  }
}

function health(over: Partial<ChatHealth> = {}): ChatHealth {
  return {
    ok: true,
    anthropic_api_key_present: false,
    default_model: 'claude-sonnet-5',
    confirmation_ttl_seconds: 300,
    active_profile: { id: 'anthropic-sonnet', label: 'Claude Sonnet', wire: 'anthropic' },
    chat_ready: false,
    ...over,
  }
}

function forbidden() {
  return Object.assign(new Error('Forbidden'), { response: { status: 403 } })
}

afterEach(() => cleanup())
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getChatHealth).mockResolvedValue(health())
})

function renderSetup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ApiKeySetup />
    </QueryClientProvider>,
  )
}

describe('ApiKeySetup', () => {
  it('offers a form when no key is configured', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(settings())
    renderSetup()

    expect(await screen.findByTestId('chat-api-key-input')).toBeTruthy()
    // The user is told where the key lands. It is their machine and their
    // credential; a silent write to an unnamed location is not acceptable.
    expect(screen.getByText(new RegExp(STORAGE_PATH.replace(/[/\\]/g, '\\$&')))).toBeTruthy()
  })

  it('saves the typed key and refreshes what depends on it', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(settings())
    vi.mocked(putApiKeySettings).mockResolvedValue(
      settings({ configured: true, source: 'settings', hint: '…wxyz' }),
    )
    renderSetup()

    const user = userEvent.setup()
    await user.type(await screen.findByTestId('chat-api-key-input'), 'sk-ant-typed-key')
    await user.click(screen.getByTestId('chat-api-key-save'))

    await waitFor(() => expect(putApiKeySettings).toHaveBeenCalledWith('sk-ant-typed-key'))
  })

  it('does not submit an empty or whitespace-only value', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(settings())
    renderSetup()

    const user = userEvent.setup()
    await user.type(await screen.findByTestId('chat-api-key-input'), '   ')
    const save = screen.getByTestId('chat-api-key-save') as HTMLButtonElement
    expect(save.disabled).toBe(true)
    await user.click(save)
    expect(putApiKeySettings).not.toHaveBeenCalled()
  })

  it('masks the field so a pasted key is not left legible on screen', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(settings())
    renderSetup()
    const input = (await screen.findByTestId('chat-api-key-input')) as HTMLInputElement
    expect(input.type).toBe('password')
  })

  it('shows the hint and a Remove control once a key is stored', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(
      settings({ configured: true, source: 'settings', hint: '…wxyz' }),
    )
    vi.mocked(deleteApiKeySettings).mockResolvedValue(settings())
    renderSetup()

    expect(await screen.findByText(/ending …wxyz/)).toBeTruthy()
    const user = userEvent.setup()
    await user.click(screen.getByTestId('chat-api-key-forget'))
    await waitFor(() => expect(deleteApiKeySettings).toHaveBeenCalled())
  })

  it('offers no Remove control for a key that came from the environment', async () => {
    // Removing it would rewrite a file that is not the source of the live
    // value — the button would appear to work and change nothing.
    vi.mocked(getApiKeySettings).mockResolvedValue(
      settings({ configured: true, source: 'environment', hint: '…wxyz' }),
    )
    renderSetup()

    await screen.findByTestId('chat-api-key-setup')
    expect(screen.queryByTestId('chat-api-key-forget')).toBeNull()
  })

  it('warns when a shell-set variable is masking the saved key', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(
      settings({
        configured: true,
        source: 'environment',
        hint: '…shel',
        overridden_by_environment: true,
      }),
    )
    renderSetup()

    // Without this the user saves a key, sees no change, and has no way to
    // find out why.
    expect(await screen.findByTestId('chat-api-key-masked')).toBeTruthy()
  })

  it('tells a non-super-admin who to ask instead of showing a form', async () => {
    vi.mocked(getApiKeySettings).mockRejectedValue(forbidden())
    renderSetup()

    expect(await screen.findByTestId('chat-api-key-forbidden')).toBeTruthy()
    expect(screen.queryByTestId('chat-api-key-input')).toBeNull()
  })

  // Task 15 — ApiKeySetup generalisation: the built-in Anthropic profile
  // keeps the verbatim inline form (every test above this point exercises
  // that path via the default `health()` mock); any OTHER active profile —
  // a custom OpenAI/local endpoint the admin switched to — gets told which
  // profile is short a key and pointed at Settings instead, because that
  // profile's key has nothing to do with `/chat/settings/api-key`.
  describe('a non-builtin active profile', () => {
    it('names the active profile and offers no inline Anthropic form', async () => {
      vi.mocked(getChatHealth).mockResolvedValue(
        health({ active_profile: { id: 'ollama-local', label: 'Local Ollama', wire: 'openai' } }),
      )
      renderSetup()

      expect(await screen.findByText(/Local Ollama/)).toBeTruthy()
      expect(screen.queryByTestId('chat-api-key-input')).toBeNull()
      // getApiKeySettings is the Anthropic-specific route; it must not even
      // be consulted to decide this branch's copy.
    })

    it('deep-links to Settings, requesting the assistant-model section', async () => {
      vi.mocked(getChatHealth).mockResolvedValue(
        health({ active_profile: { id: 'ollama-local', label: 'Local Ollama', wire: 'openai' } }),
      )
      renderSetup()
      const user = userEvent.setup()

      useUIStore.setState({ activeSlidePanel: null, settingsSectionRequest: null })
      await user.click(await screen.findByTestId('chat-api-key-open-settings'))

      expect(useUIStore.getState().activeSlidePanel).toBe('settings')
      expect(useUIStore.getState().settingsSectionRequest).toBe('assistant-model')
    })
  })
})
