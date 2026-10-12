// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { beforeEach, expect, it, vi } from 'vitest'
import client from './client'
import { getDictationConfig, transcribeRecording } from './dictation'

vi.mock('./client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
beforeEach(() => vi.clearAllMocks())

it('uses authenticated application client and cancellable multipart audio', async () => {
  vi.mocked(client.post).mockResolvedValue({ data: { text: '7.5 MW' } })
  const controller = new AbortController()
  const text = await transcribeRecording(new Blob(['audio'], { type: 'audio/webm' }), 'de', 'GridSpine', controller.signal)
  expect(text).toBe('7.5 MW')
  const [path, body, options] = vi.mocked(client.post).mock.calls[0]
  expect(path).toBe('/dictation/transcribe')
  expect((body as FormData).get('language')).toBe('de')
  expect((body as FormData).get('glossary')).toBe('GridSpine')
  expect(options).toMatchObject({ signal: controller.signal, timeout: 60000 })
  expect(options?.headers).toBeUndefined() // Axios sets the multipart boundary.
})

it('requests safe configuration without a secret payload', async () => {
  vi.mocked(client.get).mockResolvedValue({ data: { available: true } })
  expect(await getDictationConfig()).toEqual({ available: true })
})

it('rejects malformed transcript response', async () => {
  vi.mocked(client.post).mockResolvedValue({ data: { text: null } })
  await expect(transcribeRecording(new Blob(['audio']), 'auto', '', new AbortController().signal)).rejects.toThrow('Invalid transcription')
})
