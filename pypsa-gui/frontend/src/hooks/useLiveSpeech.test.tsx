// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/** LC-07 PR3: the device track stops on every exit path, a test each. */
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { configureSpeechMediaLink, TYPED_FALLBACK, type SpeechMediaLink } from '../utils/liveSpeechSession'
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
let link: { connect: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn>; drop?: (m: string) => void }

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
    connect: vi.fn(async (_g, _t, onDrop: (m: string) => void) => { link.drop = onDrop }),
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

function expectReleased(caption: RegExp) {
  expect(track.stop).toHaveBeenCalledTimes(1)
  expect(link.close).toHaveBeenCalledTimes(1)
  expect(api.endSpeech).toHaveBeenCalledWith('t-live', 3, 'sp-1')
  expect(api.endSpeech).toHaveBeenCalledTimes(1)
  return caption
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

  it('is not offered without a media link (no SDK ships), and asks the server nothing', async () => {
    configureSpeechMediaLink(null)
    const { result } = renderHook(() => useLiveSpeech(true))
    await act(async () => {})
    expect(result.current.available).toBe(false)
    expect(api.getSpeechState).not.toHaveBeenCalled()
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

  it('never keeps the grant token in hook state', async () => {
    const { result } = await listening()
    expect(JSON.stringify(result.current)).not.toContain('grant-secret')
  })
})

describe('useLiveSpeech: every exit stops the device track', () => {
  it('Stop (end)', async () => {
    const { result } = await listening()
    act(() => result.current.stop())
    expectReleased(/Speech stopped\./)
    expect(result.current.state).toBe('off')
    expect(result.current.caption).toBe(`Speech stopped. ${TYPED_FALLBACK}`)
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
    expect(result.current.caption).toBe(
      `Speech stopped after an error. No speech media service is configured. ${TYPED_FALLBACK}`)
  })

  it('error: the mic is denied; the server is never asked', async () => {
    getUserMedia.mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.available).toBe(true))
    await act(async () => { result.current.start() })
    await waitFor(() => expect(result.current.caption).toMatch(/Microphone access denied/))
    expect(api.startSpeech).not.toHaveBeenCalled()
    expect(result.current.state).toBe('off')
  })

  it('error: the media link fails to connect', async () => {
    link.connect.mockRejectedValue(new Error('The media room refused the connection.'))
    const { result } = renderHook(() => useLiveSpeech(true))
    await waitFor(() => expect(result.current.available).toBe(true))
    await act(async () => { result.current.start() })
    await waitFor(() => expect(result.current.state).toBe('off'))
    expectReleased(/./)
    expect(result.current.caption).toMatch(/media room refused/)
  })

  it('error: the media link drops while listening', async () => {
    const { result } = await listening()
    act(() => link.drop?.('The media connection was lost.'))
    expectReleased(/./)
    expect(result.current.caption).toMatch(/media connection was lost/)
  })

  it('Live closed (navigation away from the composer)', async () => {
    const hook = await listening()
    hook.rerender({ on: false })
    expectReleased(/./)
  })

  it('unmount', async () => {
    const hook = await listening()
    hook.unmount()
    expectReleased(/./)
  })

  it('tab hidden', async () => {
    const { result } = await listening()
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    expectReleased(/./)
    expect(result.current.caption).toMatch(/tab was hidden/)
  })

  it('page left (pagehide)', async () => {
    const { result } = await listening()
    act(() => { window.dispatchEvent(new Event('pagehide')) })
    expectReleased(/./)
    expect(result.current.caption).toMatch(/page was left/)
  })

  it('project switch', async () => {
    const { result } = await listening()
    act(() => { useUIStore.setState({ currentProject: 'Beta' }) })
    expectReleased(/./)
    expect(result.current.caption).toMatch(/project changed/)
  })

  it('rebind (the thread generation advanced)', async () => {
    const { result } = await listening()
    act(() => { useChatStore.setState({ utteranceGeneration: 4 }) })
    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(link.close).toHaveBeenCalledTimes(1)
    expect(result.current.caption).toMatch(/conversation was reconnected/)
  })

  it('mic revoked or unplugged (track ended)', async () => {
    const { result } = await listening()
    act(() => track.onended?.())
    expect(link.close).toHaveBeenCalledTimes(1)
    expect(api.endSpeech).toHaveBeenCalledTimes(1)
    expect(result.current.caption).toMatch(/microphone was turned off or disconnected/)
  })

  it('session expiry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.startSpeech.mockResolvedValue(started(new Date(Date.now() + 5_000).toISOString()))
    const { result } = await listening()
    await act(async () => { vi.advanceTimersByTime(5_001) })
    expectReleased(/./)
    expect(result.current.caption).toMatch(/time limit/)
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

  it('the typed fallback stays on the same durable thread', async () => {
    const { result } = await listening()
    act(() => result.current.stop())
    expect(useChatStore.getState().durableThreadId).toBe('t-live')
    expect(useChatStore.getState().utteranceGeneration).toBe(3)
  })
})
