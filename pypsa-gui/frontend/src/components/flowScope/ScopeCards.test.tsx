// W4a: section 8's scope component. Six cards, ≤ 3 sub-options each; a
// sub-option not yet available is shown and never selectable; a locked card
// cannot be switched off; switching a card off keeps its sub-options; "Use
// example preset" writes the preset; the Goal card hides the goal when
// Reliability is off.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowScopeRegistry, FlowScopeValue } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { AddToScope, ScopeCards, toggleCard, toggleOption } from './ScopeCards'
import { ScopeChips } from './ScopeChips'
import { NotInScopeGroup, SectionNotInScope } from './NotInScopeGroup'
import { useInScope } from './useFlowScope'

const getFlowScope = vi.fn()
const putFlowScope = vi.fn()
const getProjectFlow = vi.fn()
vi.mock('../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/flow')>()
  return {
    ...actual,
    getFlowScope: (...a: unknown[]) => getFlowScope(...a),
    putFlowScope: (...a: unknown[]) => putFlowScope(...a),
    getProjectFlow: (...a: unknown[]) => getProjectFlow(...a),
  }
})

const opt = (id: string, status: 'available' | 'required' | 'not_yet_available' = 'available') =>
  ({ id, label: id, status,
    reason: status === 'not_yet_available' ? "can't be run yet"
      : status === 'required' ? 'Always part of the run.' : null })

const PRESET: FlowScopeValue = {
  version: 1,
  groups: ['capacity', 'grid', 'reliability', 'commercial', 'handoff'],
  options: {
    capacity: ['sizing', 'dispatch'], grid: ['load_flow', 'grid_code'], control: [],
    reliability: ['goal_study', 'stress'], commercial: ['tariffs'], handoff: ['reports'],
  },
}

function registry(scope: FlowScopeValue = PRESET): FlowScopeRegistry {
  return {
    scope: { ...scope, source: 'preset', preset_id: 'eh_datacenter' },
    preset_id: 'eh_datacenter',
    preset_label: 'Data Center Energy Hub',
    preset: PRESET,
    cards: [
      { id: 'capacity', label: 'Capacity expansion', locked_reason: 'needs a dispatch source',
        sections: ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial',
          'scenarios'], chip: null,
        intended: ['sizing', 'dispatch'],
        options: [opt('sizing', 'required'), opt('dispatch', 'required'),
          opt('process_detail', 'not_yet_available')] },
      { id: 'grid', label: 'AC power flow & grid', locked_reason: null,
        sections: ['connection', 'assets', 'scenarios'], chip: 'Grid',
        intended: ['load_flow', 'grid_code'],
        options: [opt('load_flow'), opt('grid_code'), opt('n1_sclopf')] },
      { id: 'control', label: 'Control', locked_reason: null,
        sections: ['assets', 'scenarios'], chip: 'Control', intended: ['presets', 'island_checks'],
        options: [opt('presets'), opt('island_checks', 'not_yet_available'),
          opt('dynamics', 'not_yet_available')] },
      { id: 'reliability', label: 'Reliability', locked_reason: null,
        sections: ['assets', 'scenarios'], chip: 'Reliability',
        intended: ['goal_study', 'stress'],
        options: [opt('goal_study'), opt('stress'), opt('outage_screening')] },
      { id: 'commercial', label: 'Commercial & finance', locked_reason: null,
        sections: ['commercial'], chip: 'Commercial',
        intended: ['tariffs', 'investment_case'],
        options: [opt('tariffs'), opt('contracts'), opt('investment_case', 'not_yet_available')] },
      { id: 'handoff', label: 'Hand-off', locked_reason: 'always on', sections: [], chip: null,
        intended: ['reports'],
        options: [opt('reports'), opt('exports'), opt('specialist_packet')] },
    ],
  }
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  getFlowScope.mockResolvedValue(registry())
  putFlowScope.mockImplementation((_p: string, s: FlowScopeValue) =>
    Promise.resolve({ ...s, source: 'project', preset_id: 'eh_datacenter' }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount(node = <ScopeCards />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
  return userEvent.setup()
}

describe('ScopeCards', () => {
  it('draws six cards with the preset selected', async () => {
    mount()
    await screen.findByTestId('scope-cards')
    for (const c of ['capacity', 'grid', 'control', 'reliability', 'commercial', 'handoff']) {
      expect(screen.getByTestId(`scope-cards-card-${c}`)).toBeTruthy()
    }
    expect(screen.getByTestId('scope-cards-card-control').dataset.on).toBe('false')
    expect((screen.getByTestId('scope-cards-option-grid-grid_code') as HTMLInputElement).checked).toBe(true)
    // At the preset there is nothing to reset.
    expect(screen.queryByTestId('scope-cards-preset')).toBeNull()
  })

  it('shows a sub-option not yet available, disabled, and says the example intends it', async () => {
    mount()
    const box = await screen.findByTestId('scope-cards-option-commercial-investment_case') as HTMLInputElement
    expect(box.disabled).toBe(true)
    expect(box.checked).toBe(false)
    expect(screen.getByTestId('scope-cards-not-yet-commercial-investment_case').textContent)
      .toMatch(/Not yet available · in this example once available/)
  })

  it('locks Capacity and Hand-off on, with the reasons as visible text', async () => {
    mount()
    expect((await screen.findByTestId('scope-cards-toggle-capacity') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByTestId('scope-cards-why-capacity').textContent).toBe('needs a dispatch source')
    expect(screen.getByTestId('scope-cards-why-capacity-dispatch').textContent).toBe('Always part of the run.')
    expect(screen.getByTestId('scope-cards-why-control-dynamics').textContent).toBe("can't be run yet")
    expect((screen.getByTestId('scope-cards-toggle-handoff') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByTestId('scope-cards-option-capacity-sizing') as HTMLInputElement).disabled).toBe(true)
  })

  it('switching a card off writes the scope without it and keeps its sub-options', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('scope-cards-toggle-grid'))
    await waitFor(() => expect(putFlowScope).toHaveBeenCalled())
    const sent = putFlowScope.mock.calls[0][1] as FlowScopeValue
    expect(sent.groups).not.toContain('grid')
    expect(sent.options.grid).toEqual(['load_flow', 'grid_code'])
  })

  it('switching a card on picks the example\'s available sub-options', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('scope-cards-toggle-control'))
    await waitFor(() => expect(putFlowScope).toHaveBeenCalled())
    const sent = putFlowScope.mock.calls[0][1] as FlowScopeValue
    expect(sent.groups).toContain('control')
    expect(sent.options.control).toEqual(['presets'])
  })

  it('"Reset to <example>\'s cards" writes the preset', async () => {
    getFlowScope.mockResolvedValue(registry({ ...PRESET, groups: ['capacity', 'handoff'] }))
    const user = mount()
    const reset = await screen.findByTestId('scope-cards-preset')
    expect(reset.textContent).toBe("Reset to Data Center Energy Hub's setup")
    await user.click(reset)
    await waitFor(() => expect(putFlowScope).toHaveBeenCalledWith('Demo', PRESET))
  })

  it('says a refused write', async () => {
    putFlowScope.mockRejectedValue({ response: { data: { detail: {
      error_kind: 'flow_scope_unavailable', message: 'Control › dynamics is not yet available' } } } })
    const user = mount()
    await user.click(await screen.findByTestId('scope-cards-option-grid-n1_sclopf'))
    expect((await screen.findByTestId('scope-cards-save-error')).textContent)
      .toMatch(/not yet available/)
  })
})

describe('ScopeCards states', () => {
  it('a switched-off card shows its kept sub-options checked and dimmed', async () => {
    getFlowScope.mockResolvedValue(registry({ ...PRESET,
      groups: PRESET.groups.filter(g => g !== 'grid') }))
    mount()
    const box = await screen.findByTestId('scope-cards-option-grid-grid_code') as HTMLInputElement
    expect(box.checked).toBe(true)
    expect(box.disabled).toBe(true)
  })

  it('a settings-driven sub-option is read-only and says where it is set', async () => {
    const reg = registry()
    reg.cards[1].options[2] = { id: 'n1_sclopf', label: 'N-1 security (SCLOPF)',
      status: 'from_settings', reason: 'Set in Solver settings for now.' }
    getFlowScope.mockResolvedValue(reg)
    mount()
    const box = await screen.findByTestId('scope-cards-option-grid-n1_sclopf') as HTMLInputElement
    expect(box.disabled).toBe(true)
    expect(screen.getByTestId('scope-cards-why-grid-n1_sclopf').textContent)
      .toBe('Set in Solver settings for now.')
  })

  it('collapsed (Guided) shows one summary line; Change opens the cards in place', async () => {
    const user = mount(<ScopeCards collapsed />)
    const line = await screen.findByTestId('scope-cards-summary')
    expect(line.textContent).toBe('The Run computes: Capacity expansion, AC power flow & grid, '
      + 'Reliability, Commercial & finance, Hand-off · Change')
    expect(screen.queryByTestId('scope-cards-card-grid')).toBeNull()
    await user.click(screen.getByTestId('scope-cards-change'))
    expect(screen.getByTestId('scope-cards-card-grid')).toBeTruthy()
    await user.click(screen.getByTestId('scope-cards-done'))
    expect(screen.getByTestId('scope-cards-summary')).toBeTruthy()
  })
})

describe('the toggles', () => {
  it('the last sub-option off switches the card off', () => {
    const next = toggleOption(PRESET, 'commercial', 'tariffs', false)
    expect(next.groups).not.toContain('commercial')
    expect(next.options.commercial).toEqual([])
  })

  it('a card on with nothing kept takes its first available sub-option', () => {
    const card = registry().cards[1]
    const off = { ...PRESET, groups: PRESET.groups.filter(g => g !== 'grid'),
      options: { ...PRESET.options, grid: [] } }
    expect(toggleCard(off, { ...card, intended: [] }, true).options.grid).toEqual(['load_flow'])
  })
})

describe('useInScope', () => {
  function Probe() {
    return <span data-testid="probe">{useInScope('reliability', 'goal_study') ? 'on' : 'off'}</span>
  }

  it('is on while the scope is unread, then follows it', async () => {
    let resolve: (r: FlowScopeRegistry) => void = () => {}
    getFlowScope.mockReturnValue(new Promise(r => { resolve = r }))
    mount(<Probe />)
    expect(screen.getByTestId('probe').textContent).toBe('on')
    resolve(registry({ ...PRESET, groups: PRESET.groups.filter(g => g !== 'reliability') }))
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('off'))
  })
})

describe('AddToScope', () => {
  it('adds the card and its sub-option in place, keeping the other sub-options', async () => {
    const off = { ...PRESET, groups: PRESET.groups.filter(g => g !== 'reliability'),
      options: { ...PRESET.options, reliability: ['stress'] } }
    getFlowScope.mockResolvedValue(registry(off))
    const user = mount(
      <AddToScope card="reliability" option="goal_study" testId="add" />)
    const add = await screen.findByTestId('add-add')
    expect(add.textContent).toBe('Add goal_study')
    await user.click(add)
    await waitFor(() => expect(putFlowScope).toHaveBeenCalled())
    const sent = putFlowScope.mock.calls[0][1] as FlowScopeValue
    expect(sent.groups).toContain('reliability')
    expect(sent.options.reliability).toEqual(['stress', 'goal_study'])
  })

  it('offers nothing for a sub-option not yet available', async () => {
    mount(<AddToScope card="control" option="dynamics" testId="add" />)
    await waitFor(() => expect(getFlowScope).toHaveBeenCalled())
    expect(screen.queryByTestId('add')).toBeNull()
  })
})

describe('ScopeChips', () => {
  it('names the cards in scope whose blocks the section holds, from the registry', async () => {
    mount(<><ScopeChips section="assets" /><ScopeChips section="commercial" /><ScopeChips section="loads" /></>)
    const assets = await screen.findByTestId('scope-chips-assets')
    expect(assets.textContent).toBe('GridReliability')
    expect([...assets.children].map(c => c.getAttribute('title'))).toEqual(['AC power flow & grid', 'Reliability'])
    expect(screen.getByTestId('scope-chips-commercial').textContent).toBe('Commercial')
    expect(screen.queryByTestId('scope-chips-loads')).toBeNull()      // Capacity gets no chip
  })

  it('renders nothing while the scope is unread', () => {
    getFlowScope.mockReturnValue(new Promise(() => {}))
    mount(<ScopeChips section="assets" />)
    expect(screen.queryByTestId('scope-chips-assets')).toBeNull()
  })
})

const OUT_ITEM = { code: 'eh_study_refused', severity: 'error', message: 'The study would not start',
  source: 'eh_study', blocking: false, acknowledged: false, card: 'reliability', in_scope: false,
  target: { section: 'scenarios', panel: null, component: null, entity: null, field: null } }

describe('NotInScopeGroup', () => {
  it('lists the out-of-scope items collapsed, with the card that brings each back', async () => {
    getProjectFlow.mockResolvedValue({ sections: [{ section: 'scenarios', items: [
      { code: 'eh_study_refused', severity: 'error', message: 'The study would not start', source: 'eh_study',
        blocking: false, acknowledged: false, card: 'reliability', in_scope: false,
        target: { section: 'scenarios', panel: null, component: null, entity: null, field: null } },
      { code: 'snapshots_empty', severity: 'error', message: 'No snapshots', source: 'validation',
        blocking: true, acknowledged: false, card: null, in_scope: true,
        target: { section: 'scenarios', panel: null, component: null, entity: null, field: null } },
    ] }] })
    getFlowScope.mockResolvedValue(registry({ ...PRESET, groups: PRESET.groups.filter(g => g !== 'reliability') }))
    const user = mount(<NotInScopeGroup />)
    const toggle = await screen.findByTestId('issues-not-in-scope-toggle')
    expect(toggle.textContent).toBe('Not in scope (1)')
    expect(screen.queryByTestId('issues-not-in-scope-row')).toBeNull()
    await user.click(toggle)
    const row = screen.getByTestId('issues-not-in-scope-row')
    expect(row.textContent).toContain('The study would not start')
    const add = await screen.findByTestId('issues-not-in-scope-add-reliability')
    expect(add.textContent).toBe('Add Reliability')
    await user.click(add)
    await waitFor(() => expect(putFlowScope).toHaveBeenCalled())
    const sent = putFlowScope.mock.calls[0][1] as FlowScopeValue
    expect(sent.groups).toContain('reliability')
    expect(sent.options.reliability).toEqual(['goal_study', 'stress'])   // its kept sub-options
  })

  it('says why where the card cannot be added (no button)', async () => {
    getProjectFlow.mockResolvedValue({ sections: [{ section: 'scenarios', items: [OUT_ITEM] }] })
    const reg = registry({ ...PRESET, groups: PRESET.groups.filter(g => g !== 'reliability') })
    const nothing = "Nothing to make adequate: this site only generates and has no load."
    reg.cards[3].options = reg.cards[3].options.map(o => ({ ...o, status: 'not_yet_available', reason: nothing }))
    getFlowScope.mockResolvedValue(reg)
    const user = mount(<NotInScopeGroup />)
    await user.click(await screen.findByTestId('issues-not-in-scope-toggle'))
    expect(screen.queryByTestId('issues-not-in-scope-add-reliability')).toBeNull()
    expect((await screen.findByTestId('issues-not-in-scope-add-reliability-why')).textContent)
      .toBe(`Not offered for this site: ${nothing}`)
  })

  it('the Guided line promises nothing back on a site that cannot add the card', async () => {
    getProjectFlow.mockResolvedValue({ sections: [{ section: 'scenarios', items: [OUT_ITEM] }] })
    const reg = registry({ ...PRESET, groups: PRESET.groups.filter(g => g !== 'reliability') })
    const nothing = "Nothing to make adequate: this site only generates and has no load."
    reg.cards[3].options = reg.cards[3].options.map(o => ({ ...o, status: 'not_yet_available', reason: nothing }))
    getFlowScope.mockResolvedValue(reg)
    mount(<SectionNotInScope section="scenarios" />)
    const line = await screen.findByTestId('section-not-in-scope')
    expect(line.textContent).toContain('1 item is not in scope.')
    expect(line.textContent).not.toContain('come back')
    expect(line.textContent).toContain(`Not offered for this site: ${nothing}`)
  })
})

describe('SectionNotInScope (Guided)', () => {
  it('one muted line, not a count, with the card that brings them back', async () => {
    getProjectFlow.mockResolvedValue({ sections: [{ section: 'scenarios', items: [OUT_ITEM, OUT_ITEM] }] })
    getFlowScope.mockResolvedValue(registry({ ...PRESET, groups: PRESET.groups.filter(g => g !== 'reliability') }))
    mount(<SectionNotInScope section="scenarios" />)
    const line = await screen.findByTestId('section-not-in-scope')
    expect(line.textContent).toContain('2 items are not in scope: they come back if you add Reliability.')
    expect((await screen.findByTestId('section-not-in-scope-add-reliability')).textContent).toBe('Add Reliability')
  })
})
