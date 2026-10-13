/**
 * `SectionCard` (WP14 additions): a section merged from an edited copy
 * (`source: user_edit`) is tagged "edited by you", a `pending_instruction`
 * (a Word comment the round trip turned into an instruction) is a chip with
 * "Regenerate with this" — which regenerates WITHOUT an instruction of its
 * own so the backend uses the pending one — and the section's `comments`
 * are listed. A plain model section shows none of the three.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReportDocument, Section } from '../../api/reports'
import { SectionCard } from './SectionCard'

const DOC: ReportDocument = {
  schema_version: 1,
  report_id: 'a1b2c3d4e5f60718',
  version: 2,
  title: 'Study report — Demo',
  language: 'en',
  created_at: '2026-09-29T10:00:00+00:00',
  evidence_hash: 'deadbeef',
  profile_id: null,
  model: null,
  mode: 'generated',
  template_file_id: null,
  sections: [],
  tables: {},
  figures: {},
}

function section(over: Partial<Section> = {}): Section {
  return {
    section_id: 'summary',
    heading: 'Executive summary',
    source: 'llm',
    status: 'ok',
    blocks: [{ type: 'paragraph', md: 'Prose.' }],
    note: null,
    audit: { unverified: [], verified: [] },
    ...over,
  }
}

beforeEach(() => { vi.restoreAllMocks() })
afterEach(() => { cleanup() })

describe('SectionCard — round-trip marks (WP14)', () => {
  it('tags a user_edit section "edited by you" and lists its comments', () => {
    render(<SectionCard section={section({ source: 'user_edit', comments: ['Shorter.', 'Cite the table'] })} doc={DOC} project="Demo" />)
    const card = screen.getByTestId('report-section')
    expect(within(card).getByTestId('section-edited').textContent).toMatch(/edited by you/i)
    const comments = within(card).getByTestId('section-comments')
    expect(within(comments).getAllByRole('listitem').map(li => li.textContent)).toEqual(['Shorter.', 'Cite the table'])
    expect(within(card).queryByTestId('section-pending')).toBeNull()
  })

  it('shows a pending instruction as a chip whose button regenerates with no instruction of its own', async () => {
    const onRegenerate = vi.fn()
    const user = userEvent.setup()
    render(
      <SectionCard
        section={section({ source: 'user_edit', pending_instruction: 'Lead with the cost.' })}
        doc={DOC}
        project="Demo"
        onRegenerate={onRegenerate}
      />,
    )
    const chip = screen.getByTestId('section-pending')
    expect(chip.textContent).toContain('Lead with the cost.')
    expect(chip.textContent).toMatch(/pending instruction/i)
    await user.click(within(chip).getByRole('button', { name: /regenerate with this/i }))
    expect(onRegenerate).toHaveBeenCalledTimes(1)
    expect(onRegenerate).toHaveBeenCalledWith('summary', '')
    // the ad-hoc instruction form was not opened by that click
    expect(screen.queryByTestId('regenerate-form')).toBeNull()
  })

  it('has no chip button without onRegenerate and disables it while a job runs', () => {
    render(<SectionCard section={section({ pending_instruction: 'x' })} doc={DOC} project="Demo" />)
    expect(within(screen.getByTestId('section-pending')).queryByRole('button')).toBeNull()
    cleanup()
    render(
      <SectionCard section={section({ pending_instruction: 'x' })} doc={DOC} project="Demo" onRegenerate={vi.fn()} regenerateDisabled />,
    )
    const button = within(screen.getByTestId('section-pending')).getByRole('button', { name: /regenerate with this/i })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows none of the marks on a plain model section', () => {
    render(<SectionCard section={section()} doc={DOC} project="Demo" />)
    const card = screen.getByTestId('report-section')
    expect(within(card).queryByTestId('section-edited')).toBeNull()
    expect(within(card).queryByTestId('section-pending')).toBeNull()
    expect(within(card).queryByTestId('section-comments')).toBeNull()
    expect(card.textContent).toContain('model prose')
  })
})
