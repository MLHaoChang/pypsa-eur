// Assistant memory 04b: "Review my recent work" on the Proposals tab, its job
// status, and the automatic-review opt-in (both off until the user turns them on).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import DreamReview from './DreamReview'
import { dreamStatusWords, type DreamJob } from '../../api/memory'

const latest = vi.fn()
const start = vi.fn()
const settings = vi.fn()
const setSettings = vi.fn()

vi.mock('../../api/memory', async (orig) => {
  const real = await orig<typeof import('../../api/memory')>()
  return {
    ...real,
    dreamApi: {
      latest: () => latest(),
      start: () => start(),
      settings: () => settings(),
      setSettings: (s: unknown) => setSettings(s),
    },
  }
})

function job(over: Partial<DreamJob> = {}): DreamJob {
  return {
    id: 'j1', trigger: 'manual', status: 'done', error_kind: null,
    counts: {}, spent: {}, budget: {},
    created_at: '2026-11-11T00:00:00Z', started_at: null, finished_at: null,
    ...over,
  }
}

const OFF = { nightly: false, after_sessions: false, nightly_at: '03:00 Europe/Berlin', sessions_needed: 10 }

let qc: QueryClient
function renderIt() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><DreamReview /></QueryClientProvider>)
}

beforeEach(() => {
  latest.mockResolvedValue(null)
  settings.mockResolvedValue(OFF)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('DreamReview', () => {
  it('starts a review and shows it queued, with the button off while it is live', async () => {
    start.mockResolvedValue(job({ status: 'queued' }))
    renderIt()
    const button = await screen.findByRole('button', { name: 'Review my recent work' })
    await waitFor(() => expect((button as HTMLInputElement).disabled).toBe(false))
    expect(screen.getByTestId('dream-status').textContent).toContain('Not reviewed yet.')
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByTestId('dream-status').textContent).toContain('Review queued'))
    expect(start).toHaveBeenCalledTimes(1)
    expect((button as HTMLInputElement).disabled).toBe(true)
  })

  it('shows a refusal in place when a review is already running', async () => {
    start.mockRejectedValue({ response: { data: { detail: {
      error_kind: 'dream_job_live', message: 'a review of your recent work is already running' } } } })
    renderIt()
    const button = await screen.findByRole('button', { name: 'Review my recent work' })
    await waitFor(() => expect((button as HTMLInputElement).disabled).toBe(false))
    fireEvent.click(button)
    expect((await screen.findByRole('alert')).textContent).toContain('already running')
  })

  it('refreshes the proposals when a live review finishes', async () => {
    latest.mockResolvedValueOnce(job({ status: 'running', counts: { analyzed: 2 } }))
    renderIt()
    await waitFor(() => expect(screen.getByTestId('dream-status').textContent).toContain('2 of your chats and runs read so far'))
    expect((screen.getByRole('button', { name: 'Review my recent work' }) as HTMLInputElement).disabled).toBe(true)
    const spy = vi.spyOn(qc, 'invalidateQueries')
    latest.mockResolvedValue(job({ status: 'done', counts: { proposed: 3 } }))
    await qc.refetchQueries({ queryKey: ['memory', 'dream'] })
    await waitFor(() => expect(screen.getByTestId('dream-status').textContent).toContain('3 proposals below'))
    expect(spy).toHaveBeenCalledWith({ queryKey: ['memory', 'proposals'] })
  })

  it('shows both automatic reviews off by default and saves a change', async () => {
    setSettings.mockResolvedValue({ ...OFF, nightly: true })
    renderIt()
    const nightly = await screen.findByRole('checkbox', { name: 'Every night at 03:00' })
    const chats = screen.getByRole('checkbox', { name: 'After 10 new chats' })
    await waitFor(() => expect((nightly as HTMLInputElement).disabled).toBe(false))
    expect((nightly as HTMLInputElement).checked).toBe(false)
    expect((chats as HTMLInputElement).checked).toBe(false)
    fireEvent.click(nightly)
    await waitFor(() => expect((nightly as HTMLInputElement).checked).toBe(true))
    expect(setSettings).toHaveBeenCalledWith({ nightly: true })
    expect((chats as HTMLInputElement).checked).toBe(false)
  })
})

describe('dreamStatusWords', () => {
  it('names each outcome in the user\'s words', () => {
    expect(dreamStatusWords(job({ status: 'done' }))).toBe('Last review: nothing new to propose.')
    expect(dreamStatusWords(job({ status: 'capped', counts: { proposed: 1 } })))
      .toContain('token budget: 1 proposal kept')
    expect(dreamStatusWords(job({ status: 'failed', error_kind: 'dream_provider_error' })))
      .toBe('Last review failed because the model could not be reached.')
    expect(dreamStatusWords(job({ status: 'queued', trigger: 'nightly' }))).toBe('Review queued (nightly)…')
  })
})
