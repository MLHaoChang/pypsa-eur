// Task 15 — the super-admin surface for LLM connection profiles: pick the
// active one, manage per-profile keys, test a connection, add from a preset
// or a custom endpoint, delete. Security-load-bearing: the API returns only
// `key_present`/`key_hint`, NEVER a key value, so a stored key must never
// appear in this component's inputs — that is asserted explicitly below,
// not just assumed from the API shape.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import {
  deleteLLMProfile,
  deleteLLMProfileKey,
  fetchLLMSettingsOrNull,
  postLLMActive,
  postLLMTest,
  putLLMProfile,
  putLLMProfileKey,
  type LLMSettingsPayload,
} from '../api/llmSettings'
import { useUIStore } from '../store/uiStore'
import AssistantModelSettings from './AssistantModelSettings'

vi.mock('../api/llmSettings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/llmSettings')>()
  return {
    ...actual,
    fetchLLMSettingsOrNull: vi.fn(),
    postLLMActive: vi.fn(),
    putLLMProfileKey: vi.fn(),
    deleteLLMProfileKey: vi.fn(),
    postLLMTest: vi.fn(),
    deleteLLMProfile: vi.fn(),
    putLLMProfile: vi.fn(),
  }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const CONFIRM_TOAST = vi.fn()
vi.mock('../utils/toasts', () => ({ confirmToast: (...a: unknown[]) => CONFIRM_TOAST(...a) }))

function payload(over: Partial<LLMSettingsPayload> = {}): LLMSettingsPayload {
  return {
    active_profile_id: 'anthropic-sonnet',
    profiles: [
      {
        id: 'anthropic-sonnet', label: 'Claude Sonnet', preset: 'anthropic-sonnet',
        wire: 'anthropic', base_url: null, model: 'claude-sonnet-5',
        tools: true, vision: true, auth: 'bearer', fallback_model: null, max_output_tokens: null,
        key_required: true, key_present: true, key_hint: '…wxyz',
      },
      {
        id: 'ollama-local', label: 'Local Ollama', preset: 'custom',
        wire: 'openai', base_url: 'http://localhost:11434/v1', model: 'qwen3:8b',
        tools: false, vision: false, auth: 'none', fallback_model: null, max_output_tokens: null,
        key_required: false, key_present: false, key_hint: null,
      },
    ],
    presets: [
      {
        id: 'openai', label: 'OpenAI', wire: 'openai', base_url: 'https://api.openai.com/v1',
        auth: 'bearer', key_env: 'OPENAI_API_KEY', tools: true, vision: true,
        suggested_models: ['gpt-5.6-sol'], help: 'Get a key at platform.openai.com.',
      },
    ],
    ...over,
  }
}

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <AssistantModelSettings />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ settingsSectionRequest: null })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => cleanup())

describe('AssistantModelSettings', () => {
  it('hides itself (renders nothing) when llm-settings answers "not for you" (403/404 → null)', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
    const { container } = renderSection()
    await waitFor(() => expect(container.firstChild).toBeNull())
  })

  // Fix round 1 — ADR-0001 in a new place: an OUTAGE must not render
  // identically to "not for you". Before this fix, `useLLMSettings`'s
  // `data` was `undefined` on ANY settled failure (a real 500, a network
  // drop, not just the 403/404 fetchLLMSettingsOrNull maps to null), and
  // `data == null` is true for `undefined` too — so a genuine outage
  // silently rendered nothing, indistinguishable from an ordinary member
  // being told this isn't for them. `fetchLLMSettingsOrNull` itself was
  // already correct (it only maps 403/404 to null and rethrows everything
  // else); the loss was one layer up, in how the component read the query.
  it('renders a distinct, visible outage state — not hidden, not the "not for you" null — on a real failure', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockRejectedValue(
      Object.assign(new Error('Internal Server Error'), {
        isAxiosError: true, response: { status: 500 },
      }),
    )
    const { container } = renderSection()

    // useLLMSettings sets `retry: 2` explicitly, which wins over this
    // QueryClient's `retry: false` default — the query genuinely retries
    // twice (~3s of real backoff) before settling to `isError`. Generous
    // timeout is deliberate, not a smell.
    const errorBox = await screen.findByTestId('assistant-model-settings-error', {}, { timeout: 8_000 })
    expect(errorBox.textContent).toMatch(/could not load/i)
    // Distinguishable from BOTH other states: not the null/hidden render...
    expect(container.firstChild).not.toBeNull()
    expect(screen.queryByTestId('assistant-model-settings')).toBeNull()
    // ...and not silently reusing the ready state's "no profiles" shape —
    // there is no profile list rendered here at all, error copy only.
    expect(screen.queryByTestId(/^assistant-model-row-/)).toBeNull()
    // A retry affordance, not a dead end.
    expect(screen.getByTestId('assistant-model-settings-retry')).toBeTruthy()
  }, 12_000)

  it('renders every profile from the payload', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    renderSection()
    expect(await screen.findByText('Claude Sonnet')).toBeTruthy()
    expect(screen.getByText('Local Ollama')).toBeTruthy()
  })

  it('posts the clicked profile as active, and does not re-post the one already active', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(postLLMActive).mockResolvedValue({ active_profile_id: 'ollama-local' })
    renderSection()
    const user = userEvent.setup()

    const activeRadio = await screen.findByTestId('assistant-model-radio-anthropic-sonnet')
    await user.click(activeRadio)
    expect(postLLMActive).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('assistant-model-radio-ollama-local'))
    expect(postLLMActive).toHaveBeenCalledWith('ollama-local')
  })

  it('never displays a stored key value — the input starts and stays empty', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    renderSection()
    const input = (await screen.findByTestId(
      'assistant-model-key-input-anthropic-sonnet',
    )) as HTMLInputElement
    expect(input.value).toBe('')
    expect(input.type).toBe('password')
    // The hint renders as its own text, never inside the input.
    expect(screen.getByText(/ending …wxyz/)).toBeTruthy()
  })

  it('shows "No key needed" for an auth: none profile and renders no key input', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    renderSection()
    await screen.findByText('Local Ollama')
    expect(screen.queryByTestId('assistant-model-key-input-ollama-local')).toBeNull()
    expect(screen.getByText(/no key needed/i)).toBeTruthy()
  })

  it('saves a typed key via putLLMProfileKey and clears the draft afterwards', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(putLLMProfileKey).mockResolvedValue({ key_required: true, key_present: true, key_hint: '…nEwK' })
    renderSection()
    const user = userEvent.setup()

    const input = (await screen.findByTestId(
      'assistant-model-key-input-anthropic-sonnet',
    )) as HTMLInputElement
    await user.type(input, 'sk-ant-fresh-value')
    await user.click(screen.getByTestId('assistant-model-key-save-anthropic-sonnet'))

    await waitFor(() =>
      expect(putLLMProfileKey).toHaveBeenCalledWith('anthropic-sonnet', 'sk-ant-fresh-value'),
    )
    await waitFor(() => expect(input.value).toBe(''))
  })

  it('clears a key through confirmToast, matching the LocalSettings clear-key pattern', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(deleteLLMProfileKey).mockResolvedValue({ key_required: true, key_present: false, key_hint: null })
    renderSection()
    const user = userEvent.setup()

    await user.click(await screen.findByTestId('assistant-model-key-clear-anthropic-sonnet'))
    expect(CONFIRM_TOAST).toHaveBeenCalled()
    expect(deleteLLMProfileKey).not.toHaveBeenCalled()

    // Simulate the user confirming inside the toast.
    await CONFIRM_TOAST.mock.calls[0][1]()
    expect(deleteLLMProfileKey).toHaveBeenCalledWith('anthropic-sonnet')
  })

  it.each([
    ['ok', { verdict: 'ok', latency_ms: 42, models: null }, /connected/i],
    ['unauthorized', { verdict: 'unauthorized', latency_ms: null, models: null }, /rejected the key/i],
    ['model_not_found', { verdict: 'model_not_found', latency_ms: null, models: null }, /doesn.t recognize this model/i],
    ['invalid_request', { verdict: 'invalid_request', latency_ms: null, models: null }, /malformed/i],
  ] as const)('renders fix-oriented copy for the %s verdict', async (_name, result, matcher) => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(postLLMTest).mockResolvedValue(result)
    renderSection()
    const user = userEvent.setup()

    await user.click(await screen.findByTestId('assistant-model-test-anthropic-sonnet'))
    const verdictText = await screen.findByTestId('assistant-model-test-result-anthropic-sonnet')
    expect(verdictText.textContent).toMatch(matcher)
  })

  it('names the localhost endpoint as possibly-not-running on an unreachable verdict', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(postLLMTest).mockResolvedValue({ verdict: 'unreachable', latency_ms: null, models: null })
    renderSection()
    const user = userEvent.setup()

    // ollama-local's base_url is http://localhost:11434/v1.
    await user.click(await screen.findByTestId('assistant-model-test-ollama-local'))
    const verdictText = await screen.findByTestId('assistant-model-test-result-ollama-local')
    expect(verdictText.textContent).toMatch(/may not be running/i)
    // The full base_url must never render in this copy (host:port at most,
    // never a path/query — the security constraint on this surface).
    expect(verdictText.textContent).not.toContain('11434')
  })

  it('says "check that it is online" — not "may not be running" — for a non-localhost unreachable endpoint', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(postLLMTest).mockResolvedValue({ verdict: 'unreachable', latency_ms: null, models: null })
    renderSection()
    const user = userEvent.setup()

    await user.click(await screen.findByTestId('assistant-model-test-anthropic-sonnet'))
    const verdictText = await screen.findByTestId('assistant-model-test-result-anthropic-sonnet')
    expect(verdictText.textContent).not.toMatch(/may not be running/i)
    expect(verdictText.textContent).toMatch(/online|reachable/i)
  })

  it('deletes a profile only after the ConfirmDialog is confirmed', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(deleteLLMProfile).mockResolvedValue({ ok: true, active_profile_id: 'anthropic-sonnet' })
    renderSection()
    const user = userEvent.setup()

    await user.click(await screen.findByTestId('assistant-model-delete-ollama-local'))
    expect(deleteLLMProfile).not.toHaveBeenCalled()

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(deleteLLMProfile).toHaveBeenCalledWith('ollama-local'))
  })

  it('adds a custom profile from the form with an id derived from the label', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    vi.mocked(putLLMProfile).mockResolvedValue({
      id: 'my-endpoint', label: 'My Endpoint', preset: 'custom', wire: 'openai',
      base_url: 'http://localhost:8000/v1', model: 'llama3', tools: false, vision: false,
      auth: 'none', fallback_model: null, max_output_tokens: null,
      key_required: false, key_present: false, key_hint: null,
    })
    renderSection()
    const user = userEvent.setup()

    await user.click(await screen.findByTestId('assistant-model-add-open'))
    await user.type(screen.getByTestId('assistant-model-add-label'), 'My Endpoint')
    await user.type(screen.getByTestId('assistant-model-add-base-url'), 'http://localhost:8000/v1')
    await user.type(screen.getByTestId('assistant-model-add-model'), 'llama3')
    await user.click(screen.getByTestId('assistant-model-add-submit'))

    await waitFor(() => expect(putLLMProfile).toHaveBeenCalled())
    const [id, body] = vi.mocked(putLLMProfile).mock.calls[0]
    expect(id).toBe('my-endpoint')
    expect(body.label).toBe('My Endpoint')
    expect(body.model).toBe('llama3')
    expect(body).not.toHaveProperty('key_env')
  })

  it('scrolls into view and clears the section request when it matches assistant-model', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    useUIStore.setState({ settingsSectionRequest: 'assistant-model' })
    renderSection()

    await screen.findByText('Claude Sonnet')
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled())
    await waitFor(() => expect(useUIStore.getState().settingsSectionRequest).toBeNull())
  })

  it('does not scroll when the pending request is for a different section', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(payload())
    useUIStore.setState({ settingsSectionRequest: 'some-other-section' })
    renderSection()

    await screen.findByText('Claude Sonnet')
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()
    expect(useUIStore.getState().settingsSectionRequest).toBe('some-other-section')
  })
})
