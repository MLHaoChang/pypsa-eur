// The one reliability goal editor (W3b-R): controlled, the same values in
// Guided and Expert, only the typed field reported.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { goal } from '../../test/fixtures/reliabilityGoal'
import { ENS_HINT, ReliabilityGoalEditor } from './ReliabilityGoalEditor'

afterEach(() => cleanup())

// Guided's labels are glossary terms, which read the glossary query.
const render = (ui: ReactElement) => rtlRender(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>)

const G = goal({ target_lole_h: [3, 'stored'], ens_cap_permyriad: [0.5, 'template'] })
const input = (id: string) => screen.getByTestId(id) as HTMLInputElement

describe('ReliabilityGoalEditor', () => {
  it.each([['guided', 'hub-goal'], ['expert', 'solver-goal']] as const)(
    '%s shows the same goal values', (mode, g) => {
      render(<ReliabilityGoalEditor mode={mode} goal={G} edits={{}} onChange={vi.fn()} />)
      expect(input(`${g}-lole`).value).toBe('3')
      expect(input(`${g}-ens`).value).toBe('0.5')
      expect(input(`${g}-draws`).value).toBe('')
      expect(input(`${g}-draws`).placeholder).toBe('500 (default)')
      // the template's value says where it came from; a stored one does not
      expect(screen.getByTestId(`${g}-ens-template`).textContent).toBe('From the project template')
      expect(screen.queryByTestId(`${g}-lole-template`)).toBeNull()
    })

  it('reports only the field typed into', () => {
    const onChange = vi.fn()
    render(<ReliabilityGoalEditor mode="expert" goal={G} edits={{}} onChange={onChange} />)
    fireEvent.change(input('solver-goal-draws'), { target: { value: '800' } })
    expect(onChange).toHaveBeenCalledWith({ mc_draws: '800' })
  })

  it('shows an edit over the stored value, and names an invalid one', () => {
    render(<ReliabilityGoalEditor mode="expert" goal={G} edits={{ target_lole_h: '-1' }}
      onChange={vi.fn()} />)
    expect(input('solver-goal-lole').value).toBe('-1')
    expect(screen.getByTestId('solver-goal-error').textContent).toMatch(/Allowed shortfall must be 0 or more/)
  })

  it('Expert keeps the ENS target\'s unit and hint', () => {
    render(<ReliabilityGoalEditor mode="expert" goal={G} edits={{}} onChange={vi.fn()} />)
    const label = screen.getByText('ENS target (‱ of demand)')
    expect(label.getAttribute('title')).toBe(ENS_HINT)
    expect(ENS_HINT).toMatch(/0 = off/)
    expect(ENS_HINT).toMatch(/Requires a VOLL > 0/)
    expect(label.closest('label')!.querySelector('input')).toBe(input('solver-goal-ens'))
  })

  it('Guided and Expert show the same thing on the same empty goal (condition f)', () => {
    const empty = goal()
    render(<>
      <ReliabilityGoalEditor mode="guided" goal={empty} edits={{}} onChange={vi.fn()} />
      <ReliabilityGoalEditor mode="expert" goal={empty} edits={{}} onChange={vi.fn()} />
    </>)
    for (const f of ['lole', 'ens', 'draws']) {
      const g = input(`hub-goal-${f}`)
      const e = input(`solver-goal-${f}`)
      expect([g.value, g.placeholder]).toEqual([e.value, e.placeholder])
    }
    expect(input('hub-goal-lole').placeholder).toBe('not set')
    expect(input('hub-goal-ens').placeholder).toBe('off')
    expect(input('hub-goal-draws').placeholder).toBe('500 (default)')
  })

  it('shows only the asked fields', () => {
    render(<ReliabilityGoalEditor mode="guided" goal={goal()} edits={{}} onChange={vi.fn()}
      fields={['target_lole_h']} />)
    expect(screen.queryByTestId('hub-goal-ens')).toBeNull()
    expect(screen.queryByTestId('hub-goal-draws')).toBeNull()
  })

  it('Expert has one help icon per label (the catalogue hover), never a second hint icon', async () => {
    const { guidesApi } = await import('../GuidedTour')
    vi.spyOn(guidesApi, 'getGuide').mockResolvedValue({
      version: 1, tours: {},
      fields: { target_lole_h: 'a', ens_cap_permyriad: 'b', mc_draws: 'c' },
    } as never)
    render(<ReliabilityGoalEditor mode="expert" goal={G} edits={{}} onChange={vi.fn()} />)
    for (const f of ['lole', 'ens', 'draws']) {
      const label = input(`solver-goal-${f}`).closest('label')!
      await waitFor(() => expect(label.querySelectorAll('[tabindex="0"]').length).toBe(1))
      expect(label.querySelectorAll('.cursor-help').length).toBe(1)
      expect(label.textContent).not.toMatch(/ⓘ/)
    }
  })

  it('Expert shows the catalogue\'s hover help on the goal fields, as the EH panel did', async () => {
    const { guidesApi } = await import('../GuidedTour')
    vi.spyOn(guidesApi, 'getGuide').mockResolvedValue({
      version: 1, tours: {},
      fields: { target_lole_h: 'LOLE target — allowed loss-of-load hours per year.' },
    } as never)
    const user = userEvent.setup()
    render(<ReliabilityGoalEditor mode="expert" goal={G} edits={{}} onChange={vi.fn()} />)
    const label = input('solver-goal-lole').closest('label')!
    const trigger = await waitFor(() => {
      const t = label.querySelector('[tabindex="0"]')
      if (!t) throw new Error('help icon not rendered yet')
      return t
    })
    await user.hover(trigger)
    expect(await screen.findByText(/allowed loss-of-load hours per year/)).toBeTruthy()
  })
})
