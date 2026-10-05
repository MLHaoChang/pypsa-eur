// P24-BE gate N6: `blockerMessage` lives in utils so a hook
// (`useStartFmeaSweep`) no longer imports from a page. McPanel re-exports it,
// so every panel keeps reading the same function.
import { describe, expect, it } from 'vitest'
import { blockerMessage } from './blockerMessage'
import { blockerMessage as fromMcPanel } from '../pages/results/McPanel'
import hookSource from '../hooks/useStartFmeaSweep.ts?raw'

const httpError = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`),
    { response: { status, data: { detail } } })

describe('blockerMessage (utils)', () => {
  it('surfaces the server detail that names the blocking study', () => {
    expect(blockerMessage(httpError(409, 'a frontier study is running')))
      .toBe('a frontier study is running')
  })

  it('falls back to the transport message when there is no detail', () => {
    expect(blockerMessage(new Error('Network Error'))).toBe('Network Error')
  })

  it('is the very function McPanel exports (behaviour unchanged)', () => {
    expect(fromMcPanel).toBe(blockerMessage)
  })

  it('the FMEA sweep hook no longer depends on a page', () => {
    expect(hookSource).not.toMatch(/from ['"][^'"]*pages\//)
  })
})
