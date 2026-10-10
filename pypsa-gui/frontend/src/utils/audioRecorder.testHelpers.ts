// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { vi } from 'vitest'

export class MockRecorder {
  static instances: MockRecorder[] = []
  static isTypeSupported = vi.fn(() => true)
  state = 'inactive'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  start = vi.fn(() => { this.state = 'recording' })
  stop = vi.fn(() => {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) } as BlobEvent)
    this.onstop?.()
  })
  constructor(public stream: unknown, public options: { mimeType: string }) { MockRecorder.instances.push(this) }
}

export function installRecordingMocks() {
  MockRecorder.instances = []
  MockRecorder.isTypeSupported.mockReturnValue(true)
  const track = { stop: vi.fn(), onended: null as (() => void) | null }
  const stream = { getTracks: () => [track] } as unknown as MediaStream
  const getUserMedia = vi.fn().mockResolvedValue(stream)
  const enumerateDevices = vi.fn().mockResolvedValue([{ deviceId: 'mic-a', label: 'Desk microphone', kind: 'audioinput' }])
  vi.stubGlobal('MediaRecorder', MockRecorder)
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia, enumerateDevices, addEventListener: vi.fn(), removeEventListener: vi.fn() }, configurable: true })
  return { track, stream, getUserMedia, enumerateDevices }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
