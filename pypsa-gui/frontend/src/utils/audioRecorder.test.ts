// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AudioRecordingSession, RECORDING_MAX_BYTES, recordingError } from './audioRecorder'
import { deferred, installRecordingMocks, MockRecorder } from './audioRecorder.testHelpers'

describe('temporary audio recorder', () => {
  let mocks: ReturnType<typeof installRecordingMocks>
  const makeHandlers = () => ({ onClip: vi.fn<(audio: Blob) => void>(), onState: vi.fn<(state: 'idle' | 'starting' | 'recording') => void>(), onMeter: vi.fn<(level: number, seconds: number) => void>(), onError: vi.fn<(message: string) => void>() })
  let handlers: ReturnType<typeof makeHandlers>
  let session: AudioRecordingSession
  beforeEach(() => {
    mocks = installRecordingMocks()
    handlers = makeHandlers()
    session = new AudioRecordingSession(handlers)
  })
  afterEach(() => { session.cancel(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('selects an explicit device and releases microphone before delivering a clip', async () => {
    await session.start('mic-a')
    expect(mocks.getUserMedia).toHaveBeenCalledWith({ audio: { deviceId: { exact: 'mic-a' } } })
    handlers.onClip.mockImplementation(() => expect(mocks.track.stop).toHaveBeenCalled())
    session.finish()
    expect(handlers.onClip).toHaveBeenCalledOnce()
    expect(handlers.onClip.mock.calls[0][0].type).toBe('audio/webm;codecs=opus')
    expect(mocks.track.onended).toBeNull()
  })

  it('cancel discards a recording and suppresses late completion callbacks', async () => {
    await session.start()
    const recorder = MockRecorder.instances[0]
    const lateStop = recorder.onstop
    const lateData = recorder.ondataavailable
    session.cancel()
    lateData?.({ data: new Blob(['late audio']) } as BlobEvent)
    lateStop?.()
    expect(handlers.onClip).not.toHaveBeenCalled()
    expect(mocks.track.stop).toHaveBeenCalled()
    expect(recorder.onstop).toBeNull()
  })

  it('cancel while permission is pending closes late-granted tracks', async () => {
    const permission = deferred<MediaStream>()
    mocks.getUserMedia.mockReturnValue(permission.promise)
    const starting = session.start()
    session.cancel()
    permission.resolve(mocks.stream)
    await starting
    expect(mocks.track.stop).toHaveBeenCalled()
    expect(MockRecorder.instances).toHaveLength(0)
    expect(handlers.onClip).not.toHaveBeenCalled()
  })

  it('reports permission denial without starting or restarting recording', async () => {
    mocks.getUserMedia.mockRejectedValue(new DOMException('no', 'NotAllowedError'))
    await session.start()
    expect(handlers.onError).toHaveBeenCalledWith(expect.stringContaining('access denied'))
    expect(MockRecorder.instances).toHaveLength(0)
    expect(handlers.onState).toHaveBeenLastCalledWith('idle')
  })

  it('refuses recordings larger than the byte cap', async () => {
    await session.start()
    MockRecorder.instances[0].ondataavailable?.({ data: new Blob([new Uint8Array(RECORDING_MAX_BYTES + 1)]) } as BlobEvent)
    expect(handlers.onError).toHaveBeenCalledWith(expect.stringContaining('10 MB'))
    expect(handlers.onClip).not.toHaveBeenCalled()
    expect(mocks.track.stop).toHaveBeenCalled()
  })

  it('finishes automatically at the segment time limit', async () => {
    vi.useFakeTimers()
    await session.start()
    await vi.advanceTimersByTimeAsync(120000)
    expect(handlers.onClip).toHaveBeenCalledOnce()
    expect(mocks.track.stop).toHaveBeenCalled()
    expect(MockRecorder.instances[0].start).toHaveBeenCalledOnce()
  })

  it('handles recording failure and unsupported formats without retaining tracks', async () => {
    MockRecorder.isTypeSupported.mockReturnValue(false)
    await session.start()
    expect(mocks.track.stop).toHaveBeenCalled()
    expect(handlers.onError).toHaveBeenCalled()
  })

  it('stops on device removal rather than reopening the microphone', async () => {
    await session.start()
    mocks.track.onended?.()
    expect(handlers.onClip).toHaveBeenCalledOnce()
    expect(MockRecorder.instances).toHaveLength(1)
  })

  it('closes the meter audio context and timer on cancel', async () => {
    const close = vi.fn().mockResolvedValue(undefined)
    const resume = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('AudioContext', class {
      close = close
      resume = resume
      createAnalyser = () => ({ fftSize: 0, getByteTimeDomainData: (array: Uint8Array) => array.fill(128) })
      createMediaStreamSource = () => ({ connect: vi.fn() })
    })
    await session.start()
    session.cancel()
    expect(close).toHaveBeenCalledOnce()
    expect(mocks.track.stop).toHaveBeenCalled()
  })

  it('discards completely flat microphone input without transcribing it', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('AudioContext', class {
      close = vi.fn().mockResolvedValue(undefined)
      resume = vi.fn().mockResolvedValue(undefined)
      createAnalyser = () => ({ fftSize: 0, getByteTimeDomainData: (array: Uint8Array) => array.fill(128) })
      createMediaStreamSource = () => ({ connect: vi.fn() })
    })
    await session.start()
    await vi.advanceTimersByTimeAsync(1000)
    session.finish()
    expect(handlers.onClip).not.toHaveBeenCalled()
    expect(handlers.onError).toHaveBeenCalledWith(expect.stringContaining('No audible sound'))
    expect(mocks.track.stop).toHaveBeenCalled()
  })

  it.each(['NotFoundError', 'NotReadableError', 'SecurityError'])('gives actionable %s errors', name => {
    expect(recordingError(new DOMException('', name))).not.toContain('Could not record')
  })
})
