// GC-09: the Grid compliance section against the backend's contract:
// status rendering, the blocked-by message, keyboard access, refusals shown
// as the backend words them, and the link back to the campus inputs.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ComplianceState, Deliverable } from '../api/gridCompliance'
import GridComplianceSection, { blockedMessage } from './GridComplianceSection'

const store = vi.hoisted(() => ({ setSlidePanel: vi.fn() }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: typeof store) => unknown) => sel(store),
}))
const nav = vi.hoisted(() => ({ withReturnFrom: vi.fn((fn: () => void) => fn()) }))
vi.mock('../layout/flowNavigation', () => nav)

const api = vi.hoisted(() => ({
  state: vi.fn(), setStatus: vi.fn(), setData: vi.fn(), freeze: vi.fn(),
  addComment: vi.fn(), updateComment: vi.fn(), pack: vi.fn(),
}))
vi.mock('../api/gridCompliance', async () => {
  const real = await vi.importActual<typeof import('../api/gridCompliance')>('../api/gridCompliance')
  return { ...real, gridComplianceApi: api }
})

const STATUSES = ['not_started', 'data', 'exported', 'results_in', 'judged', 'submitted',
  'tso_comments', 'accepted'] as const

function row(code: string, over: Partial<Deliverable> = {}): Deliverable {
  return {
    code, title: `Study ${code}`, phase: 1, tool: 'PSS/E', depends_on: [], blocked_by: [],
    status: 'not_started', status_basis: 'declared', status_at: null,
    next_step: 'Collect the data list',
    data: [{ item_id: 'item-1', label: 'Single-line diagram', supplier: '', due: null, status: 'requested' }],
    freeze: null, freeze_state: 'none', stale: false,
    feeds: [{ package: 'GC-04', label: 'PSS/E study results', available: false }],
    comments: [], packs: [], ...over,
  }
}

function state(rows: Deliverable[]): ComplianceState {
  return {
    statuses: [...STATUSES],
    phases: { 1: 'Preliminary design and model', 2: 'Compliance' },
    has_campus_input: true,
    deliverables: rows,
  }
}

const refuse = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><GridComplianceSection name="P" /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(cleanup)

describe('GridComplianceSection', () => {
  it('renders each deliverable by phase with its status chip, next step and the accepted count', async () => {
    api.state.mockResolvedValue(state([
      row('01', { status: 'accepted', next_step: 'Done' }),
      row('06', { phase: 2, status: 'tso_comments', next_step: 'Answer the TSO comments' }),
    ]))
    renderSection()
    const r1 = await screen.findByTestId('gc-row-01')
    expect(within(r1).getByText('Accepted')).toBeTruthy()
    expect(within(r1).getByText(/Next: Done/)).toBeTruthy()
    const r6 = screen.getByTestId('gc-row-06')
    expect(within(r6).getByText('TSO comments')).toBeTruthy()
    expect(screen.getByRole('group', { name: 'Phase 2: Compliance' })).toBeTruthy()
    expect(screen.getByText('1 of 2 accepted')).toBeTruthy()
  })

  it('says what blocks submission until the deliverable is submitted', async () => {
    api.state.mockResolvedValue(state([
      row('08', { blocked_by: ['06', '07'], status: 'judged' }),
      row('25', { blocked_by: ['24'], status: 'submitted' }),
    ]))
    renderSection()
    const r8 = await screen.findByTestId('gc-row-08')
    expect(within(r8).getByText(/Submission waits for TRGC 06, TRGC 07 to be judged/)).toBeTruthy()
    expect(within(screen.getByTestId('gc-row-25')).queryByText(/Submission waits/)).toBeNull()
    expect(blockedMessage(row('x', { blocked_by: [] }), [...STATUSES])).toBeNull()
  })

  it('opens a deliverable and changes its status from the keyboard', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state([row('04')]))
    api.setStatus.mockResolvedValue(row('04', { status: 'data' }))
    renderSection()
    await screen.findByTestId('gc-row-04')
    await user.tab()
    const toggle = screen.getByRole('button', { name: /Study 04/ })
    expect(document.activeElement).toBe(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await user.keyboard('{Enter}')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const select = screen.getByLabelText('Status') as HTMLSelectElement
    await user.tab()
    expect(document.activeElement).toBe(select)
    await user.selectOptions(select, 'data')
    await waitFor(() => expect(api.setStatus).toHaveBeenCalledWith('P', '04', 'data'))
    await user.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
  })

  it('shows the backend refusal of a guarded move in its words', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state([row('08', { blocked_by: ['07'], status: 'judged' })]))
    api.setStatus.mockRejectedValue(refuse(409, {
      error_kind: 'gc_prerequisite_not_judged', blockers: ['07'],
      message: 'TRGC 08 cannot be submitted while TRGC 07 is not judged yet.',
    }))
    renderSection()
    await user.click(await screen.findByRole('button', { name: /Study 08/ }))
    await user.selectOptions(screen.getByLabelText('Status'), 'submitted')
    expect((await screen.findByRole('alert')).textContent)
      .toBe('TRGC 08 cannot be submitted while TRGC 07 is not judged yet.')
  })

  it('marks a stale freeze and links back to the campus inputs', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state([row('04', {
      freeze_state: 'stale', stale: true,
      freeze: { model_set_id: 'ms-abc', data_hash: 'h', frozen_at: '2026-11-17T00:00:00+00:00', source: 'interim' },
    })]))
    api.freeze.mockResolvedValue(row('04'))
    renderSection()
    const r = await screen.findByTestId('gc-row-04')
    expect(within(r).getByText('Out of date')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Study 04/ }))
    await user.click(screen.getByRole('button', { name: 'Review the campus inputs ›' }))
    expect(store.setSlidePanel).toHaveBeenCalledWith('campusElectrical')
    expect(nav.withReturnFrom).toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Freeze again' }))
    await waitFor(() => expect(api.freeze).toHaveBeenCalledWith('P', '04'))
  })

  it('logs a TSO comment and answers it', async () => {
    const user = userEvent.setup()
    const comment = {
      comment_id: 'C-001', date: '2026-11-01', clause: '§4', comment: 'Show recovery time.',
      response: '', revision: '', status: 'open' as const, pack: null,
      created_at: '', updated_at: '',
    }
    api.state.mockResolvedValue(state([row('06', { comments: [comment], packs: [{ version: 1, digest: 'd' }] })]))
    api.addComment.mockResolvedValue({ ...comment, comment_id: 'C-002' })
    api.updateComment.mockResolvedValue({ ...comment, status: 'responded' })
    renderSection()
    await user.click(await screen.findByRole('button', { name: /Study 06/ }))
    await user.type(screen.getByLabelText('TSO comment'), 'Add the PoC P–Q chart.')
    await user.click(screen.getByRole('button', { name: 'Log comment' }))
    await waitFor(() => expect(api.addComment).toHaveBeenCalledWith('P', '06',
      expect.objectContaining({ comment: 'Add the PoC P–Q chart.' })))
    await user.type(screen.getByLabelText('Response to C-001'), 'Added in section 4.')
    await user.selectOptions(screen.getByLabelText('Pack for C-001'), '1')
    await user.click(screen.getByRole('button', { name: 'Save response' }))
    await waitFor(() => expect(api.updateComment).toHaveBeenCalledWith('P', '06', 'C-001', {
      response: 'Added in section 4.', revision: '', status: 'responded', pack_version: 1,
    }))
  })

  it('shows a tracker it cannot read as an error, not an empty list', async () => {
    api.state.mockRejectedValue(refuse(409, {
      error_kind: 'gc_tracker_unreadable', message: 'The compliance tracker file cannot be read.',
    }))
    renderSection()
    expect((await screen.findByRole('alert')).textContent).toBe('The compliance tracker file cannot be read.')
    expect(screen.queryByTestId(/gc-row-/)).toBeNull()
  })
})
