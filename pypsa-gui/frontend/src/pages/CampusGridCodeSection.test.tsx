// The grid-code section of the campus electrical panel (plan C10). It holds
// the panel to the backend's contract:
// - the upload sends the file as multipart field `file`, and a refusal shows inline;
// - extraction is offered only when the backend has a key, and its refusals show inline;
// - each limit is shown with its clause, tag, page and quote, and the two
//   warnings (quote not found, value not stated) appear on the right rows;
// - Confirm, Save and Publish send what the backend expects;
// - a delete asks first, and a publish or delete refreshes the study's profile picker.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CampusState, GridCodeDraft, GridCodeList } from '../api/campusElectrical'
import CampusElectricalPanel from './CampusElectricalPanel'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }),
}))

const api = vi.hoisted(() => ({
  state: vi.fn(), draft: vi.fn(), save: vi.fn(), run: vi.fn(),
  gridCodes: vi.fn(), uploadGridCodeDocument: vi.fn(), deleteGridCodeDocument: vi.fn(),
  extractGridCode: vi.fn(), newGridCodeDraft: vi.fn(), getGridCodeDraft: vi.fn(),
  saveGridCodeDraft: vi.fn(), deleteGridCodeDraft: vi.fn(), confirmGridCodeLimit: vi.fn(),
  publishGridCode: vi.fn(), getPublishedGridCode: vi.fn(), deletePublishedGridCode: vi.fn(),
  library: vi.fn(), saveLibrary: vi.fn(), resetLibrary: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const DOC_ID = 'a'.repeat(64)
const DOC = { id: DOC_ID, sha256: DOC_ID, filename: 'vde-ar-n-4110.pdf', size: 1234567, uploaded_at: '2026-10-05T08:00:00+00:00', pages: 12 }
const YAML = 'campus:\n  pcc: {bus: GRID}\n'
const DRAFT_YAML = 'title: VDE\nvoltage_bands: []\n'

const state: CampusState = {
  campus_yaml: YAML, skipped: [],
  profiles: { eu_rfg_dcc_ce: 'EU RfG', vde_4110: 'VDE-AR-N 4110 (draft)' },
  settings: null, results: null, stale: false, hub_cost: null, hub_cost_reason: null,
}

const list = (over: Partial<GridCodeList> = {}): GridCodeList => ({
  shipped: { eu_rfg_dcc_ce: 'EU RfG' },
  published: [], drafts: [], documents: [DOC], extraction_available: true,
  ...over,
})

const draft = (over: Partial<GridCodeDraft> = {}): GridCodeDraft => ({
  id: 'vde_4110',
  yaml: DRAFT_YAML,
  profile: {
    title: 'VDE-AR-N 4110',
    voltage_bands: [{
      kv_min: 0, kv_max: 110, v_min: 0.9, v_max: 1.1, clause: 'Section 5.2.1', source: 'extracted',
      page: 14, quote: 'The voltage at the connection point shall stay between 90 % and 110 %',
    }],
    q_range_demand: {
      value: 0.33, clause: 'Section 5.7.3', source: 'extracted', page: 20,
      quote: 'a power factor between 0,95 inductive and 0,95 capacitive',
    },
    rvc_limit_pct: { value: 3, clause: 'not stated in the document; generic assumption', source: 'assumed' },
  },
  review: {
    document: DOC_ID, model: 'claude-x', extracted_at: '2026-10-05T09:00:00+00:00',
    filled_from_template: ['rvc_limit_pct'],
    limits: {
      'voltage_bands[0]': { quote_found: true, found_on_page: 14 },
      q_range_demand: { quote_found: false, found_on_page: null },
    },
  },
  unconfirmed: ['voltage_bands[0]', 'q_range_demand'],
  document: DOC,
  ...over,
})

const summary = (id: string, unconfirmed: string[] = []) => ({ id, title: `Title of ${id}`, unconfirmed, document: DOC_ID })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusElectricalPanel /></QueryClientProvider>)
}

const pdf = () => new File(['%PDF-1.7 test'], 'vde.pdf', { type: 'application/pdf' })
const refuse = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

async function openDraftFromList() {
  api.gridCodes.mockResolvedValue(list({ drafts: [summary('vde_4110', ['voltage_bands[0]', 'q_range_demand'])] }))
  api.getGridCodeDraft.mockResolvedValue(draft())
  renderPanel()
  await userEvent.click(await screen.findByRole('button', { name: 'Review draft vde_4110' }))
  return await screen.findByTestId('limit-voltage_bands[0]')
}

beforeEach(() => {
  store.currentProject = 'Hub A'
  vi.clearAllMocks()
  api.state.mockResolvedValue(state)
  api.gridCodes.mockResolvedValue(list())
  api.library.mockResolvedValue({ yaml: 'x: 1', is_default: true })
})
afterEach(() => cleanup())

describe('grid codes: the copy', () => {
  it('says the document never enters the repository, and that a person confirms each limit', async () => {
    renderPanel()
    const section = await screen.findByTestId('grid-codes')
    expect(section.textContent).toMatch(/stays in this project and is never added to the repository/i)
    expect(section.textContent).toMatch(/a person must confirm each one before it counts as the code/i)
  })
})

describe('grid codes: upload', () => {
  it('sends the file to the upload thunk', async () => {
    api.uploadGridCodeDocument.mockResolvedValue(DOC)
    renderPanel()
    const file = pdf()
    await userEvent.upload(await screen.findByLabelText('Grid-code document'), file)
    await waitFor(() => expect(api.uploadGridCodeDocument).toHaveBeenCalled())
    expect(api.uploadGridCodeDocument.mock.calls[0][0]).toBe('Hub A')
    expect(api.uploadGridCodeDocument.mock.calls[0][1]).toBe(file)
  })

  it('refreshes the lists after an upload', async () => {
    api.uploadGridCodeDocument.mockResolvedValue(DOC)
    renderPanel()
    await userEvent.upload(await screen.findByLabelText('Grid-code document'), pdf())
    await waitFor(() => expect(api.gridCodes.mock.calls.length).toBeGreaterThan(1))
  })

  it('shows the backend refusal inline', async () => {
    api.uploadGridCodeDocument.mockRejectedValue(refuse(415, 'the file is not a PDF (it does not start with %PDF-)'))
    renderPanel()
    await userEvent.upload(await screen.findByLabelText('Grid-code document'), pdf())
    expect(await screen.findByText(/does not start with %PDF-/)).toBeTruthy()
  })

  it('lists the uploaded documents with their size and pages', async () => {
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    expect(row.textContent).toContain('vde-ar-n-4110.pdf')
    expect(row.textContent).toContain('12 pages')
    expect(row.textContent).toContain('1.2 MB')
  })
})

describe('grid codes: extract', () => {
  it('is disabled without a key, says why, and offers the manual path', async () => {
    api.gridCodes.mockResolvedValue(list({ extraction_available: false }))
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    expect((within(row).getByRole('button', { name: 'Extract limits' }) as HTMLButtonElement).disabled).toBe(true)
    const section = screen.getByTestId('grid-codes')
    expect(section.textContent).toMatch(/needs an Anthropic API key/i)
    expect(section.textContent).toMatch(/new blank grid code/i)
    expect(screen.getByRole('button', { name: 'New blank grid code' })).toBeTruthy()
  })

  it('sends the document and the profile id, and shows the review rows', async () => {
    api.extractGridCode.mockResolvedValue(draft())
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    await userEvent.type(within(row).getByLabelText('Profile id'), 'vde_4110')
    await userEvent.click(within(row).getByRole('button', { name: 'Extract limits' }))
    await waitFor(() => expect(api.extractGridCode).toHaveBeenCalledWith('Hub A', DOC_ID, { profile_id: 'vde_4110', overwrite: false }))
    const band = await screen.findByTestId('limit-voltage_bands[0]')
    expect(band.textContent).toContain('Section 5.2.1')
    expect(band.textContent).toContain('extracted')
    expect(screen.getByTestId('limit-q_range_demand')).toBeTruthy()
    expect(screen.getByTestId('limit-rvc_limit_pct')).toBeTruthy()
  })

  it('leaves the profile id out when none is typed, so the backend derives one', async () => {
    api.extractGridCode.mockResolvedValue(draft())
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    await userEvent.click(within(row).getByRole('button', { name: 'Extract limits' }))
    await waitFor(() => expect(api.extractGridCode).toHaveBeenCalledWith('Hub A', DOC_ID, { profile_id: undefined, overwrite: false }))
  })

  it('shows a 503 inline with the backend detail', async () => {
    api.extractGridCode.mockRejectedValue(refuse(503, 'extraction needs ANTHROPIC_API_KEY in the environment settings'))
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    await userEvent.click(within(row).getByRole('button', { name: 'Extract limits' }))
    expect(await screen.findByText(/needs ANTHROPIC_API_KEY in the environment settings/)).toBeTruthy()
  })

  it('offers to overwrite after a 409, and then sends overwrite', async () => {
    api.extractGridCode.mockRejectedValueOnce(refuse(409, "a draft 'vde_4110' already exists"))
    api.extractGridCode.mockResolvedValueOnce(draft())
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    await userEvent.type(within(row).getByLabelText('Profile id'), 'vde_4110')
    await userEvent.click(within(row).getByRole('button', { name: 'Extract limits' }))
    expect(await screen.findByText(/already exists/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Replace the existing draft' }))
    await waitFor(() => expect(api.extractGridCode).toHaveBeenLastCalledWith('Hub A', DOC_ID, { profile_id: 'vde_4110', overwrite: true }))
  })

  it('refuses a malformed profile id before it is sent', async () => {
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    await userEvent.type(within(row).getByLabelText('Profile id'), 'Bad Id!')
    expect((within(row).getByRole('button', { name: 'Extract limits' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('grid codes: a blank grid code', () => {
  it('sends the id and the title', async () => {
    api.newGridCodeDraft.mockResolvedValue(draft({ review: null, unconfirmed: [] }))
    renderPanel()
    await screen.findByTestId('grid-codes')
    await userEvent.type(screen.getByLabelText('New grid code id'), 'site_code')
    await userEvent.type(screen.getByLabelText('New grid code title'), 'Site code')
    await userEvent.click(screen.getByRole('button', { name: 'New blank grid code' }))
    await waitFor(() => expect(api.newGridCodeDraft).toHaveBeenCalledWith('Hub A', {
      profile_id: 'site_code', title: 'Site code', overwrite: false,
    }))
    expect(await screen.findByLabelText('Grid-code draft')).toBeTruthy()
  })

  it('needs an id', async () => {
    renderPanel()
    await screen.findByTestId('grid-codes')
    expect((screen.getByRole('button', { name: 'New blank grid code' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows a refusal inline', async () => {
    api.newGridCodeDraft.mockRejectedValue(refuse(422, "profile id 'eu_rfg_dcc_ce' is a shipped profile"))
    renderPanel()
    await screen.findByTestId('grid-codes')
    await userEvent.type(screen.getByLabelText('New grid code id'), 'eu_rfg_dcc_ce')
    await userEvent.click(screen.getByRole('button', { name: 'New blank grid code' }))
    expect(await screen.findByText(/is a shipped profile/)).toBeTruthy()
  })
})

describe('grid codes: the review form', () => {
  it('shows the value, the clause, the tag, the page and the verbatim quote', async () => {
    const band = await openDraftFromList()
    expect(band.textContent).toContain('0–110 kV')
    expect(band.textContent).toContain('0.9–1.1 pu')
    expect(band.textContent).toContain('Section 5.2.1')
    expect(band.textContent).toContain('extracted')
    expect(band.textContent).toContain('page 14')
    expect(band.textContent).toContain('The voltage at the connection point shall stay between 90 % and 110 %')
    const q = screen.getByTestId('limit-q_range_demand')
    expect(q.textContent).toContain('0.33')
    expect(q.textContent).toContain('page 20')
  })

  it('warns "quote not found on its page" only where quote_found is false', async () => {
    await openDraftFromList()
    expect(within(screen.getByTestId('limit-q_range_demand')).getByText(/quote not found on its page/i)).toBeTruthy()
    expect(within(screen.getByTestId('limit-voltage_bands[0]')).queryByText(/quote not found on its page/i)).toBeNull()
    expect(within(screen.getByTestId('limit-rvc_limit_pct')).queryByText(/quote not found on its page/i)).toBeNull()
  })

  it('does not call a quote missing when the document has been deleted (quote_found null)', async () => {
    api.gridCodes.mockResolvedValue(list({ drafts: [summary('vde_4110', ['q_range_demand'])] }))
    const d = draft()
    d.review!.limits.q_range_demand = { quote_found: null, found_on_page: null }
    api.getGridCodeDraft.mockResolvedValue(d)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Review draft vde_4110' }))
    const q = await screen.findByTestId('limit-q_range_demand')
    expect(within(q).queryByText(/quote not found on its page/i)).toBeNull()
    expect(within(q).getByText(/document is no longer in the project/i)).toBeTruthy()
  })

  it('marks a limit the document did not state, by its top-level key', async () => {
    await openDraftFromList()
    expect(within(screen.getByTestId('limit-rvc_limit_pct')).getByText(/not stated in the document — generic value/i)).toBeTruthy()
    expect(within(screen.getByTestId('limit-q_range_demand')).queryByText(/not stated in the document — generic value/i)).toBeNull()
    expect(within(screen.getByTestId('limit-voltage_bands[0]')).queryByText(/not stated in the document — generic value/i)).toBeNull()
  })

  it('marks every band when the template filled voltage_bands', async () => {
    api.gridCodes.mockResolvedValue(list({ drafts: [summary('vde_4110')] }))
    const d = draft()
    d.profile.voltage_bands = [
      { kv_min: 0, kv_max: 110, v_min: 0.9, v_max: 1.1, clause: 'generic', source: 'assumed' },
      { kv_min: 110, kv_max: 400, v_min: 0.9, v_max: 1.05, clause: 'generic', source: 'assumed' },
    ]
    d.review!.filled_from_template = ['voltage_bands']
    api.getGridCodeDraft.mockResolvedValue(d)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Review draft vde_4110' }))
    for (const path of ['voltage_bands[0]', 'voltage_bands[1]']) {
      expect(within(await screen.findByTestId(`limit-${path}`)).getByText(/not stated in the document — generic value/i)).toBeTruthy()
    }
  })

  it('offers Confirm on an extracted limit only', async () => {
    await openDraftFromList()
    expect(within(screen.getByTestId('limit-voltage_bands[0]')).getByRole('button', { name: /^Confirm/ })).toBeTruthy()
    expect(within(screen.getByTestId('limit-q_range_demand')).getByRole('button', { name: /^Confirm/ })).toBeTruthy()
    expect(within(screen.getByTestId('limit-rvc_limit_pct')).queryByRole('button', { name: /^Confirm/ })).toBeNull()
  })

  it('confirms the limit at its path and shows the new tag', async () => {
    await openDraftFromList()
    const confirmed = draft({ unconfirmed: ['q_range_demand'] })
    confirmed.profile.voltage_bands[0].source = 'code'
    api.confirmGridCodeLimit.mockResolvedValue(confirmed)
    await userEvent.click(within(screen.getByTestId('limit-voltage_bands[0]')).getByRole('button', { name: /^Confirm/ }))
    await waitFor(() => expect(api.confirmGridCodeLimit).toHaveBeenCalledWith('Hub A', 'vde_4110', 'voltage_bands[0]'))
    await waitFor(() => expect(within(screen.getByTestId('limit-voltage_bands[0]')).queryByRole('button', { name: /^Confirm/ })).toBeNull())
    expect(screen.getByTestId('limit-voltage_bands[0]').textContent).toContain('code')
  })

  it('confirms the reactive range by its own path', async () => {
    await openDraftFromList()
    api.confirmGridCodeLimit.mockResolvedValue(draft())
    await userEvent.click(within(screen.getByTestId('limit-q_range_demand')).getByRole('button', { name: /^Confirm/ }))
    await waitFor(() => expect(api.confirmGridCodeLimit).toHaveBeenCalledWith('Hub A', 'vde_4110', 'q_range_demand'))
  })

  it('shows a confirm refusal inline', async () => {
    await openDraftFromList()
    api.confirmGridCodeLimit.mockRejectedValue(refuse(422, 'limit q_range_demand is not extracted'))
    await userEvent.click(within(screen.getByTestId('limit-q_range_demand')).getByRole('button', { name: /^Confirm/ }))
    expect(await screen.findByText(/is not extracted/)).toBeTruthy()
  })
})

describe('grid codes: the draft file', () => {
  it('saves the edited YAML, and shows the field a 422 names', async () => {
    await openDraftFromList()
    api.saveGridCodeDraft.mockRejectedValue(refuse(422, 'voltage_bands[0]: v_min (1.2) is above v_max (1.1)'))
    const editor = screen.getByLabelText('Grid-code draft')
    expect((editor as HTMLTextAreaElement).value).toBe(DRAFT_YAML)
    await userEvent.type(editor, '# edited')
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    await waitFor(() => expect(api.saveGridCodeDraft).toHaveBeenCalledWith('Hub A', 'vde_4110', `${DRAFT_YAML}# edited`))
    expect(await screen.findByText(/voltage_bands\[0\]: v_min/)).toBeTruthy()
  })

  it('does not save, confirm or publish edits it has not saved', async () => {
    await openDraftFromList()
    expect((screen.getByRole('button', { name: 'Save draft' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.type(screen.getByLabelText('Grid-code draft'), '#')
    expect((screen.getByRole('button', { name: 'Save draft' }) as HTMLButtonElement).disabled).toBe(false)
    expect((within(screen.getByTestId('limit-q_range_demand')).getByRole('button', { name: /^Confirm/ }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Publish' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('grid codes: publish', () => {
  it('is disabled with unsaved edits even when every limit is confirmed', async () => {
    api.gridCodes.mockResolvedValue(list({ drafts: [summary('vde_4110')] }))
    api.getGridCodeDraft.mockResolvedValue(draft({ unconfirmed: [] }))
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Review draft vde_4110' }))
    const btn = await screen.findByRole('button', { name: 'Publish' })
    expect((btn as HTMLButtonElement).disabled).toBe(false)
    await userEvent.type(screen.getByLabelText('Grid-code draft'), '#')
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('is disabled while limits are unconfirmed', async () => {
    await openDraftFromList()
    expect((screen.getByRole('button', { name: 'Publish' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('the checkbox enables it, and it sends allow_unconfirmed: true', async () => {
    await openDraftFromList()
    api.publishGridCode.mockResolvedValue({ id: 'vde_4110', unconfirmed: ['voltage_bands[0]'], profiles: {} })
    await userEvent.click(screen.getByLabelText('Publish with unconfirmed limits (they will show as extracted on every report)'))
    const btn = screen.getByRole('button', { name: 'Publish' }) as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    await userEvent.click(btn)
    await waitFor(() => expect(api.publishGridCode).toHaveBeenCalledWith('Hub A', 'vde_4110', true))
  })

  it('is enabled without the checkbox once every limit is confirmed, and sends allow_unconfirmed: false', async () => {
    api.gridCodes.mockResolvedValue(list({ drafts: [summary('vde_4110')] }))
    api.getGridCodeDraft.mockResolvedValue(draft({ unconfirmed: [] }))
    api.publishGridCode.mockResolvedValue({ id: 'vde_4110', unconfirmed: [], profiles: {} })
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Review draft vde_4110' }))
    expect(screen.queryByLabelText(/Publish with unconfirmed limits/)).toBeNull()
    const btn = await screen.findByRole('button', { name: 'Publish' })
    expect((btn as HTMLButtonElement).disabled).toBe(false)
    await userEvent.click(btn)
    await waitFor(() => expect(api.publishGridCode).toHaveBeenCalledWith('Hub A', 'vde_4110', false))
  })

  it('shows a 409 inline', async () => {
    await openDraftFromList()
    api.publishGridCode.mockRejectedValue(refuse(409, "draft 'vde_4110' has unconfirmed limits ['q_range_demand']"))
    await userEvent.click(screen.getByLabelText(/Publish with unconfirmed limits/))
    await userEvent.click(screen.getByRole('button', { name: 'Publish' }))
    expect(await screen.findByText(/has unconfirmed limits/)).toBeTruthy()
  })

  it('refreshes the campus state, so the grid-code picker lists the new profile', async () => {
    await openDraftFromList()
    api.publishGridCode.mockResolvedValue({ id: 'vde_4110', unconfirmed: [], profiles: {} })
    const before = api.state.mock.calls.length
    await userEvent.click(screen.getByLabelText(/Publish with unconfirmed limits/))
    await userEvent.click(screen.getByRole('button', { name: 'Publish' }))
    await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(before))
  })
})

describe('grid codes: delete', () => {
  it('asks before deleting a document, and does nothing on cancel', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    await userEvent.click(within(row).getByRole('button', { name: 'Delete document vde-ar-n-4110.pdf' }))
    expect(confirm).toHaveBeenCalled()
    expect(api.deleteGridCodeDocument).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('deletes a document once confirmed', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.deleteGridCodeDocument.mockResolvedValue({ deleted: DOC_ID })
    renderPanel()
    const row = await screen.findByTestId(`document-${DOC_ID}`)
    api.gridCodes.mockResolvedValue(list({ documents: [] }))
    await userEvent.click(within(row).getByRole('button', { name: 'Delete document vde-ar-n-4110.pdf' }))
    await waitFor(() => expect(api.deleteGridCodeDocument).toHaveBeenCalledWith('Hub A', DOC_ID))
    await waitFor(() => expect(screen.queryByTestId(`document-${DOC_ID}`)).toBeNull())
    confirm.mockRestore()
  })

  it('asks before deleting a draft, and deletes it once confirmed', async () => {
    api.gridCodes.mockResolvedValue(list({ drafts: [summary('vde_4110')] }))
    api.deleteGridCodeDraft.mockResolvedValue({ deleted: 'vde_4110' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPanel()
    const btn = await screen.findByRole('button', { name: 'Delete draft vde_4110' })
    await userEvent.click(btn)
    expect(confirm).toHaveBeenCalled()
    expect(api.deleteGridCodeDraft).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    await userEvent.click(btn)
    await waitFor(() => expect(api.deleteGridCodeDraft).toHaveBeenCalledWith('Hub A', 'vde_4110'))
    confirm.mockRestore()
  })

  it('asks before deleting a published profile, then refreshes the campus state', async () => {
    api.gridCodes.mockResolvedValue(list({ published: [summary('vde_4110')] }))
    api.deletePublishedGridCode.mockResolvedValue({ deleted: 'vde_4110' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPanel()
    const btn = await screen.findByRole('button', { name: 'Delete published vde_4110' })
    await userEvent.click(btn)
    expect(api.deletePublishedGridCode).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    const before = api.state.mock.calls.length
    await userEvent.click(btn)
    await waitFor(() => expect(api.deletePublishedGridCode).toHaveBeenCalledWith('Hub A', 'vde_4110'))
    await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(before))
    confirm.mockRestore()
  })

  it('closes the review form when its draft is deleted', async () => {
    await openDraftFromList()
    api.deleteGridCodeDraft.mockResolvedValue({ deleted: 'vde_4110' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await userEvent.click(screen.getByRole('button', { name: 'Delete draft' }))
    await waitFor(() => expect(screen.queryByLabelText('Grid-code draft')).toBeNull())
    confirm.mockRestore()
  })
})

describe('grid codes: the study picker', () => {
  it('marks a profile with unconfirmed limits', async () => {
    api.gridCodes.mockResolvedValue(list({ published: [summary('vde_4110', ['q_range_demand'])] }))
    renderPanel()
    const picker = await screen.findByLabelText('Grid code')
    await waitFor(() => {
      const labels = Array.from((picker as HTMLSelectElement).options).map(o => o.textContent)
      expect(labels).toContain('VDE-AR-N 4110 (draft) (unconfirmed limits)')
    })
    const plain = Array.from((picker as HTMLSelectElement).options).map(o => o.textContent)
    expect(plain).toContain('EU RfG')
  })

  it('does not mark a fully confirmed profile', async () => {
    api.gridCodes.mockResolvedValue(list({ published: [summary('vde_4110', [])] }))
    renderPanel()
    await screen.findByTestId('grid-codes')
    const picker = await screen.findByLabelText('Grid code')
    const labels = Array.from((picker as HTMLSelectElement).options).map(o => o.textContent)
    expect(labels.some(l => l?.includes('unconfirmed'))).toBe(false)
  })
})
