/**
 * The uploads API — WP11 adds a `kind` to the upload and list calls
 * (`?kind=report_template`); without one the URLs are unchanged.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CSRF_HEADER } from './csrf'
import { listUploads, uploadFile } from './uploads'

const fetchMock = vi.fn<typeof fetch>()

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  document.cookie = 'pypsa_gui_csrf=tok'
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.cookie = 'pypsa_gui_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
})

function lastCall(): { url: string; init: RequestInit | undefined } {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit | undefined]
  return { url, init }
}

describe('uploads api — kind (WP11)', () => {
  it('uploads without a kind to the plain route (unchanged default)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ file_id: 'f1', kind: 'user_upload' }))
    const file = new File(['x'], 'a.docx')
    await uploadFile('My Project', file)
    const { url, init } = lastCall()
    expect(url).toBe('/api/projects/My%20Project/uploads')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeInstanceOf(FormData)
    expect((init?.body as FormData).get('file')).toBe(file)
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
  })

  it('uploads a report template with ?kind=report_template', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ file_id: 'f1', kind: 'report_template' }))
    const out = await uploadFile('Demo', new File(['x'], 'corp.docx'), 'report_template')
    expect(out.kind).toBe('report_template')
    expect(lastCall().url).toBe('/api/projects/Demo/uploads?kind=report_template')
  })

  it('lists all uploads by default and filters with ?kind=', async () => {
    fetchMock.mockImplementation(async () => jsonResponse([]))
    await listUploads('Demo')
    expect(lastCall().url).toBe('/api/projects/Demo/uploads')
    await listUploads('Demo', 'report_template')
    expect(lastCall().url).toBe('/api/projects/Demo/uploads?kind=report_template')
  })
})
