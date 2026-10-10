// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react'
import { formatApiDetail } from '../api/client'
import { transcribeRecording } from '../api/dictation'
import { AudioRecordingSession, canRecordAudio } from '../utils/audioRecorder'

export function useAudioDictation(language: string, glossary: string) {
  const [state, setState] = useState<'idle' | 'starting' | 'recording'>('idle')
  const [processing, setProcessing] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const session = useRef<AudioRecordingSession | null>(null)
  const request = useRef<AbortController | null>(null)
  const retryClip = useRef<Blob | null>(null)
  const options = useRef({ language, glossary })
  options.current = { language, glossary }

  const transcribe = useCallback(async (audio: Blob) => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    retryClip.current = audio
    setProcessing(true)
    setError('')
    try {
      const { language: hint, glossary: terms } = options.current
      const text = await transcribeRecording(audio, hint, terms, controller.signal)
      if (controller.signal.aborted || request.current !== controller) return
      if (!text.trim()) {
        setError('No speech was recognized. Try speaking closer to the microphone.')
        return
      }
      setTranscript(previous => previous ? `${previous}\n${text}` : text)
      retryClip.current = null
    } catch (error) {
      if (controller.signal.aborted || request.current !== controller) return
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(formatApiDetail(detail, error instanceof Error ? error.message : 'Transcription failed.'))
    } finally {
      if (request.current === controller) {
        request.current = null
        setProcessing(false)
      }
    }
  }, [])

  const cancel = useCallback(() => {
    session.current?.cancel()
    session.current = null
    request.current?.abort()
    request.current = null
    retryClip.current = null
    setState('idle')
    setProcessing(false)
    setTranscript('')
    setError('')
    setLevel(0)
    setSeconds(0)
  }, [])

  const start = useCallback((deviceId: string) => {
    if (request.current) return
    setError('')
    setSeconds(0)
    setLevel(0)
    retryClip.current = null
    session.current ??= new AudioRecordingSession({
      onClip: audio => { void transcribe(audio) },
      onState: setState,
      onMeter: (nextLevel, nextSeconds) => { setLevel(nextLevel); setSeconds(nextSeconds) },
      onError: setError,
    })
    void session.current.start(deviceId)
  }, [transcribe])

  useEffect(() => {
    const hide = () => { if (document.hidden) cancel() }
    document.addEventListener('visibilitychange', hide)
    return () => {
      document.removeEventListener('visibilitychange', hide)
      cancel()
    }
  }, [cancel])

  return {
    supported: canRecordAudio(), state, processing, transcript, setTranscript,
    error, seconds, level, start, finish: () => session.current?.finish(), cancel,
    canRetry: !!retryClip.current, retry: () => { if (retryClip.current) void transcribe(retryClip.current) },
  }
}
