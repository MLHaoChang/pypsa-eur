// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * LC-07 PR3: the browser side of one continuous speech session.
 *
 * Owns the microphone stream, the server speech session (`/speech/start|end`)
 * and the media link that carries the track to the grant's room. Every exit
 * runs one `stop()`: the device tracks stop first, synchronously, then the
 * link closes and the server session ends. The media grant lives only in the
 * link's `connect` call; it is never stored here, logged or put in state.
 *
 * No LiveKit SDK ships: the link is a seam a deployment installs with
 * `configureSpeechMediaLink`. Without one the speech control is not offered.
 */
import { endSpeech, startSpeech, type SpeechMediaGrant } from '../api/durableConversations'
import { errorKind } from '../api/profiles'

export interface SpeechMediaLink {
  /** Publish the track to the grant's room. `onDrop` reports a lost link. */
  connect(grant: SpeechMediaGrant, track: MediaStreamTrack, onDrop: () => void): Promise<void>
  close(): void
}

let linkFactory: (() => SpeechMediaLink) | null = null

/** Install the media link (a deployment step; none ships). */
export function configureSpeechMediaLink(factory: (() => SpeechMediaLink) | null): void {
  linkFactory = factory
}

export function speechMediaLinkConfigured(): boolean {
  return linkFactory !== null
}

export type SpeechState = 'off' | 'starting' | 'listening'

/** Why a session stopped; each has one caption. */
export type SpeechStopReason =
  | 'ended' | 'error' | 'closed' | 'hidden' | 'pagehide' | 'unmount'
  | 'project' | 'rebind' | 'revoked' | 'expired'

/** One plain line per exit reason (UX: "Voice stopped: <reason>."). */
const STOP_COPY: Record<SpeechStopReason, string> = {
  ended: 'you turned it off',
  error: 'the voice service did not answer',
  closed: 'the chat here was closed',
  hidden: 'this tab was hidden',
  pagehide: 'the page was left',
  unmount: 'the assistant was closed',
  project: 'the project changed',
  rebind: 'the conversation was reconnected',
  revoked: 'the microphone was turned off or unplugged',
  expired: 'the session reached its time limit',
}

/** Why an error stopped voice: a fixed sentence each, never an error's own text. */
export type SpeechErrorCode =
  | 'mic_denied' | 'mic_missing' | 'mic_busy' | 'mic_failed'
  | 'not_set_up' | 'session_open' | 'resume_first' | 'reconnected' | 'service_failed'
  | 'link_failed' | 'link_lost'

const ERROR_COPY: Record<SpeechErrorCode, string> = {
  mic_denied: 'microphone access was not allowed',
  mic_missing: 'no microphone was found',
  mic_busy: 'the microphone is in use by another app',
  mic_failed: 'the microphone could not start',
  not_set_up: 'voice is not set up on this server',
  session_open: 'voice is already on for this conversation elsewhere',
  resume_first: 'the conversation needs to resume first',
  reconnected: 'the conversation was reconnected',
  service_failed: 'the voice service did not start',
  link_failed: 'the voice connection did not start',
  link_lost: 'the voice connection was lost',
}

export const LISTENING_CAPTION = 'Listening: speak, then pause.'
export const TYPED_FALLBACK = 'Type here to continue in the same conversation.'

/** "Voice stopped: <reason>. Type here to continue in the same conversation." */
export function stopCaption(reason: SpeechStopReason, code?: SpeechErrorCode): string {
  const words = reason === 'error' && code ? ERROR_COPY[code] : STOP_COPY[reason]
  return `Voice stopped: ${words}. ${TYPED_FALLBACK}`
}

export function micErrorCode(error: unknown): SpeechErrorCode {
  // DOMException may come from another realm and need not be instanceof Error.
  const name = error && typeof error === 'object' && 'name' in error ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'mic_denied'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'mic_missing'
  if (name === 'NotReadableError') return 'mic_busy'
  return 'mic_failed'
}

export function refusalCode(error: unknown): SpeechErrorCode {
  switch (errorKind(error)) {
    case 'speech_disabled': case 'speech_not_configured': return 'not_set_up'
    case 'speech_session_open': return 'session_open'
    case 'speech_episode_required': return 'resume_first'
    case 'stale_generation': case 'invalid_generation': return 'reconnected'
    default: return 'service_failed'
  }
}

export type SpeechHandlers = {
  onState: (state: SpeechState) => void
  onStopped: (reason: SpeechStopReason, caption: string) => void
}

export class LiveSpeechSession {
  private generation = 0
  private stream: MediaStream | null = null
  private link: SpeechMediaLink | null = null
  private server: { threadId: string; generation: number; sessionId: string } | null = null
  private expiry: ReturnType<typeof setTimeout> | null = null
  private state: SpeechState = 'off'

  constructor(private readonly handlers: SpeechHandlers) {}

  get current(): SpeechState { return this.state }

  /** Start speaking on the bound thread; any failure stops with a caption. */
  async start(threadId: string, threadGeneration: number, language: string): Promise<void> {
    if (this.state !== 'off') return
    const factory = linkFactory
    if (!factory) {
      this.finish('error', 'link_failed')
      return
    }
    const run = ++this.generation
    this.setState('starting')
    try {
      // The mic first: a refusal here reserves nothing on the server.
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      } catch (e) {
        this.fail(run, micErrorCode(e))
        return
      }
      if (run !== this.generation) { stopTracks(stream); return }
      this.stream = stream
      const track = stream.getAudioTracks()[0]
      if (!track) { this.fail(run, 'mic_missing'); return }
      track.onended = () => { if (run === this.generation) this.stop('revoked') }

      let started
      try {
        started = await startSpeech(threadId, threadGeneration, language)
      } catch (e) {
        this.fail(run, refusalCode(e))
        return
      }
      const server = { threadId, generation: started.generation, sessionId: started.session.session_id }
      // A stale run ends its own session and never touches a newer run's ref.
      if (run !== this.generation) { endQuietly(server); return }
      this.server = server

      const link = factory()
      this.link = link
      await link.connect(started.media, track, () => {
        if (run === this.generation) this.stop('error', 'link_lost')
      })
      if (run !== this.generation) return
      const ends = Date.parse(started.session.expires_at ?? '')
      if (Number.isFinite(ends)) {
        this.expiry = setTimeout(() => {
          if (run === this.generation) this.stop('expired')
        }, Math.max(0, ends - Date.now()))
      }
      this.setState('listening')
    } catch (e) {
      this.fail(run, this.server ? 'link_failed' : 'service_failed')
    }
  }

  /** Stop on any exit. Tracks stop before anything else; safe to call twice. */
  stop(reason: SpeechStopReason, code?: SpeechErrorCode): void {
    if (this.state === 'off' && !this.stream && !this.server && !this.link) return
    this.generation += 1
    this.release()
    this.finish(reason, code)
  }

  private fail(run: number, code: SpeechErrorCode): void {
    if (run !== this.generation) return
    this.stop('error', code)
  }

  private release(): void {
    if (this.stream) {
      this.stream.getTracks().forEach(t => { t.onended = null; t.stop() })
      this.stream = null
    }
    if (this.expiry) { clearTimeout(this.expiry); this.expiry = null }
    const link = this.link
    this.link = null
    try { link?.close() } catch { /* already closed */ }
    this.endServer()
  }

  private endServer(): void {
    const server = this.server
    this.server = null
    // Best effort: a rebind, timeout or restart already closed it server-side,
    // and the server reaps an unended session at its deadline.
    if (server) endQuietly(server)
  }

  private finish(reason: SpeechStopReason, code?: SpeechErrorCode): void {
    this.setState('off')
    this.handlers.onStopped(reason, stopCaption(reason, code))
  }

  private setState(state: SpeechState): void {
    if (this.state === state) return
    this.state = state
    this.handlers.onState(state)
  }
}

function stopTracks(stream: MediaStream): void {
  stream.getTracks().forEach(t => t.stop())
}

function endQuietly(server: { threadId: string; generation: number; sessionId: string }): void {
  void endSpeech(server.threadId, server.generation, server.sessionId).catch(() => {})
}
