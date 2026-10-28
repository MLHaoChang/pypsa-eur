import { describe, it, expect, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { client } from './client'
import { contextErrorMessage, contextFailureBacksOff, sitesApi } from './sites'

function axiosError(status?: number, detail?: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() }
  const response = status === undefined ? undefined : { status, statusText: '', headers: {}, config, data: detail === undefined ? {} : { detail } }
  return new AxiosError('Request failed', 'ERR_BAD_RESPONSE', config, null, response as never)
}

describe('contextFailureBacksOff', () => {
  it('backs off when the upstream refused or nothing answered', () => {
    expect(contextFailureBacksOff(axiosError(502))).toBe(true)
    expect(contextFailureBacksOff(axiosError(429))).toBe(true)
    expect(contextFailureBacksOff(axiosError(504))).toBe(true)
    expect(contextFailureBacksOff(axiosError(undefined))).toBe(true)  // network error / timeout
  })
  it('does not back off for this project\'s own state: a lock, a solve, a boundary too large', () => {
    expect(contextFailureBacksOff(axiosError(409))).toBe(false)
    expect(contextFailureBacksOff(axiosError(422))).toBe(false)
    expect(contextFailureBacksOff(axiosError(404))).toBe(false)
    expect(contextFailureBacksOff(new Error('boom'))).toBe(false)
  })
})

describe('contextErrorMessage', () => {
  it('prefers the backend detail, names a 409, and falls back to the error message', () => {
    expect(contextErrorMessage(axiosError(502, 'Overpass answered HTTP 429 (rate limited)'))).toBe('Overpass answered HTTP 429 (rate limited)')
    expect(contextErrorMessage(axiosError(409, { error_kind: 'project_locked' }))).toBe('the project is locked or a solve is running')
    expect(contextErrorMessage(new Error('boom'))).toBe('boom')
  })
})

describe('fetchContext', () => {
  it('is quiet: the 3D view words a failure itself, so the raw upstream message never reaches a toast', async () => {
    const post = vi.spyOn(client, 'post').mockResolvedValue({ data: {} } as never)
    await sitesApi.fetchContext('p', 'campus')
    expect(post.mock.calls[0][2]).toMatchObject({ skipErrorToast: true })
    post.mockRestore()
  })
})
