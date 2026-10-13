import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { getChatHealth, getApiKeySettings } from '../api/chat'
import { getChatProfiles } from '../api/llmSettings'
import ChatLaunchGreeting from './ChatLaunchGreeting'

// The launch orientation, from the approved spec
// (docs/superpowers/specs/2026-08-05-assistant-presence-and-deixis-design.md):
//
//   "That local summary renders IMMEDIATELY — no spinner, no key required, no
//    network. […] With no project open, the greeting says so and offers the two
//    useful next actions […] This makes the assistant the natural entry point
//    on a cold start rather than an empty canvas."
//
// "No network" means no ANTHROPIC call. `getMeta` / `getStatus` are already
// fetched at launch by OverviewPanel and cached under the same keys, so the
// numbers usually arrive from cache — but the greeting must not WAIT on them.
// A spinner here would defeat the point: the whole value is that the first
// thing on screen already knows where you are.
//
// The API-key rule is the other half, and it is a rule about what must NOT
// happen: "It must not produce the red `missing_api_key` error banner — a
// feature that throws an error on every launch gets disabled permanently
// within a week." So the no-key path is asserted against chatStore.error
// staying null, not merely against the absence of some element.

vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn() },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { getStatus: vi.fn() },
}))
// P28: the key offer reads per-profile readiness from GET /chat/profiles.
// Refused by default (the local-mode / older-backend fallback: `/health`
// decides for the active profile); a case that needs the list says so.
vi.mock('../api/llmSettings', () => ({ getChatProfiles: vi.fn() }))
vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    getApiKeySettings: vi.fn(),
    getChatHealth: vi.fn(),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})

const KEY_CONFIGURED = {
  configured: true, source: 'settings' as const, hint: '…wxyz',
  overridden_by_environment: false, storage_path: '/tmp/user.env',
}
const KEY_MISSING = { ...KEY_CONFIGURED, configured: false, hint: null, source: null }

function renderGreeting() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatLaunchGreeting />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'Baltic 2030' })
  useChatStore.setState({ error: null })
  vi.mocked(networkApi.getMeta).mockResolvedValue({
    name: 'Baltic 2030', bus_count: 24, snapshot_count: 168,
  })
  vi.mocked(simulationApi.getStatus).mockResolvedValue({
    running: false, status: 'completed', condition: 'optimal',
    objective: 1234, solve_time: 12, dispatch: 'fresh',
  })
  vi.mocked(getApiKeySettings).mockResolvedValue(KEY_CONFIGURED)
  // Readiness unknown (the probe carries no chat_ready) unless a test says.
  vi.mocked(getChatHealth).mockResolvedValue({ ok: true } as never)
  vi.mocked(getChatProfiles).mockRejectedValue(new Error('401'))
  useChatStore.setState({ profileId: null, boundProfileId: null } as never)
})

describe('the launch orientation, with a project open', () => {
  it('names the project and its size', async () => {
    renderGreeting()

    const g = await screen.findByTestId('chat-launch-greeting')
    expect(g.textContent).toContain('Baltic 2030')
    await waitFor(() => {
      expect(screen.getByTestId('chat-launch-facts').textContent).toMatch(/24/)
    })
    expect(screen.getByTestId('chat-launch-facts').textContent).toMatch(/168/)
  })

  // The spec's own words: the summary renders immediately, no spinner. A
  // never-settling queryFn is the honest way to ask that — if the component
  // gates its body on `isLoading`, this render is empty and the assertion
  // fails without any timing luck involved.
  it('renders before the network answers, with no spinner', () => {
    vi.mocked(networkApi.getMeta).mockReturnValue(new Promise(() => {}) as never)
    vi.mocked(simulationApi.getStatus).mockReturnValue(new Promise(() => {}) as never)
    vi.mocked(getApiKeySettings).mockReturnValue(new Promise(() => {}) as never)

    renderGreeting()

    // Synchronously — nothing awaited.
    const g = screen.getByTestId('chat-launch-greeting')
    expect(g.textContent).toContain('Baltic 2030')
    expect(g.textContent).not.toMatch(/loading|Loading|…ing…/)
    expect(screen.queryByTestId('chat-launch-spinner')).toBeNull()
  })

  it('says results are stale when the network changed since the solve', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'completed', condition: 'optimal',
      objective: 1234, solve_time: 12, dispatch: 'stale',
    })
    renderGreeting()

    await waitFor(() => {
      expect(screen.getByTestId('chat-launch-solve').textContent).toMatch(/stale/i)
    })
  })

  it('says the results match when dispatch is fresh', async () => {
    renderGreeting()
    await waitFor(() => {
      expect(screen.getByTestId('chat-launch-solve').textContent).toMatch(/match/i)
    })
    expect(screen.getByTestId('chat-launch-solve').textContent).not.toMatch(/stale/i)
  })

  it('says it is not solved yet when there is no dispatch', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'idle', condition: null,
      objective: null, solve_time: null, dispatch: 'none',
    })
    renderGreeting()

    await waitFor(() => {
      expect(screen.getByTestId('chat-launch-solve').textContent).toMatch(/not solved/i)
    })
  })
})

describe('the launch orientation, with no project open', () => {
  beforeEach(() => { useUIStore.setState({ currentProject: null }) })

  it('says so and offers both ways forward', async () => {
    renderGreeting()

    const g = await screen.findByTestId('chat-launch-greeting')
    expect(g.textContent).toMatch(/no project/i)
    expect(screen.getByTestId('chat-empty-open-project')).toBeTruthy()
    expect(screen.getByTestId('chat-empty-new-project')).toBeTruthy()
  })

  // There is nothing to report about a network that is not open, and a solve
  // line reading "Not solved yet" against no project is noise dressed as fact.
  it('reports nothing about a network that is not open', async () => {
    renderGreeting()
    await screen.findByTestId('chat-launch-greeting')

    expect(screen.queryByTestId('chat-launch-facts')).toBeNull()
    expect(screen.queryByTestId('chat-launch-solve')).toBeNull()
  })
})

describe('the launch orientation with no API key', () => {
  it('makes a quiet offer instead of an error', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    renderGreeting()

    expect(await screen.findByTestId('chat-launch-key-offer')).toBeTruthy()
    // The rule the spec states as a prohibition. `missing_api_key` in
    // chatStore.error is what paints the red banner (ChatPanel.tsx's
    // ErrorBanner), so a greeting that sets it would ship the exact failure
    // the spec is guarding against — one that fires on EVERY launch.
    expect(useChatStore.getState().error).toBeNull()
    expect(screen.queryByTestId('chat-error-banner')).toBeNull()
  })

  it('stays out of the way once a key is configured', async () => {
    renderGreeting()
    await screen.findByTestId('chat-launch-greeting')

    await waitFor(() => {
      expect(vi.mocked(getApiKeySettings)).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('chat-launch-key-offer')).toBeNull()
  })

  // A member of a multi-tenant instance gets a 403 from this route — the key
  // is instance-wide and super-admin only. Offering them a field they cannot
  // use is worse than saying nothing, and the query erroring must not be
  // mistaken for "no key configured".
  it('offers nothing when the key route refuses the caller', async () => {
    vi.mocked(getApiKeySettings).mockRejectedValue(
      Object.assign(new Error('forbidden'), { response: { status: 403 } }),
    )
    renderGreeting()
    await screen.findByTestId('chat-launch-greeting')

    await waitFor(() => {
      expect(vi.mocked(getApiKeySettings)).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('chat-launch-key-offer')).toBeNull()
    expect(useChatStore.getState().error).toBeNull()
  })
})

// P26 (coordinator item 8): "Add an Anthropic API key to talk to me" showed
// while the assistant worked on another profile (the smoke's stub, a local
// model). The offer follows the Send gate's rule: the session's effective
// profile is `profileId ?? active`, and the offer hides while that profile is
// known to be ready. Both modes.
describe('the key offer follows the effective profile (P26)', () => {
  const health = (chat_ready: boolean) => ({ ok: true, chat_ready,
    active_profile: { id: 'local', label: 'Local', wire: 'openai' } })
  for (const mode of ['expert', 'guided'] as const) {
    it(`${mode}: hidden while the active profile is ready and no other is picked`, async () => {
      useUIStore.setState({ uiMode: mode })
      vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
      vi.mocked(getChatHealth).mockResolvedValue(health(true) as never)
      renderGreeting()
      await waitFor(() => expect(vi.mocked(getChatHealth)).toHaveBeenCalled())
      await waitFor(() => expect(vi.mocked(getApiKeySettings)).toHaveBeenCalled())
      await new Promise(r => setTimeout(r, 20))
      expect(screen.queryByTestId('chat-launch-key-offer')).toBeNull()
    })
  }

  it('hidden while the picked profile IS the ready active one', async () => {
    useChatStore.setState({ profileId: 'local' })
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    vi.mocked(getChatHealth).mockResolvedValue(health(true) as never)
    renderGreeting()
    await waitFor(() => expect(vi.mocked(getChatHealth)).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByTestId('chat-launch-key-offer')).toBeNull()
  })

  it('shown while the effective profile is not ready', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    vi.mocked(getChatHealth).mockResolvedValue(health(false) as never)
    renderGreeting()
    expect(await screen.findByTestId('chat-launch-key-offer')).toBeTruthy()
  })

  it('shown when another profile is picked (its readiness is not known here)', async () => {
    useChatStore.setState({ profileId: 'anthropic-default' })
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    vi.mocked(getChatHealth).mockResolvedValue(health(true) as never)
    renderGreeting()
    expect(await screen.findByTestId('chat-launch-key-offer')).toBeTruthy()
  })
})

// P28 A3 (deferred spec §3.1; closes P26 note 5): the offer follows the
// profile the next turn runs on — `profileId ?? boundProfileId ?? active` —
// and that profile's own `chat_ready` from GET /chat/profiles.
describe('the key offer follows per-profile readiness (P28 A3)', () => {
  const LIST = {
    active_profile_id: 'anthropic-default',
    profiles: [
      { id: 'anthropic-default', label: 'Default', wire: 'anthropic', chat_ready: false },
      { id: 'local', label: 'Local', wire: 'openai', chat_ready: true },
    ],
  }
  const notReadyHealth = { ok: true, chat_ready: false,
    active_profile: { id: 'anthropic-default', label: 'Default', wire: 'anthropic' } }

  it('picked ready non-active profile → no key offer', async () => {
    useChatStore.setState({ profileId: 'local' })
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    vi.mocked(getChatHealth).mockResolvedValue(notReadyHealth as never)
    vi.mocked(getChatProfiles).mockResolvedValue(LIST as never)
    renderGreeting()
    await waitFor(() => expect(vi.mocked(getChatProfiles)).toHaveBeenCalled())
    await waitFor(() => expect(vi.mocked(getApiKeySettings)).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 30))
    expect(screen.queryByTestId('chat-launch-key-offer')).toBeNull()
  })

  it('bound (after a reload) to a ready non-active profile → no key offer', async () => {
    useChatStore.setState({ boundProfileId: 'local' } as never)
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    vi.mocked(getChatHealth).mockResolvedValue(notReadyHealth as never)
    vi.mocked(getChatProfiles).mockResolvedValue(LIST as never)
    renderGreeting()
    await waitFor(() => expect(vi.mocked(getChatProfiles)).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 30))
    expect(screen.queryByTestId('chat-launch-key-offer')).toBeNull()
  })

  it('the control: nothing picked or bound, the active profile not ready → the offer shows', async () => {
    vi.mocked(getApiKeySettings).mockResolvedValue(KEY_MISSING)
    vi.mocked(getChatHealth).mockResolvedValue(notReadyHealth as never)
    vi.mocked(getChatProfiles).mockResolvedValue(LIST as never)
    renderGreeting()
    expect(await screen.findByTestId('chat-launch-key-offer')).toBeTruthy()
  })
})
