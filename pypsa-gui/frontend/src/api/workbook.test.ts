// The Excel input workbook client (S3): the posted fields, the download names,
// and each refusal in plain words with its one action (UX 27 condition 3).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import client from './client'
import { workbookApi, workbookRefusal } from './workbook'

vi.mock('./client', async (orig) => {
  const actual = await orig<typeof import('./client')>()
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } }
})

const err = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

beforeEach(() => { vi.mocked(client.get).mockReset(); vi.mocked(client.post).mockReset() })

describe('workbookApi', () => {
  it('imports with the file, the preview hash and the name, without a toast', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: { name: 'Hub' } })
    const file = new File(['x'], 'Hub.xlsx')
    await workbookApi.import(file, 'abc', 'Hub 2')
    const [url, fd, cfg] = vi.mocked(client.post).mock.calls[0] as [string, FormData, { skipErrorToast: boolean }]
    expect(url).toBe('/workbook/import')
    expect(fd.get('file')).toBe(file)
    expect(fd.get('expected_sha256')).toBe('abc')
    expect(fd.get('name')).toBe('Hub 2')
    expect(cfg.skipErrorToast).toBe(true)
  })

  it('previews without a toast', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: {} })
    await workbookApi.preview(new File(['x'], 'a.xlsx'))
    expect(vi.mocked(client.post).mock.calls[0][0]).toBe('/workbook/preview')
    expect((vi.mocked(client.post).mock.calls[0][2] as { skipErrorToast: boolean }).skipErrorToast).toBe(true)
  })

  it('downloads the template under its own name and a project under the server name', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: new Blob(['x']), headers: { 'content-disposition': "attachment; filename*=UTF-8''PyPSA%20Studio%20input%20workbook.xlsx" } })
    expect((await workbookApi.template()).filename).toBe('PyPSA Studio input workbook.xlsx')
    vi.mocked(client.get).mockResolvedValue({ data: new Blob(['x']), headers: {} })
    expect((await workbookApi.project('My hub')).filename).toBe('My hub.xlsx')
    expect(vi.mocked(client.get).mock.calls[1][0]).toBe('/projects/My%20hub/workbook')
  })

  it('a refused download carries the backend sentence', async () => {
    const body = new Blob([JSON.stringify({ detail: { error_kind: 'saved_time_series_unreadable', message: 'The project’s saved time series cannot be read.' } })])
    vi.mocked(client.get).mockRejectedValue({ response: { status: 422, data: body } })
    await expect(workbookApi.project('X')).rejects.toThrow('The project’s saved time series cannot be read.')
  })
})

describe('workbookRefusal', () => {
  it('a changed file offers Preview again', () => {
    expect(workbookRefusal(err(409, { error_kind: 'import_file_changed', message: 'The file is not the one that was previewed: preview it again before importing.' })))
      .toEqual({ message: 'The file is not the one that was previewed: preview it again before importing.', action: 'preview-again' })
  })
  it('the upload cap names the limit and says nothing was read', () => {
    expect(workbookRefusal(err(413, 'Upload exceeds the 200 MB limit.')))
      .toEqual({ message: 'The file is larger than the 200 MB upload limit. Nothing was read.', action: 'choose-file' })
  })
  it.each([
    [413, 'import_expansion_too_large', 'The workbook expands past the read limit.'],
    [422, 'import_unreadable', 'The file is not a readable Excel workbook.'],
    [422, 'invalid_edit_value', 'Nodes › N1: the name is not allowed.'],
  ])('%i %s offers Choose another file', (status, kind, message) => {
    expect(workbookRefusal(err(status, { error_kind: kind, message }))).toEqual({ message, action: 'choose-file' })
  })
  it('a write failure is in the dialog’s own words, with Try again', () => {
    expect(workbookRefusal(err(500, { error_kind: 'import_write_failed', message: 'x (OSError)' })))
      .toEqual({ message: 'The project could not be written; nothing was created.', action: 'try-again' })
  })
  it('a taken name names it in curly quotes', () => {
    expect(workbookRefusal(err(409, "Project 'Hub' already exists")))
      .toEqual({ message: 'A project named “Hub” already exists, so nothing was created.', action: 'try-again', clash: 'Hub' })
  })
  it('an unanswered request and an unknown refusal never show a code', () => {
    expect(workbookRefusal(new Error('Network Error')).message).toBe('The app did not answer: nothing was created.')
    expect(workbookRefusal(err(502, '<html>')).message).toBe('The workbook could not be imported: nothing was created.')
  })
})
