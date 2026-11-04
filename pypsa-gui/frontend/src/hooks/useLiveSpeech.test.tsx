// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/** LC-07 PR3: the device track stops on every exit path, a test each. */
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { configureSpeechMediaLink, LISTENING_CAPTION, refusalCode, stopCaption, TYPED_FALLBACK, type SpeechMediaLink } from '../utils/liveSpeechSession'
import { deferred } from '../utils/audioRecorder.testHelpers'

const api = vi.hoisted(() => ({
  getSpeechState: vi.fn(),
  startSpeech: vi.fn(),
  endSpeech: vi.fn(),
}))
vi.mock('../api/durableConversations', () => api)

import { useLiveSpeech } from './useLiveSpeech'

const GRANT = { url: 'wss://media.example/room', token: 'grant-secret', expires_at: 1e12 }
const started = (expires_at = new Date(Date.now() + 600_000).toISOString()) => ({
  session: { session_id: 'sp-1', state: 'open', expires_at },
  generation: 3,
  media: GRANT,
  reconcile: [],
})

let track: { stop: ReturnType<typeof vi.fn>; onended: (() => void) | null; kind: string }
let getUserMedia: ReturnType<typeof vi.fn>
let link: { connect: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn>; drop?: () => void }

function refusal(kind: string, message: string, status = 503) {
  return Object.assign(new Error('Request failed'), {
    isAxiosError: true, response: { status, data: { detail: { error_kind: kind, message } } },
  })
}

beforeEach(() => {
  const own = { stop: vi.fn(), onended: null, kind: 'audio' }
  track = own
  const stream = { getTracks: () => [own], getAudioTracks: () => [own] }
  getUserMedia = vi.fn().mockResolvedValue(stream)
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
  link = {
    connect: vi.fn(async (_g, _t, onDrop: () => void) => { link.drop = onDrop }),
    close: vi.fn(),
  }
  configureSpeechMediaLink(() => link as unknown as SpeechMediaLink)
  api.getSpeechState.mockResolvedValue({ generation: 3, session: null })
  api.startSpeech.mockResolvedValue(started())
  api.endSpeech.mockResolvedValue({ status: 'closed', session: null })
  useChatStore.setState({ durableThreadId: 't-live', utteranceGeneration: 3 })
  useUIStore.setState({ currentProject: 'Alpha' })
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})

afterEach(() => {
  cleanup()          // unmount (an exit itself) before the mocks are cleared
  configureSpeechMediaLink(null)
  vi.useRealTimers()
  vi.clearAllMocks()
})

async function listening(active = true) {
  const hook = renderHook(({ on }) => useLiveSpeech(on), { initialProps: { on: active } })
  await waitFor(() => expect(hook.result.current.available).toBe(true))
  await act(async () => { hook.result.current.start() })
  await waitFor(() => expect(hook.result.current.state).toBe('listening'))
  expect(track.stop).not.toHaveBeenCalled()
  return hook
}

function expectReleased() {
  expect(track.stop).toHaveBeenCalledTimes(1)
  expect(link.close).toHaveBeenCalledTimes(1)
  expect(api.endSpeech).toHaveBeenCalledWith('t-live', 3, 'sp-1')
  expect(api.endSpeech).toHaveBeenCalledTimes(1)
}

describe('useLiveSpeech availability', () => {
  it('is offered when the server answers and a link is configured', async () => {
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.available).toBe(true))
    expect(api.getSpeechState).toHaveBeenCalledWith('t-live')
  })

  it('is not offered when the server flag is off', async () => {
    api.getSpeechState.mockRejectedValue(refusal('speech_disabled', 'Continuous speech is not enabled.'))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(api.getSpeechState).toHaveBeenCalled())
    await act(async () => {})
    expect(result.current.available).toBe(false)
  })

  it('flag on but no media link (no SDK ships): disabled with a visible reason', async () => {
    configureSpeechMediaLink(null)
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.blocked).toBe(
      'Voice unavailable: this app has no media connection set up, so use reviewed dictation.'))
    expect(result.current.available).toBe(false)
  })

  it('flag off: no reason line of its own (the launcher keeps "not connected")', async () => {
    configureSpeechMediaLink(null)
    api.getSpeechState.mockRejectedValue(refusal('speech_disabled', 'Continuous speech is not enabled.'))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(api.getSpeechState).toHaveBeenCalled())
    await act(async () => {})
    expect(result.current.blocked).toBe('')
  })

  it('is not offered while Live is closed', async () => {
    const { result } = renderHook(() => useLiveSpeech(false))
    await act(async () => {})
    expect(result.current.available).toBe(false)
  })
})

describe('useLiveSpeech start', () => {
  it('asks for the mic, then the server, then connects the track with the grant', async () => {
    await listening()
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(api.startSpeech).toHaveBeenCalledWith('t-live', 3, expect.any(String))
    expect(link.connect).toHaveBeenCalledWith(GRANT, track, expect.any(Function))
  })

  it('while listening the caption is the listening line', async () => {
    const { result } = await listening()
    expect(result.current.caption).toBe(LISTENING_CAPTION)
    expect(LISTENING_CAPTION).toBe('Listening: speak, then pause.')
  })

  it('never keeps the grant token in hook state', async () => {
    const { result } = await listening()
    expect(JSON.stringify(result.current)).not.toContain('grant-secret')
  })
})

describe('useLiveSpeech: every exit stops the device track', () => {
  it('Stop (end)', async () => {
    const { result } = await listening()
    act(() => result.current.stop())
    expectReleased()
    expect(result.current.state).toBe('off')
    expect(result.current.caption).toBe(`Voice stopped: you turned it off. ${TYPED_FALLBACK}`)
  })

  it('error: the server refuses start; the mic stops and nothing is ended', async () => {
    api.startSpeech.mockRejectedValue(refusal('speech_not_configured',
      'No speech media service is configured. Type your message instead.'))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.available).toBe(true))
    await act(async () => { result.current.start() })
    await waitFor(() => expect(result.current.state).toBe('off'))
    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(link.connect).not.toHaveBeenCalled()
    expect(api.endSpeech).not.toHaveBeenCalled()
    expect(result.current.caption).toBe(`Voice stopped: voice is not set up on this server. ${TYPED_FALLBACK}`)
  })

  it('error: the mic is denied; the server is never asked', async () => {
    getUserMedia.mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.available).toBe(true))
    await act(async () => { result.current.start() })
    await waitFor(() => expect(result.current.caption).toBe(`Voice stopped: microphone access was not allowed. ${TYPED_FALLBACK}`))
    expect(api.startSpeech).not.toHaveBeenCalled()
    expect(result.current.state).toBe('off')
  })

  it('error: the media link fails to connect', async () => {
    link.connect.mockRejectedValue(new Error('The media room refused the connection.'))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.available).toBe(true))
    await act(async () => { result.current.start() })
    await waitFor(() => expect(result.current.state).toBe('off'))
    expectReleased()
    expect(result.current.caption).toBe(`Voice stopped: the voice connection did not start. ${TYPED_FALLBACK}`)
  })

  it('error: the media link drops while listening', async () => {
    const { result } = await listening()
    act(() => link.drop?.())
    expectReleased()
    expect(result.current.caption).toBe(`Voice stopped: the voice connection was lost. ${TYPED_FALLBACK}`)
  })

  it('Live closed (navigation away from the composer)', async () => {
    const hook = await listening()
    hook.rerender({ on: false })
    expectReleased()
    expect(hook.result.current.caption).toBe(`Voice stopped: the chat here was closed. ${TYPED_FALLBACK}`)
  })

  it('unmount', async () => {
    const hook = await listening()
    hook.unmount()
    expectReleased()
  })

  it('tab hidden', async () => {
    const { result } = await listening()
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    expectReleased()
    expect(result.current.caption).toBe(`Voice stopped: this tab was hidden. ${TYPED_FALLBACK}`)
  })

  it('page left (pagehide)', async () => {
    const { result } = await listening()
    act(() => { window.dispatchEvent(new Event('pagehide')) })
    expectReleased()
    expect(result.current.caption).toBe(`Voice stopped: the page was left. ${TYPED_FALLBACK}`)
  })

  it('project switch', async () => {
    const { result } = await listening()
    act(() => { useUIStore.setState({ currentProject: 'Beta' }) })
    expectReleased()
    expect(result.current.caption).toBe(`Voice stopped: the project changed. ${TYPED_FALLBACK}`)
  })

  it('rebind (the thread generation advanced)', async () => {
    const { result } = await listening()
    act(() => { useChatStore.setState({ utteranceGeneration: 4 }) })
    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(link.close).toHaveBeenCalledTimes(1)
    expect(result.current.caption).toBe(`Voice stopped: the conversation was reconnected. ${TYPED_FALLBACK}`)
  })

  it('mic revoked or unplugged (track ended)', async () => {
    const { result } = await listening()
    act(() => track.onended?.())
    expect(link.close).toHaveBeenCalledTimes(1)
    expect(api.endSpeech).toHaveBeenCalledTimes(1)
    expect(result.current.caption).toBe(`Voice stopped: the microphone was turned off or unplugged. ${TYPED_FALLBACK}`)
  })

  it('session expiry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.startSpeech.mockResolvedValue(started(new Date(Date.now() + 5_000).toISOString()))
    const { result } = await listening()
    await act(async () => { vi.advanceTimersByTime(5_001) })
    expectReleased()
    expect(result.current.caption).toBe(`Voice stopped: the session reached its time limit. ${TYPED_FALLBACK}`)
  })

  it('a stop while the mic prompt is open stops the late track and starts nothing', async () => {
    const mic = deferred<unknown>()
    getUserMedia.mockReturnValue(mic.promise)
    const hook = renderHook(({ on }) => useLiveSpeech(on), { initialProps: { on: true } })
    await waitFor(() => expect(hook.result.current.available).toBe(true))
    act(() => hook.result.current.start())
    expect(hook.result.current.state).toBe('starting')
    hook.rerender({ on: false })
    await act(async () => { mic.resolve({ getTracks: () => [track], getAudioTracks: () => [track] }) })
    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(api.startSpeech).not.toHaveBeenCalled()
  })

  it('a stop while the server is starting ends the session once it answers', async () => {
    const server = deferred<ReturnType<typeof started>>()
    api.startSpeech.mockReturnValue(server.promise)
    const hook = renderHook(({ on }) => useLiveSpeech(on), { initialProps: { on: true } })
    await waitFor(() => expect(hook.result.current.available).toBe(true))
    act(() => hook.result.current.start())
    await waitFor(() => expect(api.startSpeech).toHaveBeenCalled())
    act(() => { window.dispatchEvent(new Event('pagehide')) })
    expect(track.stop).toHaveBeenCalledTimes(1)
    await act(async () => { server.resolve(started()) })
    expect(api.endSpeech).toHaveBeenCalledWith('t-live', 3, 'sp-1')
    expect(link.connect).not.toHaveBeenCalled()
  })

  it('a late start from a stopped run never replaces the new run session', async () => {
    const first = deferred<ReturnType<typeof started>>()
    api.startSpeech.mockReturnValueOnce(first.promise)
    const hook = renderHook(({ on }) => useLiveSpeech(on), { initialProps: { on: true } })
    await waitFor(() => expect(hook.result.current.available).toBe(true))
    act(() => hook.result.current.start())
    await waitFor(() => expect(api.startSpeech).toHaveBeenCalledTimes(1))
    act(() => hook.result.current.stop())
    api.startSpeech.mockResolvedValueOnce({ ...started(), session: { session_id: 'sp-2', state: 'open', expires_at: new Date(Date.now() + 600_000).toISOString() } })
    await act(async () => { hook.result.current.start() })
    await waitFor(() => expect(hook.result.current.state).toBe('listening'))
    await act(async () => { first.resolve(started()) })
    expect(api.endSpeech).toHaveBeenCalledWith('t-live', 3, 'sp-1')
    act(() => hook.result.current.stop())
    expect(api.endSpeech).toHaveBeenCalledWith('t-live', 3, 'sp-2')
  })

  it('the typed fallback stays on the same durable thread', async () => {
    const { result } = await listening()
    act(() => result.current.stop())
    expect(useChatStore.getState().durableThreadId).toBe('t-live')
    expect(useChatStore.getState().utteranceGeneration).toBe(3)
  })
})

describe('stopCaption: one plain line per exit reason', () => {
  it.each([
    ['ended', 'Voice stopped: you turned it off.'],
    ['error', 'Voice stopped: the voice service did not answer.'],
    ['closed', 'Voice stopped: the chat here was closed.'],
    ['hidden', 'Voice stopped: this tab was hidden.'],
    ['pagehide', 'Voice stopped: the page was left.'],
    ['unmount', 'Voice stopped: the assistant was closed.'],
    ['project', 'Voice stopped: the project changed.'],
    ['rebind', 'Voice stopped: the conversation was reconnected.'],
    ['revoked', 'Voice stopped: the microphone was turned off or unplugged.'],
    ['expired', 'Voice stopped: the session reached its time limit.'],
  ] as const)('%s', (reason, line) => {
    expect(stopCaption(reason)).toBe(`${line} ${TYPED_FALLBACK}`)
    expect(line).not.toMatch(/—|_|\n/)
  })

  it.each([
    ['mic_denied', 'microphone access was not allowed'],
    ['mic_missing', 'no microphone was found'],
    ['mic_busy', 'the microphone is in use by another app'],
    ['mic_failed', 'the microphone could not start'],
    ['not_set_up', 'voice is not set up on this server'],
    ['session_open', 'voice is already on for this conversation elsewhere'],
    ['resume_first', 'the conversation needs to resume first'],
    ['reconnected', 'the conversation was reconnected'],
    ['service_failed', 'the voice service did not start'],
    ['link_failed', 'the voice connection did not start'],
    ['link_lost', 'the voice connection was lost'],
  ] as const)('error %s is a fixed sentence', (code, words) => {
    expect(stopCaption('error', code)).toBe(`Voice stopped: ${words}. ${TYPED_FALLBACK}`)
  })

  it('a server message never reaches the caption', () => {
    const line = stopCaption('error', refusalCode(Object.assign(new Error('x'), {
      response: { data: { detail: { error_kind: 'speech_transport_failed', message: 'Internal room id r-9 failed' } } } })))
    expect(line).toBe(`Voice stopped: the voice service did not start. ${TYPED_FALLBACK}`)
  })
})
