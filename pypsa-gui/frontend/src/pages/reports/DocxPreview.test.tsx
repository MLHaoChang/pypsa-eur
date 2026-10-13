/**
 * `DocxPreview` (WP11) — fetches the exported `.docx` from its blob URL and
 * hands the bytes to `docx-preview`'s `renderAsync` (mocked here: the
 * library needs a real DOM layout to paint pages, jsdom has none).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { DocxPreview } from './DocxPreview'

const docx = vi.hoisted(() => ({ renderAsync: vi.fn() }))
vi.mock('docx-preview', () => ({ renderAsync: docx.renderAsync }))

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  docx.renderAsync.mockReset().mockResolvedValue(undefined)
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const URL = '/api/projects/Demo/uploads/f1/blob'
const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

describe('DocxPreview', () => {
  it('fetches the blob URL and renders it through renderAsync into the container', async () => {
    fetchMock.mockResolvedValueOnce(new Response(new Uint8Array([0x50, 0x4b, 3, 4]), {
      status: 200, headers: { 'Content-Type': DOCX_MIME },
    }))
    render(<DocxPreview url={URL} filename="report_v1.docx" />)
    expect(screen.getByTestId('docx-preview-loading')).toBeTruthy()
    await waitFor(() => expect(docx.renderAsync).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(URL)
    const [data, container, styleContainer, options] = docx.renderAsync.mock.calls[0]
    // `Response.blob()` answers with the fetch runtime's Blob (undici's under
    // vitest), not jsdom's global — assert the shape and the bytes.
    expect((data as Blob).constructor.name).toBe('Blob')
    expect((data as Blob).type).toBe(DOCX_MIME)
    expect(await (data as Blob).arrayBuffer().then(b => Array.from(new Uint8Array(b)))).toEqual([0x50, 0x4b, 3, 4])
    expect(container).toBeInstanceOf(HTMLElement)
    expect((container as HTMLElement).dataset.testid).toBe('docx-preview-container')
    expect(styleContainer).toBeUndefined()
    expect(options).toEqual(expect.objectContaining({ className: 'docx', inWrapper: true, ignoreWidth: false }))
    await waitFor(() => expect(screen.queryByTestId('docx-preview-loading')).toBeNull())
    expect(screen.queryByTestId('docx-preview-error')).toBeNull()
  })

  it('shows an error when the blob cannot be fetched', async () => {
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 404, statusText: 'Not Found' }))
    render(<DocxPreview url={URL} />)
    const err = await screen.findByTestId('docx-preview-error')
    expect(err.textContent).toMatch(/404/)
    expect(docx.renderAsync).not.toHaveBeenCalled()
  })

  it('shows an error when docx-preview refuses the file', async () => {
    fetchMock.mockResolvedValueOnce(new Response(new Uint8Array([1]), { status: 200 }))
    docx.renderAsync.mockRejectedValueOnce(new Error('not a zip'))
    render(<DocxPreview url={URL} />)
    const err = await screen.findByTestId('docx-preview-error')
    expect(err.textContent).toMatch(/not a zip/)
  })

  it('re-renders when the URL changes', async () => {
    fetchMock.mockImplementation(async () => new Response(new Uint8Array([1]), { status: 200 }))
    const view = render(<DocxPreview url={URL} />)
    await waitFor(() => expect(docx.renderAsync).toHaveBeenCalledTimes(1))
    view.rerender(<DocxPreview url="/api/projects/Demo/uploads/f2/blob" />)
    await waitFor(() => expect(docx.renderAsync).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenLastCalledWith('/api/projects/Demo/uploads/f2/blob')
  })
})
