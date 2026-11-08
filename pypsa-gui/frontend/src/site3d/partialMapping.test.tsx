// SC-07 S5b R3: the partial task card line (UX condition 2, U-e, U-f; GK B N3).
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MAPPING_REASON, PartialMappingLine, partialMapping } from './partialMapping'

const TASK = '0f0e0d0c-0b0a-4908-8706-050403020100'
const task = (next: Record<string, unknown> | null, changes: unknown = ['Set the IT load “Hall A” to 2 MW.']) => ({
  task_id: TASK, status: 'running', changes,
  ...(next ? { next_step: next } : {}),
})
const failed = (tool: string, error_kind?: string) => ({ id: 's2', tool, status: 'failed', ...(error_kind ? { error_kind } : {}) })
// What the task layer stores for a refused apply: "uncertain" with the tool's typed kind.
const refused = (error_kind: string) => ({ id: 's2', tool: 'site_scene_apply', status: 'uncertain', error_kind })

describe('partial mapping line (R3)', () => {
  it.each([
    ['proposal_expired', 'the prepared mapping expired'],
    ['proposal_cancelled', 'it was cancelled'],
    ['revision_conflict', 'the site changed after it was prepared'],
    ['unknown_target', 'a target needs your choice'],
  ])('a refused apply (%s) says what was done and why the mapping was not', (kind, words) => {
    render(<PartialMappingLine task={task(refused(kind))} taskId={TASK} onRequest={vi.fn()} />)
    expect(screen.getByRole('status').textContent)
      .toBe(`Done: Set the IT load “Hall A” to 2 MW. Not done: the 3D site mapping (${words}).`)
    const text = screen.getByTestId('task-partial-mapping').textContent ?? ''
    expect(text).not.toContain(kind)
    expect(text).not.toMatch(/[—;›]/)
  })

  it('a denied card (the apply still pending) claims no "Not done", unlike a cancelled one (U-e)', () => {
    expect(partialMapping(task({ id: 's2', tool: 'site_scene_apply', status: 'pending' }))).toBeNull()
    expect(partialMapping(task(failed('site_scene_apply', 'proposal_cancelled')))?.reason).toBe(MAPPING_REASON.proposal_cancelled)
  })

  it('a failed apply with no kind falls back to the generic reason', () => {
    expect(partialMapping(task(failed('site_scene_apply')))?.reason).toBe('it did not complete')
  })

  it('a prepare that asks for a choice (no kind) names the choice', () => {
    expect(partialMapping(task(failed('site_scene_prepare')))?.reason).toBe('a target needs your choice')
  })

  it.each([
    ['an unknown outcome', task(failed('site_scene_apply', 'scene_apply_outcome_unknown'))],
    ['an uncertain apply', task({ id: 's2', tool: 'site_scene_apply', status: 'uncertain' })],
    ['an uncertain apply with another kind', task(refused('some_other_kind'))],
    ['an unknown outcome stored uncertain', task(refused('scene_apply_outcome_unknown'))],
    ['a pending apply', task({ id: 's2', tool: 'site_scene_apply', status: 'pending' })],
    ['no model edit done', task(failed('site_scene_apply', 'proposal_expired'), [])],
    ['another tool failed', task(failed('update_component', 'invalid'))],
    ['no next step', task(null)],
  ])('%s never reads "Not done" (U-f)', (_label, t) => {
    const { container } = render(<PartialMappingLine task={t} taskId={TASK} onRequest={vi.fn()} />)
    expect(container.textContent).toBe('')
  })

  it('joins several edits with full stops, never a semicolon', () => {
    const p = partialMapping(task(failed('site_scene_apply', 'proposal_expired'), ['Set A to 1 MW;', 'Set B to 2 MW.']))
    expect(p?.done).toBe('Set A to 1 MW. Set B to 2 MW')
  })

  it('Prepare the mapping again is a button that cancels the task and asks for a fresh prepare, never a retry (N3)', () => {
    const onRequest = vi.fn()
    render(<PartialMappingLine task={task(failed('site_scene_apply', 'proposal_expired'))} taskId={TASK} onRequest={onRequest} />)
    const button = screen.getByTestId('task-prepare-mapping-again')
    expect(button.tagName).toBe('BUTTON')
    expect(button.textContent).toBe('Prepare the mapping again')
    fireEvent.click(button)
    const [text, label] = onRequest.mock.calls[0]
    expect(text).toContain(`cancel_task with task_id ${TASK}`)
    expect(text).toContain('site_scene_prepare')
    expect(text).toContain('Do not repeat the model edit')
    expect(text).not.toMatch(/resolve_task_step\(|resolve_task_step with/)
    expect(label).toBe('Prepare the mapping again')
  })

  it('is disabled while the chat cannot send', () => {
    render(<PartialMappingLine task={task(failed('site_scene_apply', 'proposal_expired'))} taskId={TASK} disabled onRequest={vi.fn()} />)
    expect((screen.getByTestId('task-prepare-mapping-again') as HTMLButtonElement).disabled).toBe(true)
  })
})
