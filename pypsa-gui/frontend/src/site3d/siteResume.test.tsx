// SC-07 S5b R5: the Resume line (UX condition 4, U-a–U-d, U-f).
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ScenePreviewRow } from '../api/siteScene'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import SiteResume, { ACTION_WORDS, resumeRow } from './siteResume'

const open = (id: string, action = 'place_existing'): ScenePreviewRow => ({ id, status: 'open', action, base_revision: 3 })
const unknown = (id: string, action = 'bind_targets'): ScenePreviewRow => ({ id, status: 'outcome_unknown', action })
const applied = (id: string, action = 'detach'): ScenePreviewRow => ({ id, status: 'applied', action, base_revision: 3 })
const line = () => screen.getByTestId('site-resume-line').textContent ?? ''
const ID = /p-[0-9a-f]{4}|place_existing|bind_targets|detach|outcome_unknown/

beforeEach(() => {
  useChatStore.setState({ composerSeed: null })
  useUIStore.setState({ assistantDockOpen: false })
})

describe.each(['guided', 'expert'] as const)('Resume line (%s)', mode => {
  beforeEach(() => useUIStore.setState({ uiMode: mode }))

  it('is absent with nothing to act on, and an applied row never shows on load (U-b)', () => {
    const { container, rerender } = render(<SiteResume siteName="Campus" rows={[]} readAt={1} recheck={vi.fn()} />)
    expect(container.textContent).toBe('')
    rerender(<SiteResume siteName="Campus" rows={undefined} readAt={1} recheck={vi.fn()} />)
    expect(container.textContent).toBe('')
    rerender(<SiteResume siteName="Campus" rows={[applied('p-aaaa')]} readAt={1} recheck={vi.fn()} />)
    expect(container.textContent).toBe('')
  })

  it('an open preview has one action, the one link (U-a, U-c)', () => {
    render(<SiteResume siteName="Campus" rows={[open('p-aaaa')]} readAt={1} recheck={vi.fn()} />)
    expect(line()).toBe('Found: a placement you prepared, not applied yet.')
    expect(screen.getByRole('status').textContent).toBe(line())
    const review = screen.getByTestId('site-resume-review')
    expect(review.textContent).toBe('Review it in chat ›')
    expect(screen.queryByTestId('site-resume-prepare-again')).toBeNull()
    expect(screen.queryByTestId('site-resume-check')).toBeNull()
    fireEvent.click(review)
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(String(useChatStore.getState().composerSeed)).toContain('placement I prepared for the site “Campus” in Site 3D')
    expect(screen.getByTestId('site-resume').textContent).not.toMatch(ID)
  })

  it('several rows stay one line: unknown first, the rest as (N more) (U-d)', () => {
    render(<SiteResume siteName="Campus" rows={[open('p-aaaa'), unknown('p-bbbb'), open('p-cccc'), applied('p-dddd')]}
      readAt={1} recheck={vi.fn()} />)
    expect(line()).toBe('Found: a mapping to the model whose outcome is not known yet (2 more).')
    expect(screen.getAllByRole('status')).toHaveLength(1)
  })

  it('an unknown outcome checks the site, Checking… until the read answers, then applied (U-b, U-f)', async () => {
    let answer!: (v: { rows: ScenePreviewRow[]; readAt: number }) => void
    const recheck = vi.fn(() => new Promise<{ rows: ScenePreviewRow[]; readAt: number }>(r => { answer = r }))
    const { rerender } = render(<SiteResume siteName="Campus" rows={[unknown('p-bbbb')]} readAt={1} recheck={recheck} />)
    expect(line()).not.toContain('Not done')
    fireEvent.click(screen.getByTestId('site-resume-check'))
    expect(screen.getByTestId('site-resume-check').textContent).toBe('Checking…')
    expect(line()).not.toMatch(/applied\./)
    await act(async () => { answer({ rows: [applied('p-bbbb', 'bind_targets')], readAt: 2 }) })
    rerender(<SiteResume siteName="Campus" rows={[applied('p-bbbb', 'bind_targets')]} readAt={2} recheck={recheck} />)
    expect(line()).toBe('Found: your mapping to the model was applied.')
    expect(screen.queryByTestId('site-resume-prepare-again')).toBeNull()
    // The next fresh read clears it (#1183).
    rerender(<SiteResume siteName="Campus" rows={[applied('p-bbbb', 'bind_targets')]} readAt={3} recheck={recheck} />)
    expect(screen.queryByTestId('site-resume')).toBeNull()
  })

  it('a check that finds no receipt offers Prepare it again, a button (U-c)', async () => {
    const recheck = vi.fn(async () => ({ rows: [unknown('p-bbbb')], readAt: 2 }))
    const { rerender } = render(<SiteResume siteName="Campus" rows={[unknown('p-bbbb')]} readAt={1} recheck={recheck} />)
    await act(async () => { fireEvent.click(screen.getByTestId('site-resume-check')) })
    rerender(<SiteResume siteName="Campus" rows={[unknown('p-bbbb')]} readAt={2} recheck={recheck} />)
    expect(line()).toBe('Checked: your mapping to the model was not applied.')
    const again = screen.getByTestId('site-resume-prepare-again')
    expect(again.textContent).toBe('Prepare it again')
    fireEvent.click(again)
    expect(useChatStore.getState().composerSeed).toBe('Prepare the mapping to the model for the site “Campus” in Site 3D again.')
  })

  it('a failed read claims nothing and keeps the check', async () => {
    const recheck = vi.fn(async () => null)
    render(<SiteResume siteName="Campus" rows={[unknown('p-bbbb')]} readAt={1} recheck={recheck} />)
    await act(async () => { fireEvent.click(screen.getByTestId('site-resume-check')) })
    expect(line()).toBe('Found: a mapping to the model whose outcome is not known yet. The check could not read the site.')
    expect(screen.getByTestId('site-resume-check').textContent).toBe('Check the 3D site')
  })
})

describe('resume words', () => {
  it('names every chat action in words, with no key or id', () => {
    for (const words of Object.values(ACTION_WORDS)) expect(words).not.toMatch(/_|:/)
  })
  it('picks unknown before open, else the newest', () => {
    expect(resumeRow([open('a'), open('b')])).toEqual({ row: open('a'), more: 1 })
    expect(resumeRow([open('a'), unknown('b')])?.row.id).toBe('b')
    expect(resumeRow([applied('a')])).toBeNull()
  })
})
