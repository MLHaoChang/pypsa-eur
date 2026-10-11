// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getDictationConfig } from '../api/dictation'
import { useAudioDictation } from '../hooks/useAudioDictation'
import type { UseSpeechToTextResult } from '../hooks/useSpeechToText'

export const DICTATION_LANGUAGES: Record<string, string> = {
  auto: 'Auto', en: 'English', de: 'Deutsch', zh: '中文', fr: 'Français',
  es: 'Español', it: 'Italiano', pt: 'Português', ja: '日本語', ko: '한국어',
  ar: 'العربية', hi: 'हिन्दी', nl: 'Nederlands', pl: 'Polski', uk: 'Українська',
}

type Props = {
  speech: UseSpeechToTextResult
  language: string
  onLanguage: (language: string) => void
  onInsert: (text: string) => void
  streaming: boolean
  dockOpen: boolean
  project: string | null
  coarsePointer: boolean
  panelRequested: boolean
  onPanelRequestConsumed: () => void
}

export default function DictationControls({ speech, language, onLanguage, onInsert, streaming, dockOpen, project, coarsePointer, panelRequested, onPanelRequestConsumed }: Props) {
  const [open, setOpen] = useState(false)
  const [engine, setEngine] = useState<'ai' | 'browser'>('ai')
  const [glossary, setGlossary] = useState('PyPSA, GridSpine, MW, MWh')
  const [device, setDevice] = useState('')
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const audio = useAudioDictation(language, glossary)
  const config = useQuery({ queryKey: ['dictation-config'], queryFn: getDictationConfig, enabled: dockOpen, retry: false, staleTime: 60000 })
  const aiReady = audio.supported && config.data?.available === true
  const aiSelected = engine === 'ai' && aiReady
  const recording = audio.state !== 'idle'
  const busy = recording || audio.processing
  const available = engine === 'ai' ? audio.supported || speech.available : speech.available

  useEffect(() => {
    audio.cancel()
    setOpen(false)
  // Reset on project identity, even when the panel remains mounted.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project])
  useEffect(() => {
    if (!panelRequested) return
    if (dockOpen && !streaming) setOpen(true)
    onPanelRequestConsumed()
  }, [panelRequested, dockOpen, streaming, onPanelRequestConsumed])
  useEffect(() => {
    if (!dockOpen || streaming) { audio.cancel(); setOpen(false) }
  }, [dockOpen, streaming, audio.cancel])
  useEffect(() => {
    if (!open) return
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      audio.cancel()
      setOpen(false)
    }
    window.addEventListener('keydown', escape, true)
    return () => window.removeEventListener('keydown', escape, true)
  }, [open, audio.cancel])
  useEffect(() => {
    if (!open || !navigator.mediaDevices?.enumerateDevices) return
    let active = true
    const refresh = () => {
      void navigator.mediaDevices.enumerateDevices().then(all => {
        if (active) setDevices(all.filter(entry => entry.kind === 'audioinput'))
      }).catch(() => {})
    }
    refresh()
    navigator.mediaDevices.addEventListener?.('devicechange', refresh)
    return () => { active = false; navigator.mediaDevices.removeEventListener?.('devicechange', refresh) }
  }, [open, audio.state])

  const cancel = () => { audio.cancel(); setOpen(false) }
  const mic = () => {
    if (speech.listening) { speech.stop(); return }
    if (audio.state === 'starting') { audio.cancel(); return }
    if (engine === 'ai') {
      setOpen(true)
      if (aiReady) {
        if (recording) audio.finish()
        else if (!audio.processing) audio.start(device)
      }
    } else speech.toggle()
  }
  const fallback = () => {
    audio.cancel()
    setEngine('browser')
    setOpen(false)
    speech.toggle()
  }

  return <>
    <button
      className={`${coarsePointer ? 'min-w-[44px] min-h-[44px] text-lg' : 'px-2 py-1 text-[10px]'} rounded border ${speech.listening || recording ? 'bg-accent/15 border-accent text-accent' : 'bg-bg-3/40 hover:bg-bg-3 border-border text-muted'} disabled:opacity-50`}
      onClick={mic} disabled={!available || streaming || audio.processing}
      title={!available ? 'Open dictation settings to check microphone support' : speech.listening || recording ? 'Pause voice input' : aiSelected ? 'Start AI dictation' : engine === 'ai' ? 'Configure AI dictation or choose browser recognition' : 'Start browser dictation'}
      aria-label={speech.listening || recording ? 'Stop voice input' : 'Start voice input'}
      aria-pressed={speech.listening || recording} data-testid="chat-mic"
    >{speech.listening || recording ? '■' : '🎙'}</button>
    <button className="px-1 py-1 text-[10px] rounded border border-border text-muted hover:bg-bg-3"
      aria-label="Dictation settings" aria-expanded={open} onClick={() => { speech.stop(); setOpen(previous => !previous); if (open) audio.cancel() }} disabled={streaming}
      data-testid="dictation-settings">Voice</button>
    {open && <div className="absolute bottom-full left-2 right-2 mb-2 z-40 rounded border border-border bg-bg-2 p-3 shadow-xl max-h-[70vh] overflow-y-auto" role="dialog" aria-label="Dictation" data-testid="dictation-panel"
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel() } }}>
      <div className="flex justify-between items-center mb-2"><strong className="text-sm">Dictation</strong><button onClick={cancel} aria-label="Close dictation">×</button></div>
      <div className="flex gap-2 flex-wrap text-xs">
        <label>Method <select aria-label="Dictation method" className="bg-bg border border-border rounded p-1" value={engine} disabled={busy}
          onChange={event => { audio.cancel(); speech.stop(); setEngine(event.target.value as 'ai' | 'browser') }}>
          <option value="ai">AI transcription</option><option value="browser">Browser recognition</option>
        </select></label>
        <label>Language <select aria-label="Dictation language" className="bg-bg border border-border rounded p-1" value={language} disabled={busy}
          onChange={event => onLanguage(event.target.value)}>
          {Object.entries(DICTATION_LANGUAGES).filter(([code]) => code === 'auto' || !config.data || config.data.languages.includes(code)).map(([code, label]) => <option value={code} key={code}>{label}</option>)}
        </select></label>
      </div>
      {engine === 'ai' ? <>
        <p className="text-[11px] text-muted my-2">Audio is sent to OpenAI when you pause or finish. Review the words, numbers and units before inserting. Chat model selection is independent.</p>
        {!audio.supported && <p role="status" className="text-xs">Audio recording requires a supported browser, a secure connection and microphone permission.</p>}
        {audio.supported && !config.data?.available && <p role="status" className="text-xs">{config.isLoading ? 'Checking AI dictation…' : config.isError ? 'Could not check AI dictation. Retry the connection check or use browser recognition.' : 'AI dictation needs an OpenAI API key in Assistant settings.'}</p>}
        {!config.data?.available && <button className="text-xs underline my-1" onClick={() => { void config.refetch() }}>Check connection again</button>}
        <label className="block text-xs mt-2">Microphone <select aria-label="Microphone" className="bg-bg border border-border rounded p-1 max-w-full" value={device} disabled={busy} onChange={event => setDevice(event.target.value)}>
          <option value="">System default</option>{devices.filter(entry => entry.deviceId).map((entry, index) => <option value={entry.deviceId} key={entry.deviceId}>{entry.label || `Microphone ${index + 1}`}</option>)}
        </select></label>
        <label className="block text-xs mt-2">Terminology <input aria-label="Dictation terminology" className="block w-full bg-bg border border-border rounded p-1" value={glossary} maxLength={500} disabled={busy} onChange={event => setGlossary(event.target.value)} /></label>
        <p className="text-[10px] text-muted">Optional words sent with the recording. No project files or chat history are included.</p>
        <div className="flex gap-2 items-center mt-2 text-xs">
          <button className="border border-border rounded px-2 py-1 disabled:opacity-50" disabled={!aiReady || audio.processing || audio.state === 'starting'} onClick={() => recording ? audio.finish() : audio.start(device)}>
            {audio.state === 'starting' ? 'Opening microphone…' : recording ? 'Pause / transcribe' : audio.transcript ? 'Resume recording' : 'Record'}
          </button>
          <span role="status">{audio.processing ? 'Transcribing…' : recording ? `Recording ${Math.floor(audio.seconds)}s / 120s` : 'Microphone off'}</span>
          {recording && <meter aria-label="Microphone level" min={0} max={1} value={audio.level} className="w-16" />}
        </div>
        <label className="block text-xs mt-2">Review transcript <textarea aria-label="Review transcript" className="block w-full bg-bg border border-border rounded p-2 min-h-[80px]" value={audio.transcript} onChange={event => audio.setTranscript(event.target.value)} /></label>
        <p className="text-[10px] text-muted">Preview appears after each paused segment. Nothing is sent to chat automatically.</p>
        {audio.error && <p role="alert" className="text-xs text-danger mt-2">{audio.error}</p>}
        <div className="flex gap-2 flex-wrap mt-2 text-xs">
          <button className="bg-accent text-bg rounded px-2 py-1 disabled:opacity-50" disabled={busy || !audio.transcript.trim()} onClick={() => { onInsert(audio.transcript); cancel() }}>Insert into chat</button>
          {audio.canRetry && <button disabled={busy} onClick={audio.retry}>Retry transcription</button>}
          <button onClick={cancel}>Discard</button>
          {speech.available && <button disabled={busy} onClick={fallback}>Use browser dictation</button>}
        </div>
      </> : <>
        <p className="text-xs text-muted my-2">Browser recognition inserts words into the editable chat draft. Auto uses your browser language ({typeof navigator !== 'undefined' ? navigator.language : 'en-US'}); it does not detect the spoken language.</p>
        {!speech.available && <p role="alert" className="text-xs">{speech.permissionDenied ? 'Microphone permission denied. Allow microphone access in browser or system settings.' : 'Browser recognition is unavailable. Try AI transcription or a supported browser.'}</p>}
        <button className="text-xs border border-border rounded px-2 py-1" disabled={!speech.available} onClick={() => { setOpen(false); speech.toggle() }}>Start browser dictation</button>
      </>}
    </div>}
  </>
}
