// U3: the decision panel hands its steps to the assistant through the
// hub-design buttons: Delegate SENDS one request naming the study (the user's
// click; confirmation cards still gate the writes), Ask only prefills. Every
// tool the sent text names exists in the backend's catalogue.
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import StudyAssist from './StudyAssist'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import { ASSIST_LABELS, assistText, type AssistStep } from '../../utils/decisionVocabulary'

const STEPS: AssistStep[] = ['ledger', 'run', 'verdict', 'report']

beforeEach(() => {
  useChatStore.setState({ requestQueue: [] } as never)
  useUIStore.setState({ uiMode: 'guided' })
})
afterEach(() => cleanup())

describe('StudyAssist', () => {
  it.each(STEPS)('%s: delegate sends one request naming the study, labelled for the transcript', step => {
    render(<StudyAssist step={step} project="Site battery" studyId="abc123" />)
    fireEvent.click(screen.getByTestId(`study-delegate-${step}`))
    const q = useChatStore.getState().requestQueue
    expect(q).toHaveLength(1)
    expect(q[0].text).toContain('abc123')
    expect(q[0].text).toContain('"Site battery"')
    expect(screen.getByTestId(`study-delegate-${step}`).textContent).toContain(ASSIST_LABELS[step].delegate)
  })

  it('ask prefills the composer and sends nothing', () => {
    render(<StudyAssist step="verdict" project="p" studyId="s" />)
    fireEvent.click(screen.getByTestId('study-ask-verdict'))
    expect(useChatStore.getState().requestQueue).toHaveLength(0)
  })

  it('every tool the requests name is in the backend catalogue', () => {
    const catalogue = readFileSync(join(__dirname, '../../../../backend/harness/catalogue.py'), 'utf8')
    const named = new Set(STEPS.flatMap(s => assistText(s, 'p', 's').match(/\b[a-z]+(?:_[a-z]+)+\b/g) ?? []))
    expect(named.size).toBeGreaterThan(3)
    for (const tool of named) expect({ tool, known: catalogue.includes(`"${tool}"`) }).toEqual({ tool, known: true })
  })
})
