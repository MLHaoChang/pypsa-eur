// U3 (UX §4.3, §11 "question-first rail"): in Guided the decision study is
// a rail generated from the question, Question → Site → Key choices → Run →
// Verdict → Improve → Report, each step opening its view and carrying its
// state; Expert keeps the full section nav.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import QuestionRail, { RAIL, railState } from './QuestionRail'
import type { ChipState, HubSectionId } from '../../utils/decisionVocabulary'

afterEach(() => cleanup())

const ALL = (s: ChipState) => ({
  site: s, demand: s, options: s, tariff: s, finance: s, assumptions: s, run: s, findings: s, report: s,
}) as Record<HubSectionId, ChipState>

describe('the question rail', () => {
  it('has the seven steps in order, each opening its view', () => {
    expect(RAIL.map(r => r.step)).toEqual(['question', 'site', 'keyChoices', 'run', 'verdict', 'improve', 'report'])
    expect(RAIL.map(r => r.view)).toEqual(['hub', 'intake', 'ledger', 'run', 'verdict', 'robust', 'report'])
  })

  it('a step is done only when its sections are', () => {
    const st = { ...ALL('not_started'), site: 'done', demand: 'using_defaults', run: 'done' } as Record<HubSectionId, ChipState>
    expect(railState('question', st, false)).toBe('done')
    expect(railState('site', st, false)).toBe('done')
    expect(railState('keyChoices', st, false)).toBe('not_started')
    expect(railState('run', st, false)).toBe('done')
    expect(railState('improve', st, false)).toBe('not_started')
    expect(railState('improve', st, true)).toBe('done')
    expect(railState('site', { ...st, demand: 'not_started' }, false)).toBe('not_started')
  })

  it('marks the current step and opens a picked one', () => {
    const onOpen = vi.fn()
    render(<QuestionRail current="ledger" statuses={ALL('done')} robust={true} onOpen={onOpen} />)
    expect(screen.getByTestId('decision-rail-keyChoices').getAttribute('aria-current')).toBe('step')
    fireEvent.click(screen.getByTestId('decision-rail-verdict'))
    expect(onOpen).toHaveBeenCalledWith('verdict')
  })
})
