// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * LC-07 PR3: continuous speech for the Companion Live composer.
 *
 * Offered only while Live is open, the durable thread is bound, a media link
 * is configured and the server answers `GET /speech` (the flag is on). The
 * session stops, and its device tracks with it, when Live closes, the
 * component unmounts, the tab is hidden, the page is left, the project
 * changes or the thread is rebound. The typed composer stays on the same
 * durable thread throughout.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { getSpeechState } from '../api/durableConversations'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import {
  LiveSpeechSession, speechMediaLinkConfigured, type SpeechState, type SpeechStopReason,
} from '../utils/liveSpeechSession'

export interface LiveSpeech {
  available: boolean
  state: SpeechState
  /** The last stop's caption; cleared on the next start. */
  caption: string
  start: () => void
  stop: () => void
}

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
  const [available, setAvailable] = useState(false)
  const session = useRef<LiveSpeechSession | null>(null)
  if (!session.current) {
    session.current = new LiveSpeechSession({
      onState: setState,
      onStopped: (_reason, text) => setCaption(text),
    })
  }
  const stopWith = useCallback((reason: SpeechStopReason) => session.current?.stop(reason), [])

  // Availability: the flag is on server-side (GET /speech answers) and a link exists.
  useEffect(() => {
    setAvailable(false)
    if (!active || !threadId || !speechMediaLinkConfigured()) return
    let live = true
    getSpeechState(threadId).then(() => { if (live) setAvailable(true) }, () => {})
    return () => { live = false }
  }, [active, threadId])

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

  return { available, state, caption, start, stop }
}
