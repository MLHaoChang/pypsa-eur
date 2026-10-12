// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import client from './client'

export type DictationConfig = {
  available: boolean
  model: string
  languages: string[]
  max_bytes: number
  max_recording_seconds: number
}

export async function getDictationConfig(): Promise<DictationConfig> {
  return (await client.get<DictationConfig>('/dictation/config', { skipErrorToast: true })).data
}

export async function transcribeRecording(
  audio: Blob, language: string, glossary: string, signal: AbortSignal,
): Promise<string> {
  const body = new FormData()
  body.append('file', audio, 'recording')
  body.append('language', language)
  body.append('glossary', glossary)
  const response = await client.post<{ text: string }>('/dictation/transcribe', body, {
    signal, timeout: 60000, skipErrorToast: true,
  })
  if (typeof response.data.text !== 'string') throw new Error('Invalid transcription response.')
  return response.data.text
}
