// GC-09: the Grid compliance section against the backend's contract:
// status rendering, the blocked-by message, keyboard access, refusals shown
// as the backend words them, and the link back to the campus inputs.
// Rows and refusal bodies come from `gridCompliance.json`, which the backend
// test `test_grid_compliance_ui_fixture.py` builds from the real API (U1).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AxiosError, AxiosHeaders, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import client from '../api/client'
import type {
  ComplianceState, DataItem, Deliverable, Freeze, GcRefusal, TsoComment,
} from '../api/gridCompliance'
import fixture from '../test/fixtures/gridCompliance.json'
import GridComplianceSection, { blockedMessage, FREEZE_LABEL, STATUS_LABEL } from './GridComplianceSection'

const store = vi.hoisted(() => ({ setSlidePanel: vi.fn(), requestPanelSection: vi.fn() }))
vi.mock('../store/uiStore', () => ({
  useUIStore: Object.assign((sel: (s: typeof store) => unknown) => sel(store), {
    getState: () => ({ projectMismatch: null }),
  }),
}))
const nav = vi.hoisted(() => ({ withReturnFrom: vi.fn((fn: () => void) => fn()) }))
vi.mock('../layout/flowNavigation', () => nav)
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast, toast }))

const api = vi.hoisted(() => ({
  state: vi.fn(), setStatus: vi.fn(), setData: vi.fn(), freeze: vi.fn(),
  addComment: vi.fn(), updateComment: vi.fn(), pack: vi.fn(),
}))
vi.mock('../api/gridCompliance', async () => {
  const real = await vi.importActual<typeof import('../api/gridCompliance')>('../api/gridCompliance')
  return { ...real, gridComplianceApi: api }
})
const real = await vi.importActual<typeof import('../api/gridCompliance')>('../api/gridCompliance')

interface Fixture {
  state: ComplianceState
  pack: { content_disposition: string }
  errors: Record<string, { status: number; detail: GcRefusal }>
}
const FIX = fixture as unknown as Fixture

/** A deliverable as the server returned it, with test-specific changes. */
function row(code: string, over: Partial<Deliverable> = {}): Deliverable {
  const found = FIX.state.deliverables.find(d => d.code === code)
  if (!found) throw new Error(`no deliverable ${code} in the fixture`)
  return { ...structuredClone(found), ...over }
}

function state(rows: Deliverable[] = FIX.state.deliverables.map(d => row(d.code))): ComplianceState {
  return { ...structuredClone(FIX.state), deliverables: rows }
}

/** A refusal exactly as the server sent it. */
function refuse(kind: string) {
  const { status, detail } = FIX.errors[kind]
  return { response: { status, data: { detail: structuredClone(detail) } } }
}

const submittedFreeze = (): Freeze => row('14').submissions[0]

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><GridComplianceSection name="P" /></QueryClientProvider>)
}

async function openRow(user: ReturnType<typeof userEvent.setup>, code: string) {
  const r = await screen.findByTestId(`gc-row-${code}`)
  await user.click(within(r).getAllByRole('button')[0])
}

/** Answers every request of the real client with `respond` (the interceptors still run). */
function serve(respond: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>) {
  const before = client.defaults.adapter
  client.defaults.adapter = respond
  return () => { client.defaults.adapter = before }
}

function reply(config: InternalAxiosRequestConfig, status: number, data: unknown,
  headers: Record<string, string> = {}): Promise<AxiosResponse> {
  const response = { data, status, statusText: '', headers: new AxiosHeaders(headers), config }
  if (status < 400) return Promise.resolve(response)
  return Promise.reject(new AxiosError(`Request failed with status code ${status}`,
    AxiosError.ERR_BAD_REQUEST, config, null, response))
}

const refusalBlob = (kind: string) =>
  new Blob([JSON.stringify({ detail: FIX.errors[kind].detail })], { type: 'application/json' })

let restore: (() => void) | null = null
beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  restore?.()
  restore = null
})

describe('the fixture is the server shape (U1)', () => {
  // `Record<keyof T, true>`: TypeScript rejects a missing or an extra key, so
  // these lists are the TS types, compared below with what the server sent.
  const DELIVERABLE: Record<keyof Deliverable, true> = {
    code: true, title: true, phase: true, tool: true, depends_on: true, blocked_by: true, status: true,
    status_basis: true, status_at: true, next_step: true, data: true, freeze: true, freeze_state: true,
    stale: true, submissions: true, since_submission: true, feeds: true, comments: true, packs: true,
  }
  const COMMENT: Record<keyof TsoComment, true> = {
    comment_id: true, date: true, clause: true, comment: true, response: true, revision: true,
    status: true, pack: true, created_at: true, updated_at: true,
  }
  const FREEZE: Record<keyof Freeze, true> = { model_set_id: true, data_hash: true, frozen_at: true, source: true }
  const ITEM: Record<keyof DataItem, true> = { item_id: true, label: true, supplier: true, due: true, status: true }
  const STATE: Record<keyof ComplianceState, true> = {
    statuses: true, phases: true, has_campus_input: true, deliverables: true,
  }
  const keys = (o: object) => Object.keys(o).sort()

  it('has every key the types name, and no other', () => {
    expect(keys(FIX.state)).toEqual(keys(STATE))
    expect(FIX.state.statuses).toEqual(Object.keys(STATUS_LABEL))
    for (const d of FIX.state.deliverables) {
      expect(keys(d)).toEqual(keys(DELIVERABLE))
      expect(Object.keys(FREEZE_LABEL)).toContain(d.freeze_state)
      for (const it of d.data) expect(keys(it)).toEqual(keys(ITEM))
      for (const c of d.comments) expect(keys(c)).toEqual(keys(COMMENT))
      for (const f of [d.freeze, ...d.submissions]) if (f) expect(keys(f)).toEqual(keys(FREEZE))
      for (const f of d.feeds) expect(keys(f)).toEqual(['available', 'label', 'package', 'state'])
      for (const p of d.packs) expect(keys(p)).toEqual(['digest', 'version'])
    }
    for (const { detail } of Object.values(FIX.errors)) {
      expect(typeof detail.error_kind).toBe('string')
      expect(typeof detail.message).toBe('string')
    }
    // the representative state the tests below rely on
    expect(row('14').since_submission).toEqual({ inputs: 'changed', reason: null })
    expect(row('12').freeze_state).toBe('stale')
    expect(row('11').comments.length).toBeGreaterThan(0)
    expect(row('11').packs.length).toBeGreaterThan(0)
    expect(row('15').feeds.map(f => f.state)).toEqual(['unavailable', 'pending'])
    expect(FIX.errors.gc_invalid.detail.field).toBe('due')
  })
})

describe('GridComplianceSection', () => {
  it('renders each deliverable by phase with its status chip, next step and the accepted count', async () => {
    api.state.mockResolvedValue(state())
    renderSection()
    const r14 = await screen.findByTestId('gc-row-14')
    expect(within(r14).getByText('Submitted')).toBeTruthy()
    expect(within(r14).getByText(`Next: ${row('14').next_step}`, { exact: false })).toBeTruthy()
    expect(within(screen.getByTestId('gc-row-11')).getByText('Results in')).toBeTruthy()
    expect(screen.getByRole('group', { name: 'Phase 2: Compliance' })).toBeTruthy()
    expect(screen.getByText('0 of 25 accepted')).toBeTruthy()
  })

  it('says what blocks submission until the deliverable is submitted', async () => {
    api.state.mockResolvedValue(state())
    renderSection()
    const r8 = await screen.findByTestId('gc-row-08')
    expect(within(r8).getByText(/Submission waits for TRGC 06, TRGC 07 to be judged/)).toBeTruthy()
    expect(within(screen.getByTestId('gc-row-14')).queryByText(/Submission waits/)).toBeNull()
    expect(blockedMessage(row('25', { status: 'submitted' }), FIX.state.statuses)).toBeNull()
  })

  it('opens a deliverable and changes its status from the keyboard', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state([row('04')]))
    api.setStatus.mockResolvedValue(row('04', { status: 'data' }))
    renderSection()
    await screen.findByTestId('gc-row-04')
    await user.tab()
    const toggle = screen.getByRole('button', { name: /Dynamic model acceptance tests \(RMS\)/ })
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

  it('keeps the server status after a refused move, shows its words and rereads the tracker (U3)', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    api.setStatus.mockRejectedValue(refuse('gc_prerequisite_not_judged'))
    renderSection()
    await openRow(user, '08')
    const reads = api.state.mock.calls.length
    const select = screen.getByLabelText('Status') as HTMLSelectElement
    await user.selectOptions(select, 'submitted')
    expect((await screen.findByRole('alert')).textContent)
      .toBe(FIX.errors.gc_prerequisite_not_judged.detail.message)
    await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(reads))
    expect(select.value).toBe('not_started')
    expect(within(screen.getByTestId('gc-row-08')).getAllByText('Not started').length).toBeGreaterThan(0)
  })

  it.each(Object.keys(FIX.errors))('shows the %s refusal as text (U4)', async kind => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    api.setStatus.mockRejectedValue(refuse(kind))
    renderSection()
    await openRow(user, '13')
    await user.selectOptions(screen.getByLabelText('Status'), 'submitted')
    const shown = (await screen.findByRole('alert')).textContent ?? ''
    const { message, field } = FIX.errors[kind].detail
    if (field) expect(shown).toBe(`Due date: ${message.slice(field.length + 2)}`)
    else expect(shown).toBe(message)
  })

  it('shows a lock held by someone else in its words, under the status control (U4)', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    api.freeze.mockRejectedValue(refuse('project_locked'))
    renderSection()
    await openRow(user, '12')
    await user.click(screen.getByRole('button', { name: 'Freeze again' }))
    expect((await screen.findByRole('alert')).textContent).toBe(FIX.errors.project_locked.detail.message)
  })

  it('names the field of an invalid data-list edit and shows the server value again (U3, U4)', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    api.setData.mockRejectedValue(refuse('gc_invalid'))
    renderSection()
    await openRow(user, '15')
    const label = row('15').data[0].label
    const supplier = screen.getByLabelText(`Supplier of ${label}`) as HTMLInputElement
    await user.clear(supplier)
    await user.type(supplier, 'Someone else')
    await user.tab()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/^Due date: /)
    expect(alert.textContent).not.toMatch(/^due:/)
    expect(api.setData).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(
      (screen.getByLabelText(`Supplier of ${label}`) as HTMLInputElement).value).toBe(row('15').data[0].supplier))
  })

  it('a quiet request drops only the toast: the refusal still reaches the caller in words (U4)', async () => {
    restore = serve(config => reply(config, 409, { detail: FIX.errors.project_locked.detail }))
    const err = await real.gridComplianceApi.setStatus('P', '01', 'data').catch((e: unknown) => e)
    expect(real.gcErrorText(err)).toBe(FIX.errors.project_locked.detail.message)
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('marks a stale freeze and links back to the campus inputs', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    api.freeze.mockResolvedValue(row('12', { freeze_state: 'fresh', stale: false }))
    renderSection()
    const r = await screen.findByTestId('gc-row-12')
    expect(within(r).getByText('Out of date')).toBeTruthy()
    await openRow(user, '12')
    expect(screen.getByText(`Data frozen on ${row('12').freeze!.frozen_at.slice(0, 10)}`, { exact: false })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Campus file ›' }))
    expect(nav.withReturnFrom).toHaveBeenCalledTimes(1)
    expect(store.setSlidePanel).toHaveBeenCalledWith('campusElectrical')
    expect(store.requestPanelSection).toHaveBeenCalledWith('campusElectrical', 'campus_file')
    await user.click(screen.getByRole('button', { name: 'Freeze again' }))
    await waitFor(() => expect(api.freeze).toHaveBeenCalledWith('P', '12'))
  })

  it('keeps Freeze off after submission and says what was submitted on', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    renderSection()
    await openRow(user, '14')
    expect((screen.getByRole('button', { name: 'Freeze again' }) as HTMLButtonElement).disabled).toBe(true)
    const on = submittedFreeze().frozen_at.slice(0, 10)
    expect(screen.getByText(`submitted on the data frozen on ${on}, and that record is kept`, { exact: false }))
      .toBeTruthy()
    expect(screen.getByText(/What was submitted stays as frozen/)).toBeTruthy()
    expect(within(screen.getByTestId('gc-row-14')).getAllByText('As submitted').length).toBeGreaterThan(0)
    expect(within(screen.getByTestId('gc-row-14')).queryByText('Out of date')).toBeNull()
    expect(screen.queryByText(/freeze again once they are right/)).toBeNull()
  })

  it('says which feeds could not be read', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    renderSection()
    await openRow(user, '15')
    const [broken, pending] = row('15').feeds
    expect(screen.getByText(`Could not be read: ${broken.label}.`)).toBeTruthy()
    expect(screen.getByText(`Not in yet: ${pending.label}.`)).toBeTruthy()
  })

  it('shows no hash id, digest or file path anywhere, with a row open and submissions present (U6)', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state())
    renderSection()
    await openRow(user, '14')
    const r14 = row('14')
    expect(r14.submissions.length).toBeGreaterThan(0)
    const html = document.body.innerHTML
    for (const f of [r14.freeze!, ...r14.submissions]) {
      expect(html).not.toContain(f.model_set_id)
      expect(html).not.toContain(f.data_hash)
    }
    expect(html).not.toContain(row('11').packs[0].digest)
    expect(document.body.textContent).not.toMatch(/PLACEHOLDER|\.json|\.yaml|\/campus/)
  })

  it('says when the inputs were not compared with the submission, and why', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue(state([row('14', {
      since_submission: { inputs: 'not_compared', reason: 'The current inputs cannot be read.' },
    })]))
    renderSection()
    await openRow(user, '14')
    expect(screen.getByText(/were not compared with what was submitted\. The current inputs cannot be read\./)).toBeTruthy()
    expect(screen.queryByText(/The inputs changed after submission/)).toBeNull()
  })

  it('turns Freeze off with a visible reason while there is no campus file', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValue({ ...state([row('04')]), has_campus_input: false })
    renderSection()
    await openRow(user, '04')
    expect((screen.getByRole('button', { name: 'Freeze data' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Freeze is off: there is no campus file yet/)).toBeTruthy()
    expect(screen.getByText('Log comment is off until the TSO comment is typed.')).toBeTruthy()
  })

  it('shows a key for every chip style and what started means', async () => {
    api.state.mockResolvedValue(state())
    renderSection()
    const key = await screen.findByLabelText('Chip key')
    for (const word of ['Not started', 'Judged', 'TSO comments', 'Accepted', 'Frozen', 'Out of date',
      'Inputs unreadable']) expect(within(key).getByText(word)).toBeTruthy()
    const started = FIX.state.deliverables.filter(d => d.status !== 'not_started').length
    expect(screen.getByText(new RegExp(`Started: ${started} of 25 deliverables have moved past`))).toBeTruthy()
  })

  it('logs a TSO comment and answers it', async () => {
    const user = userEvent.setup()
    const [answered, open] = row('11').comments
    api.state.mockResolvedValue(state())
    api.addComment.mockResolvedValue(answered)
    api.updateComment.mockResolvedValue(answered)
    renderSection()
    await openRow(user, '11')
    expect(within(screen.getByTestId(`gc-comment-${answered.comment_id}`)).getByText('Responded')).toBeTruthy()
    await user.type(screen.getByLabelText('TSO comment'), 'Add the PoC P–Q chart.')
    await user.click(screen.getByRole('button', { name: 'Log comment' }))
    await waitFor(() => expect(api.addComment).toHaveBeenCalledWith('P', '11',
      expect.objectContaining({ comment: 'Add the PoC P–Q chart.' })))
    await user.type(screen.getByLabelText(`Response to ${open.comment_id}`), 'Added in section 4.')
    await user.selectOptions(screen.getByLabelText(`Pack for ${open.comment_id}`), '1')
    const item = screen.getByTestId(`gc-comment-${open.comment_id}`)
    await user.click(within(item).getByRole('button', { name: 'Save response' }))
    await waitFor(() => expect(api.updateComment).toHaveBeenCalledWith('P', '11', open.comment_id, {
      response: 'Added in section 4.', revision: '', status: 'responded', pack_version: 1,
    }))
  })

  it('shows a tracker it cannot read in the server words, with no rows and no start action (U2)', async () => {
    api.state.mockRejectedValue(refuse('gc_tracker_unreadable'))
    renderSection()
    expect((await screen.findByRole('alert')).textContent).toBe(FIX.errors.gc_tracker_unreadable.detail.message)
    expect(screen.getByText(/Changes are off until the compliance tracker can be read again/)).toBeTruthy()
    expect(screen.queryByTestId(/gc-row-/)).toBeNull()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByText(/Not started: no deliverable/)).toBeNull()
    expect(screen.queryByRole('button', { name: /start|freeze|build|log comment|save/i })).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('turns every write off with a visible reason once a reread finds the tracker unreadable (U2)', async () => {
    const user = userEvent.setup()
    api.state.mockResolvedValueOnce(state()).mockRejectedValue(refuse('gc_tracker_unreadable'))
    api.setStatus.mockResolvedValue(row('11', { status: 'judged' }))
    renderSection()
    await openRow(user, '11')
    await user.selectOptions(screen.getByLabelText('Status'), 'judged')
    expect((await screen.findByRole('alert')).textContent).toBe(FIX.errors.gc_tracker_unreadable.detail.message)
    expect(screen.getByText(/The rows below are as last read/)).toBeTruthy()
    const detail = document.getElementById('gc-detail-11')!
    expect(within(detail).getByText(/Changes are off until the compliance tracker can be read again/)).toBeTruthy()
    const controls = within(detail).queryAllByRole('combobox')
      .concat(within(detail).queryAllByRole('textbox'), within(detail).queryAllByRole('button')
        .filter(b => b.textContent !== 'Campus file ›'))
    expect(controls.length).toBeGreaterThan(5)
    for (const c of controls) expect((c as HTMLButtonElement).disabled, c.outerHTML.slice(0, 80)).toBe(true)
  })

  it('downloads the pack under the server file name with its bytes unchanged, off while building (U5)', async () => {
    const user = userEvent.setup()
    const bytes = new Blob([new Uint8Array([80, 75, 3, 4, 0, 255])], { type: 'application/zip' })
    let send: () => void = () => undefined
    restore = serve(config => new Promise(resolve => {
      send = () => resolve(reply(config, 200, bytes, { 'content-disposition': FIX.pack.content_disposition }))
    }))
    api.pack.mockImplementation(real.gridComplianceApi.pack)
    api.state.mockResolvedValue(state())
    const saved: Blob[] = []
    const names: string[] = []
    URL.createObjectURL = vi.fn((b: Blob) => { saved.push(b); return 'blob:pack' })
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download) })
    renderSection()
    await openRow(user, '11')
    await user.click(screen.getByRole('button', { name: 'Build report pack' }))
    const building = await screen.findByRole('button', { name: 'Building…' })
    expect((building as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Building the report pack/)).toBeTruthy()
    send()
    await waitFor(() => expect(names).toEqual(['TRGC-11-pack-v1.zip']))
    expect(saved).toEqual([bytes])
    expect(saved[0]).toBe(bytes)
    expect((screen.getByRole('button', { name: 'Build report pack' }) as HTMLButtonElement).disabled).toBe(false)
    click.mockRestore()
  })

  it('shows the server words when a pack build is refused, though the body came as a Blob (U5)', async () => {
    const user = userEvent.setup()
    restore = serve(config => reply(config, 409, refusalBlob('project_locked')))
    api.pack.mockImplementation(real.gridComplianceApi.pack)
    api.state.mockResolvedValue(state())
    renderSection()
    await openRow(user, '11')
    await user.click(screen.getByRole('button', { name: 'Build report pack' }))
    expect((await screen.findByRole('alert')).textContent).toBe(FIX.errors.project_locked.detail.message)
    expect(toast.error).not.toHaveBeenCalled()
  })
})
