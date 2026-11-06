// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * LC-07 PR3: continuous speech for the Companion Live composer.
 *
 * Offered only while Live is open, the durable thread is bound, a media link
 * is configured and the server answers `GET /speech` (the flag is on); with
 * the flag on and no link, `blocked` gives the visible reason. The
 * session stops, and its device tracks with it, when Live closes, the
 * component unmounts, the tab is hidden, the page is left, the project
 * changes or the thread is rebound. The typed composer stays on the same
 * durable thread throughout.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { getSpeechState, type SpeechNotice } from '../api/durableConversations'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import {
  LISTENING_CAPTION, LiveSpeechSession, speechMediaLinkConfigured, type SpeechState, type SpeechStopReason,
} from '../utils/liveSpeechSession'

export interface LiveSpeech {
  available: boolean
  /** The visible reason voice stays disabled although the server flag is on (no media link). */
  blocked: string
  state: SpeechState
  /** The listening line, else the last stop's line; cleared on the next start. */
  caption: string
  start: () => void
  stop: () => void
}

const NOTICE_POLL_MS = 2000
const NOTICE_STATUSES = new Set(['echo_ignored', 'clarify', 'not_an_approval'])

function speechLanguage(): string {
  const tag = typeof navigator !== 'undefined' ? navigator.language : ''
  return /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(tag ?? '') ? tag : 'en'
}

export function useLiveSpeech(active: boolean): LiveSpeech {
  const threadId = useChatStore((s) => s.durableThreadId)
  const generation = useChatStore((s) => s.utteranceGeneration)
  const project = useUIStore((s) => s.currentProject)
  const [state, setState] = useState<SpeechState>('off')
  const [caption, setCaption] = useState('')
  const [flagOn, setFlagOn] = useState(false)
  // LC-07c: the newest unseen server notice (fixed message, never transcript text).
  const [notice, setNotice] = useState('')
  const seenSeq = useRef(-1)
  const session = useRef<LiveSpeechSession | null>(null)
  if (!session.current) {
    session.current = new LiveSpeechSession({
      onState: setState,
      onStopped: (_reason, text) => setCaption(text),
    })
  }
  const stopWith = useCallback((reason: SpeechStopReason) => session.current?.stop(reason), [])

  // The flag is on server-side when GET /speech answers; voice needs a media link as well.
  useEffect(() => {
    setFlagOn(false)
    if (!active || !threadId) return
    let live = true
    getSpeechState(threadId).then(() => { if (live) setFlagOn(true) }, () => {})
    return () => { live = false }
  }, [active, threadId])
  // Notices: polled only while listening; each seq shows once; cleared with the session.
  useEffect(() => {
    seenSeq.current = -1
    setNotice('')
    if (state !== 'listening' || !threadId) return
    let live = true
    const poll = () => {
      getSpeechState(threadId).then((r) => {
        if (!live) return
        const fresh = (r.notices ?? [])
          .filter((n: SpeechNotice) => NOTICE_STATUSES.has(n.status) && typeof n.message === 'string'
            && n.generation === generation && n.seq > seenSeq.current)
          .sort((a, b) => b.seq - a.seq)[0]
        if (fresh) { seenSeq.current = fresh.seq; setNotice(fresh.message) }
      }, () => {})
    }
    const id = window.setInterval(poll, NOTICE_POLL_MS)
    return () => { live = false; window.clearInterval(id) }
  }, [state, threadId, generation])
  const linked = speechMediaLinkConfigured()
  const available = flagOn && linked
  const blocked = flagOn && !linked
    ? 'Voice unavailable: this app has no media connection set up, so use reviewed dictation.' : ''

  // Live closed, unmount.
  useEffect(() => {
    if (!active) stopWith('closed')
  }, [active, stopWith])
  useEffect(() => () => stopWith('unmount'), [stopWith])

  // Project switch and rebind (the thread or its generation changed).
  const bound = useRef({ project, threadId, generation })
  useEffect(() => {
    const prev = bound.current
    bound.current = { project, threadId, generation }
    if (prev.project !== project) stopWith('project')
    else if (prev.threadId !== threadId || prev.generation !== generation) stopWith('rebind')
  }, [project, threadId, generation, stopWith])

  // Tab hidden, page left.
  useEffect(() => {
    const onVisibility = () => { if (document.visibilityState === 'hidden') stopWith('hidden') }
    const onPageHide = () => stopWith('pagehide')
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [stopWith])

  const start = useCallback(() => {
    const { durableThreadId, utteranceGeneration } = useChatStore.getState()
    if (!durableThreadId) return
    setCaption('')
    void session.current?.start(durableThreadId, utteranceGeneration, speechLanguage())
  }, [])
  const stop = useCallback(() => stopWith('ended'), [stopWith])

  return { available, blocked, state, caption: state === 'listening' ? (notice || LISTENING_CAPTION) : caption, start, stop }
}
