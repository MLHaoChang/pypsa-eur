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
import { errorKind, errorText } from '../api/profiles'
import { recordingError } from './audioRecorder'

export interface SpeechMediaLink {
  /** Publish the track to the grant's room. `onDrop` reports a lost link. */
  connect(grant: SpeechMediaGrant, track: MediaStreamTrack, onDrop: (message: string) => void): Promise<void>
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

const STOP_COPY: Record<SpeechStopReason, string> = {
  ended: 'Speech stopped.',
  error: 'Speech stopped after an error.',
  closed: 'Speech stopped.',
  hidden: 'Speech stopped because this tab was hidden.',
  pagehide: 'Speech stopped because the page was left.',
  unmount: 'Speech stopped.',
  project: 'Speech stopped because the project changed.',
  rebind: 'Speech stopped because the conversation was reconnected.',
  revoked: 'Speech stopped: the microphone was turned off or disconnected.',
  expired: 'Speech stopped: the session reached its time limit.',
}

export const TYPED_FALLBACK = 'Type here to continue in the same conversation.'

export function stopCaption(reason: SpeechStopReason, detail = ''): string {
  return [STOP_COPY[reason], detail, TYPED_FALLBACK].filter(Boolean).join(' ')
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
      this.finish('error', 'No speech media link is configured.')
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
        this.fail(run, recordingError(e))
        return
      }
      if (run !== this.generation) { stopTracks(stream); return }
      this.stream = stream
      const track = stream.getAudioTracks()[0]
      if (!track) { this.fail(run, 'No microphone track.'); return }
      track.onended = () => { if (run === this.generation) this.stop('revoked') }

      let started
      try {
        started = await startSpeech(threadId, threadGeneration, language)
      } catch (e) {
        this.fail(run, refusalText(e))
        return
      }
      this.server = { threadId, generation: started.generation, sessionId: started.session.session_id }
      if (run !== this.generation) { this.endServer(); return }

      const link = factory()
      this.link = link
      await link.connect(started.media, track, (message) => {
        if (run === this.generation) this.stop('error', message)
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
      this.fail(run, e instanceof Error && e.message ? e.message : 'The speech media link failed.')
    }
  }

  /** Stop on any exit. Tracks stop before anything else; safe to call twice. */
  stop(reason: SpeechStopReason, detail = ''): void {
    if (this.state === 'off' && !this.stream && !this.server && !this.link) return
    this.generation += 1
    this.release()
    this.finish(reason, detail)
  }

  private fail(run: number, detail: string): void {
    if (run !== this.generation) return
    this.stop('error', detail)
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
    if (server) void endSpeech(server.threadId, server.generation, server.sessionId).catch(() => {})
  }

  private finish(reason: SpeechStopReason, detail: string): void {
    this.setState('off')
    this.handlers.onStopped(reason, stopCaption(reason, detail))
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

function refusalText(e: unknown): string {
  const kind = errorKind(e)
  const text = errorText(e)
  // The server's own copy already ends with "Type your message instead."
  return kind ? text.replace(/\s*Type your message instead\.?$/, '') : 'The speech service could not start.'
}
