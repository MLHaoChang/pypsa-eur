// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import DictationControls from './DictationControls'
import { getDictationConfig, transcribeRecording } from '../api/dictation'
import { deferred, installRecordingMocks, MockRecorder } from '../utils/audioRecorder.testHelpers'

vi.mock('../api/dictation', () => ({ getDictationConfig: vi.fn(), transcribeRecording: vi.fn() }))

const speech = { available: true, supported: true, listening: false, interim: '', permissionDenied: false, toggle: vi.fn(), stop: vi.fn() }
const insert = vi.fn()
let recording: ReturnType<typeof installRecordingMocks>
const defaults = { speech, language: 'auto', onLanguage: vi.fn(), onInsert: insert, streaming: false, dockOpen: true, project: 'Demo', coarsePointer: false, panelRequested: false, onPanelRequestConsumed: vi.fn() }

function mount(overrides = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const result = render(<DictationControls {...defaults} {...overrides} />, { wrapper })
  return { ...result, update: (props: object) => result.rerender(<DictationControls {...defaults} {...props} />) }
}

async function open() {
  fireEvent.click(screen.getByTestId('dictation-settings'))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Record' }) as HTMLButtonElement).disabled).toBe(false))
}

beforeEach(() => {
  vi.clearAllMocks()
  recording = installRecordingMocks()
  vi.mocked(getDictationConfig).mockResolvedValue({ available: true, languages: ['en', 'de', 'zh'], model: 'gpt-4o-mini-transcribe', max_bytes: 10485760, max_recording_seconds: 120 })
  vi.mocked(transcribeRecording).mockResolvedValue('Bus A-12: 7.5 MW; not 75 MWh.')
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('opens the review panel when asked and does not start the microphone', async () => {
  const consumed = vi.fn()
  mount({ panelRequested: true, onPanelRequestConsumed: consumed })
  expect(await screen.findByTestId('dictation-panel')).toBeTruthy()
  expect(speech.toggle).not.toHaveBeenCalled()
  expect(recording.getUserMedia).not.toHaveBeenCalled()
  expect(consumed).toHaveBeenCalledOnce()
})

it('records, pauses, reviews and explicitly inserts an editable transcript', async () => {
  mount()
  await open()
  fireEvent.change(screen.getByLabelText('Microphone'), { target: { value: 'mic-a' } })
  fireEvent.change(screen.getByLabelText('Dictation terminology'), { target: { value: 'Bus A-12, MW, MWh' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Pause / transcribe' })
  expect(recording.getUserMedia).toHaveBeenCalledWith({ audio: { deviceId: { exact: 'mic-a' } } })
  fireEvent.click(screen.getByRole('button', { name: 'Pause / transcribe' }))
  await waitFor(() => expect((screen.getByLabelText('Review transcript') as HTMLTextAreaElement).value).toBe('Bus A-12: 7.5 MW; not 75 MWh.'))
  expect(transcribeRecording).toHaveBeenCalledWith(expect.any(Blob), 'auto', 'Bus A-12, MW, MWh', expect.any(AbortSignal))
  expect(recording.track.stop).toHaveBeenCalled()
  expect(insert).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Review transcript'), { target: { value: 'Bus A-12: 8.5 MW.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Insert into chat' }))
  expect(insert).toHaveBeenCalledWith('Bus A-12: 8.5 MW.')
  expect(screen.queryByTestId('dictation-panel')).toBeNull()
})

it('supports manual language and resumes as a new segment without duplicating prior text', async () => {
  mount({ language: 'de' })
  await open()
  for (const label of ['Record', 'Resume recording']) {
    fireEvent.click(screen.getByRole('button', { name: label }))
    await screen.findByRole('button', { name: 'Pause / transcribe' })
    fireEvent.click(screen.getByRole('button', { name: 'Pause / transcribe' }))
    await screen.findByRole('button', { name: 'Resume recording' })
    await waitFor(() => expect((screen.getByRole('button', { name: 'Insert into chat' }) as HTMLButtonElement).disabled).toBe(false))
  }
  expect(transcribeRecording).toHaveBeenCalledTimes(2)
  expect(vi.mocked(transcribeRecording).mock.calls.every(call => call[1] === 'de')).toBe(true)
  expect((screen.getByLabelText('Review transcript') as HTMLTextAreaElement).value).toBe('Bus A-12: 7.5 MW; not 75 MWh.\nBus A-12: 7.5 MW; not 75 MWh.')
  expect(insert).not.toHaveBeenCalled()
})

it.each([{ project: 'Other' }, { dockOpen: false }, { streaming: true }])('discards recording when context changes: %j', async change => {
  const view = mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Pause / transcribe' })
  view.update(change)
  expect(recording.track.stop).toHaveBeenCalled()
  expect(transcribeRecording).not.toHaveBeenCalled()
  expect(insert).not.toHaveBeenCalled()
})

it('aborts pending transcription and ignores a late response after discard', async () => {
  const pending = deferred<string>()
  vi.mocked(transcribeRecording).mockReturnValue(pending.promise)
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Pause / transcribe' })
  fireEvent.click(screen.getByRole('button', { name: 'Pause / transcribe' }))
  const signal = vi.mocked(transcribeRecording).mock.calls[0][3]
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
  expect(signal.aborted).toBe(true)
  await act(async () => { pending.resolve('Stale transcript'); await pending.promise })
  expect(insert).not.toHaveBeenCalled()
  await open()
  expect((screen.getByLabelText('Review transcript') as HTMLTextAreaElement).value).toBe('')
})

it('shows safe provider errors and retries only when requested', async () => {
  vi.mocked(transcribeRecording).mockRejectedValueOnce({ response: { data: { detail: { code: 'provider_auth', message: 'Check the transcription API key.' } } } })
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Pause / transcribe' })
  fireEvent.click(screen.getByRole('button', { name: 'Pause / transcribe' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Check the transcription API key.')
  expect(transcribeRecording).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: 'Retry transcription' }))
  await waitFor(() => expect((screen.getByLabelText('Review transcript') as HTMLTextAreaElement).value).toBe('Bus A-12: 7.5 MW; not 75 MWh.'))
  expect(transcribeRecording).toHaveBeenCalledTimes(2)
})

it('offers explicit browser fallback and explains browser Auto language', async () => {
  vi.mocked(getDictationConfig).mockResolvedValue({ available: false, languages: ['en', 'de', 'zh'], model: '', max_bytes: 10485760, max_recording_seconds: 120 })
  mount()
  fireEvent.click(screen.getByTestId('dictation-settings'))
  await screen.findByText(/needs an OpenAI API key/)
  fireEvent.change(screen.getByLabelText('Dictation method'), { target: { value: 'browser' } })
  expect(screen.getByText(/does not detect the spoken language/)).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Start browser dictation' }))
  expect(speech.toggle).toHaveBeenCalledOnce()
  expect(transcribeRecording).not.toHaveBeenCalled()
})

it('opens setup rather than silently changing the recognition method when AI is unavailable', async () => {
  vi.mocked(getDictationConfig).mockResolvedValue({ available: false, languages: ['en', 'de', 'zh'], model: '', max_bytes: 10485760, max_recording_seconds: 120 })
  mount()
  await waitFor(() => expect(getDictationConfig).toHaveBeenCalled())
  fireEvent.click(screen.getByTestId('chat-mic'))
  await screen.findByText(/needs an OpenAI API key/)
  expect(speech.toggle).not.toHaveBeenCalled()
  expect(recording.getUserMedia).not.toHaveBeenCalled()
  expect(transcribeRecording).not.toHaveBeenCalled()
})

it('handles microphone permission denial without a paid call', async () => {
  recording.getUserMedia.mockRejectedValue(new DOMException('no', 'NotAllowedError'))
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  expect((await screen.findByRole('alert')).textContent).toContain('access denied')
  expect(transcribeRecording).not.toHaveBeenCalled()
})

it('the mic stop button cancels a pending permission request and closes late-granted tracks', async () => {
  const permission = deferred<MediaStream>()
  recording.getUserMedia.mockReturnValue(permission.promise)
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Opening microphone…' })
  fireEvent.click(screen.getByTestId('chat-mic'))
  await act(async () => { permission.resolve(recording.stream); await permission.promise })
  expect(recording.track.stop).toHaveBeenCalled()
  expect(transcribeRecording).not.toHaveBeenCalled()
  expect(MockRecorder.instances).toHaveLength(0)
})

it('Escape closes a recording even when focus remains on the mic button', async () => {
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Pause / transcribe' })
  fireEvent.keyDown(screen.getByTestId('chat-mic'), { key: 'Escape' })
  expect(recording.track.stop).toHaveBeenCalled()
  expect(transcribeRecording).not.toHaveBeenCalled()
})

it('unmount releases active microphone tracks', async () => {
  const view = mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Record' }))
  await screen.findByRole('button', { name: 'Pause / transcribe' })
  view.unmount()
  expect(recording.track.stop).toHaveBeenCalled()
  expect(MockRecorder.instances[0].onstop).toBeNull()
})
