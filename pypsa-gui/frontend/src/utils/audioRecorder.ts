// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/** Temporary recording only: no persistence, no network and no chat dependency. */
export const RECORDING_MAX_BYTES = 10 * 1024 * 1024
export const RECORDING_MAX_SECONDS = 120

export type RecordingHandlers = {
  onClip: (audio: Blob) => void
  onState: (state: 'idle' | 'starting' | 'recording') => void
  onMeter: (level: number, seconds: number) => void
  onError: (message: string) => void
}

export function canRecordAudio(): boolean {
  return typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

export function recordingError(error: unknown): string {
  // DOMException may come from another realm and need not be instanceof Error.
  const name = error && typeof error === 'object' && 'name' in error ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'Microphone access denied. Allow this app in browser or system microphone settings, then retry.'
  if (name === 'NotFoundError') return 'No microphone found. Connect a microphone and retry.'
  if (name === 'NotReadableError') return 'The microphone is busy or unavailable. Check other apps and retry.'
  return 'Could not record audio. Try another microphone or browser dictation.'
}

export class AudioRecordingSession {
  private generation = 0
  private stream: MediaStream | null = null
  private recorder: MediaRecorder | null = null
  private timer: ReturnType<typeof setInterval> | null = null
  private context: AudioContext | null = null

  constructor(private readonly handlers: RecordingHandlers) {}

  async start(deviceId = ''): Promise<void> {
    this.cancel()
    const generation = this.generation
    this.handlers.onState('starting')
    let stream: MediaStream | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      })
      if (generation !== this.generation) {
        stream.getTracks().forEach(track => track.stop())
        return
      }
      this.stream = stream
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type => MediaRecorder.isTypeSupported(type))
      if (!mime) throw new Error('No supported recording format')
      const recorder = new MediaRecorder(stream, { mimeType: mime })
      this.recorder = recorder
      const chunks: Blob[] = []
      let size = 0
      let meterSamples = 0
      let peakLevel = 0
      recorder.ondataavailable = event => {
        if (generation !== this.generation || !event.data.size) return
        size += event.data.size
        if (size > RECORDING_MAX_BYTES) {
          this.cancel()
          this.handlers.onError('Recording exceeded 10 MB. Record a shorter segment.')
          return
        }
        chunks.push(event.data)
      }
      recorder.onerror = () => {
        if (generation !== this.generation) return
        this.cancel()
        this.handlers.onError('Recording failed. Try again or use browser dictation.')
      }
      recorder.onstop = () => {
        if (generation !== this.generation) return
        this.release()
        this.recorder = null
        this.handlers.onState('idle')
        // Reject a completely flat input when a meter was available. This is
        // a signal check, not speech/language detection; quiet speech still
        // needs review and noisy recordings are handled by transcription.
        if (meterSamples >= 3 && peakLevel < 0.001) {
          this.handlers.onError('No audible sound was recorded. Check the microphone and try again.')
          return
        }
        if (size) this.handlers.onClip(new Blob(chunks, { type: mime }))
        else this.handlers.onError('The recording is empty. Try again.')
      }
      stream.getTracks().forEach(track => { track.onended = () => this.finish() })
      // Meter failure must not prevent recording; some WebViews lack Web Audio.
      let analyser: AnalyserNode | null = null
      try {
        if (typeof AudioContext !== 'undefined') {
          this.context = new AudioContext()
          analyser = this.context.createAnalyser()
          analyser.fftSize = 256
          this.context.createMediaStreamSource(stream).connect(analyser)
          void this.context.resume().catch(() => {})
        }
      } catch { /* recording can work without a meter */ }
      const waveform = new Uint8Array(256)
      const started = performance.now()
      recorder.start(1000)
      this.handlers.onState('recording')
      this.timer = setInterval(() => {
        const seconds = (performance.now() - started) / 1000
        let level = 0
        if (analyser) {
          analyser.getByteTimeDomainData(waveform)
          level = Math.min(1, Math.sqrt(waveform.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / waveform.length) * 4)
          meterSamples++
          peakLevel = Math.max(peakLevel, level)
        }
        this.handlers.onMeter(level, seconds)
        if (seconds >= RECORDING_MAX_SECONDS) this.finish()
      }, 200)
    } catch (error) {
      stream?.getTracks().forEach(track => track.stop())
      if (generation !== this.generation) return
      this.cancel()
      this.handlers.onError(recordingError(error))
    }
  }

  /** Finish a segment for transcription. Tracks close before async onstop. */
  finish(): void {
    const recorder = this.recorder
    if (!recorder || recorder.state === 'inactive') return
    this.release()
    try { recorder.stop() } catch {
      this.cancel()
      this.handlers.onError('Could not finish this recording. Please retry.')
    }
  }

  /** Discard on cancel, project change, hide or unmount; suppress late events. */
  cancel(): void {
    this.generation++
    const recorder = this.recorder
    this.recorder = null
    if (recorder) {
      recorder.onstop = null
      recorder.onerror = null
      recorder.ondataavailable = null
      try { if (recorder.state !== 'inactive') recorder.stop() } catch { /* already closed */ }
    }
    this.release()
    this.handlers.onState('idle')
  }

  private release(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
    this.stream?.getTracks().forEach(track => { track.onended = null; track.stop() })
    this.stream = null
    if (this.context) void this.context.close().catch(() => {})
    this.context = null
  }
}
