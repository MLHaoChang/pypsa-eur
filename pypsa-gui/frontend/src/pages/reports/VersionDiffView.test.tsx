/**
 * `VersionDiffView` (WP14): two version selects (defaults: the previous
 * version against the latest) drive `GET …/versions/{a}/diff/{b}`; the
 * answer is a table with one row per section — a change tag with an icon
 * AND a word (unchanged / changed / added / removed), the source in a → b,
 * the pending instruction and the comments the b version carries.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { VersionDiff } from '../../api/reports'
import { VersionDiffView } from './VersionDiffView'

const api = vi.hoisted(() => ({ getVersionDiff: vi.fn() }))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, ...api }
})

const REPORT_ID = 'a1b2c3d4e5f60718'

const DIFF: VersionDiff = {
  a: 2,
  b: 3,
  sections: [
    { section_id: 'summary', heading: 'Executive summary', change: 'changed', source_a: 'llm', source_b: 'user_edit', pending_instruction: 'Shorter.', comments: ['Shorter.', 'Cite the table'] },
    { section_id: 'cost', heading: 'Cost at target', change: 'unchanged', source_a: 'code', source_b: 'code', pending_instruction: null, comments: [] },
    { section_id: 'appendix', heading: 'Appendix', change: 'added', source_a: null, source_b: 'user_edit', pending_instruction: null, comments: [] },
    { section_id: 'gates', heading: 'Gates', change: 'removed', source_a: 'code', source_b: null, pending_instruction: null, comments: [] },
  ],
}

function renderView(latestVersion = 3, over: Partial<React.ComponentProps<typeof VersionDiffView>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <VersionDiffView project="Demo" reportId={REPORT_ID} latestVersion={latestVersion} onClose={onClose} {...over} />
    </QueryClientProvider>,
  )
  return { onClose }
}

beforeEach(() => {
  api.getVersionDiff.mockReset().mockImplementation(async (_p: string, _id: string, a: number, b: number) => ({ ...DIFF, a, b }))
})

afterEach(() => {
  cleanup()
})

describe('VersionDiffView', () => {
  it('defaults to previous → latest, fetches the diff and renders one row per section with a tag, sources, instruction and comments', async () => {
    renderView(3)
    const from = screen.getByLabelText('Compare from') as HTMLSelectElement
    const to = screen.getByLabelText('Compare to') as HTMLSelectElement
    expect(from.value).toBe('2')
    expect(to.value).toBe('3')
    expect(Array.from(from.options).map(o => o.value)).toEqual(['3', '2', '1'])
    await waitFor(() => expect(api.getVersionDiff).toHaveBeenCalledWith('Demo', REPORT_ID, 2, 3))

    const table = await screen.findByTestId('version-diff-table')
    const rows = within(table).getAllByTestId(/^diff-row-/)
    expect(rows.map(r => r.dataset.change)).toEqual(['changed', 'unchanged', 'added', 'removed'])
    // a word and an icon, never colour alone
    for (const [row, word] of [[rows[0], 'changed'], [rows[1], 'unchanged'], [rows[2], 'added'], [rows[3], 'removed']] as const) {
      const tag = within(row).getByTestId('diff-change')
      expect(tag.textContent?.trim().toLowerCase()).toBe(word)
      expect(tag.querySelector('svg')).not.toBeNull()
    }
    expect(within(rows[0]).getByTestId('diff-source').textContent).toMatch(/model prose\s*→\s*edited by you/)
    expect(within(rows[1]).getByTestId('diff-source').textContent).toMatch(/from evidence\s*→\s*from evidence/)
    expect(within(rows[2]).getByTestId('diff-source').textContent).toMatch(/—\s*→\s*edited by you/)
    expect(within(rows[3]).getByTestId('diff-source').textContent).toMatch(/from evidence\s*→\s*—/)
    expect(within(rows[0]).getByTestId('diff-pending').textContent).toContain('Shorter.')
    expect(within(rows[1]).getByTestId('diff-pending').textContent).toBe('—')
    expect(within(rows[0]).getByTestId('diff-comments').textContent).toContain('Cite the table')
    expect(rows[0].textContent).toContain('Executive summary')
    expect(screen.getByTestId('version-diff-summary').textContent).toMatch(/1 changed · 1 added · 1 removed · 1 unchanged/)
  })

  it('refetches when either select changes and refuses a = b', async () => {
    const user = userEvent.setup()
    renderView(3)
    await screen.findByTestId('version-diff-table')
    await user.selectOptions(screen.getByLabelText('Compare from'), '1')
    await waitFor(() => expect(api.getVersionDiff).toHaveBeenLastCalledWith('Demo', REPORT_ID, 1, 3))
    await user.selectOptions(screen.getByLabelText('Compare to'), '2')
    await waitFor(() => expect(api.getVersionDiff).toHaveBeenLastCalledWith('Demo', REPORT_ID, 1, 2))
    const calls = api.getVersionDiff.mock.calls.length
    await user.selectOptions(screen.getByLabelText('Compare to'), '1')
    expect(screen.getByTestId('version-diff-same').textContent).toMatch(/pick two different versions/i)
    expect(api.getVersionDiff.mock.calls.length).toBe(calls)
  })

  it('shows a refusal as text and closes through the Close control', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    api.getVersionDiff.mockRejectedValue(new ReportsError(
      { error_kind: 'report_version_not_found', message: 'No such version.' }, 404,
    ))
    const user = userEvent.setup()
    const { onClose } = renderView(2)
    expect((await screen.findByTestId('version-diff-error')).textContent).toMatch(/no such version/i)
    await user.click(screen.getByTestId('version-diff-close'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
