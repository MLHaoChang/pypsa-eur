/**
 * `RoundTripPanel` (WP14): "Upload edited copy" on the viewer — a drop zone
 * and a file input (`.docx`, 25 MB) that upload with `kind=report_roundtrip`
 * and post the round trip (`bind_as_template` from the "keep as template"
 * box, on by default), then show the merge result: the sections the user
 * edited, each Word comment as a pending instruction with "Regenerate with
 * this", the accepted tracked changes, the content that matched no section
 * ("not merged"), and the document-level comments. The viewer is told about
 * the new version through `onMerged`. Refusals are toasts with a sentence
 * per kind.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { RoundTripResponse } from '../../api/reports'
import { RoundTripPanel } from './RoundTripPanel'

const api = vi.hoisted(() => ({ uploadRoundTrip: vi.fn() }))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, ...api }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const REPORT_ID = 'a1b2c3d4e5f60718'

const RESPONSE: RoundTripResponse = {
  report_id: REPORT_ID,
  version: 2,
  template_file_id: 'f9',
  result: {
    sections: [
      { section_id: 'summary', heading: 'Executive summary', blocks: [{ type: 'paragraph', md: 'Edited.' }], changed: true, comments: ['Shorter.'] },
      { section_id: 'cost', heading: 'Cost at target', blocks: [], changed: false, comments: ['Cite the frontier table', 'Round to k€'] },
      { section_id: 'frontier', heading: 'Frontier', blocks: [], changed: false, comments: [] },
      { section_id: null, heading: 'Appendix Z', blocks: [], changed: true, comments: ['Where does this go?'] },
    ],
    unmatched: ['A paragraph nobody owns.', 'Another stray line.'],
    comments_global: ['Nice report overall.'],
    accepted_tracked_changes: 3,
  },
}

function renderPanel(over: Partial<React.ComponentProps<typeof RoundTripPanel>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onMerged = vi.fn()
  const onRegenerateWith = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <RoundTripPanel project="Demo" reportId={REPORT_ID} onMerged={onMerged} onRegenerateWith={onRegenerateWith} {...over} />
    </QueryClientProvider>,
  )
  return { onMerged, onRegenerateWith }
}

function docxFile(name = 'edited.docx'): File {
  return new File(['PK'], name, { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
}

beforeEach(() => {
  api.uploadRoundTrip.mockReset().mockResolvedValue(RESPONSE)
  toast.success.mockReset()
  toast.error.mockReset()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('RoundTripPanel', () => {
  it('uploads a picked .docx as a round trip (keep-as-template on by default) and renders the merge result', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const { onMerged, onRegenerateWith } = renderPanel()
    expect(screen.queryByTestId('roundtrip-result')).toBeNull()
    const box = screen.getByLabelText(/keep as template/i) as HTMLInputElement
    expect(box.checked).toBe(true)
    const file = docxFile()
    await user.upload(screen.getByTestId('roundtrip-file-input'), file)
    await waitFor(() => expect(api.uploadRoundTrip).toHaveBeenCalledWith('Demo', REPORT_ID, file, true))
    const result = await screen.findByTestId('roundtrip-result')
    expect(result.textContent).toMatch(/v2/)
    expect(result.textContent).toMatch(/3 tracked changes accepted/i)
    expect(result.textContent).toMatch(/kept as the report's template/i)

    // changed sections: heading + "edited by you"
    const changed = within(result).getByTestId('roundtrip-changed')
    const changedRows = within(changed).getAllByTestId('roundtrip-changed-row')
    expect(changedRows.map(r => r.textContent)).toEqual([
      expect.stringMatching(/Executive summary.*edited by you/s),
      expect.stringMatching(/Appendix Z.*edited by you/s),
    ])

    // comments: one row per comment, "Regenerate with this" only for a matched section
    const comments = within(result).getByTestId('roundtrip-comments')
    const rows = within(comments).getAllByTestId('roundtrip-comment')
    expect(rows.map(r => r.textContent)).toEqual([
      expect.stringContaining('Shorter.'),
      expect.stringContaining('Cite the frontier table'),
      expect.stringContaining('Round to k€'),
      expect.stringContaining('Where does this go?'),
    ])
    expect(rows[0].textContent).toContain('Executive summary')
    expect(rows[1].textContent).toContain('Cost at target')
    expect(within(rows[3]).queryByRole('button')).toBeNull()
    await user.click(within(rows[1]).getByRole('button', { name: /regenerate with this/i }))
    expect(onRegenerateWith).toHaveBeenCalledWith('cost')

    // unmatched content and the document-level comments
    const unmatched = within(result).getByTestId('roundtrip-unmatched')
    expect(unmatched.textContent).toMatch(/not merged — no matching section/i)
    expect(within(unmatched).getAllByRole('listitem').map(li => li.textContent))
      .toEqual(['A paragraph nobody owns.', 'Another stray line.'])
    expect(within(result).getByTestId('roundtrip-global').textContent).toContain('Nice report overall.')

    expect(onMerged).toHaveBeenCalledTimes(1)
    expect(onMerged).toHaveBeenCalledWith(RESPONSE)
    expect(toast.success).toHaveBeenCalled()
  })

  it('sends bind_as_template=false when "keep as template" is off, and says the template was not changed', async () => {
    api.uploadRoundTrip.mockResolvedValue({ ...RESPONSE, template_file_id: null })
    const user = userEvent.setup({ applyAccept: false })
    renderPanel()
    await user.click(screen.getByLabelText(/keep as template/i))
    const file = docxFile()
    await user.upload(screen.getByTestId('roundtrip-file-input'), file)
    await waitFor(() => expect(api.uploadRoundTrip).toHaveBeenCalledWith('Demo', REPORT_ID, file, false))
    const result = await screen.findByTestId('roundtrip-result')
    expect(result.textContent).not.toMatch(/kept as the report's template/i)
  })

  it('accepts a dropped file on the drop zone', async () => {
    renderPanel()
    const zone = screen.getByTestId('roundtrip-dropzone')
    const file = docxFile('dropped.docx')
    fireEvent.dragOver(zone, { dataTransfer: { types: ['Files'], files: [file] } })
    expect(zone.dataset.dragActive).toBe('true')
    fireEvent.drop(zone, { dataTransfer: { types: ['Files'], files: [file] } })
    await waitFor(() => expect(api.uploadRoundTrip).toHaveBeenCalledWith('Demo', REPORT_ID, file, true))
    expect(zone.dataset.dragActive).toBe('false')
    await screen.findByTestId('roundtrip-result')
  })

  it('refuses a non-.docx or an oversize file before uploading', async () => {
    const user = userEvent.setup({ applyAccept: false })
    renderPanel()
    const input = screen.getByTestId('roundtrip-file-input')
    await user.upload(input, new File(['x'], 'notes.pdf', { type: 'application/pdf' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(/\.docx/)
    expect(api.uploadRoundTrip).not.toHaveBeenCalled()

    const big = new File(['x'], 'big.docx')
    Object.defineProperty(big, 'size', { value: 25 * 1024 * 1024 + 1 })
    await user.upload(input, big)
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(2))
    expect(String(toast.error.mock.calls[1][0])).toMatch(/25 MB/)
    expect(api.uploadRoundTrip).not.toHaveBeenCalled()
  })

  it('does nothing while disabled', async () => {
    renderPanel({ disabled: true })
    expect((screen.getByTestId('roundtrip-file-input') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: /upload edited copy/i }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.drop(screen.getByTestId('roundtrip-dropzone'), { dataTransfer: { types: ['Files'], files: [docxFile()] } })
    await new Promise(r => setTimeout(r, 0))
    expect(api.uploadRoundTrip).not.toHaveBeenCalled()
  })

  it('toasts a sentence per refusal kind and shows no result', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    const cases: Array<[string, number, RegExp]> = [
      ['roundtrip_unreadable', 400, /could not be read as a word file/i],
      ['roundtrip_not_a_report', 400, /does not look like an export of this report/i],
      ['report_job_in_flight', 409, /already being written/i],
      ['project_locked', 409, /being edited by another user/i],
    ]
    const user = userEvent.setup({ applyAccept: false })
    const { onMerged } = renderPanel()
    for (const [kind, status, copy] of cases) {
      toast.error.mockReset()
      api.uploadRoundTrip.mockRejectedValueOnce(new ReportsError(
        { error_kind: kind, message: `backend ${kind}: 'Demo' is being edited by another user` }, status,
      ))
      await user.upload(screen.getByTestId('roundtrip-file-input'), docxFile())
      await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
      expect(String(toast.error.mock.calls[0][0])).toMatch(copy)
    }
    expect(screen.queryByTestId('roundtrip-result')).toBeNull()
    expect(onMerged).not.toHaveBeenCalled()
  })
})
