// The asset-library section of the campus electrical panel (plan C9). It holds
// the panel to the backend's contract:
// - the editor shows the library the backend sent, and a badge says whether it
//   is the shipped default or the project's copy;
// - Save sends the edited text, shows a 422 inline, and refreshes the study's state;
// - Reset asks first, then deletes the copy;
// - the page says every shipped cost is an assumed placeholder.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CampusState } from '../api/campusElectrical'
import CampusElectricalPanel from './CampusElectricalPanel'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }),
}))

const api = vi.hoisted(() => ({
  state: vi.fn(), draft: vi.fn(), save: vi.fn(), run: vi.fn(), gridCodes: vi.fn(),
  library: vi.fn(), saveLibrary: vi.fn(), resetLibrary: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const LIB = 'discount_rate: {value: 0.07, source: assumed}\ntransformers: []\n'
const state: CampusState = {
  campus_yaml: 'campus:\n  pcc: {bus: GRID}\n', skipped: [], profiles: { eu_rfg_dcc_ce: 'EU RfG' },
  settings: null, results: null, stale: false, hub_cost: null, hub_cost_reason: 'no study has run yet',
}
const refuse = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusElectricalPanel /></QueryClientProvider>)
}

beforeEach(() => {
  store.currentProject = 'Hub A'
  vi.clearAllMocks()
  api.state.mockResolvedValue(state)
  api.gridCodes.mockResolvedValue({ shipped: {}, published: [], drafts: [], documents: [], extraction_available: false })
  api.library.mockResolvedValue({ yaml: LIB, is_default: true })
})
afterEach(() => cleanup())

describe('asset library: the editor', () => {
  it('shows the library and says it is the default one', async () => {
    renderPanel()
    const editor = (await screen.findByLabelText('Asset library')) as HTMLTextAreaElement
    await waitFor(() => expect(editor.value).toBe(LIB))
    expect(screen.getByTestId('library-badge').textContent).toBe('default library')
    expect(api.library).toHaveBeenCalledWith('Hub A')
  })

  it('says plainly that every shipped cost is an assumed placeholder', async () => {
    renderPanel()
    const section = await screen.findByTestId('asset-library')
    expect(section.textContent).toMatch(/every cost in the shipped library is an assumed placeholder/i)
    expect(section.textContent).toMatch(/replace it with quotes/i)
  })

  it('badges a project copy', async () => {
    api.library.mockResolvedValue({ yaml: LIB, is_default: false })
    renderPanel()
    await waitFor(() => expect(screen.getByTestId('library-badge').textContent).toBe('project library'))
  })

  it('saves the edited text, becomes the project library and refreshes the study state', async () => {
    api.saveLibrary.mockImplementation(async (_p: string, yaml: string) => ({ yaml, is_default: false }))
    renderPanel()
    const editor = (await screen.findByLabelText('Asset library')) as HTMLTextAreaElement
    await waitFor(() => expect(editor.value).toBe(LIB))
    const save = screen.getByRole('button', { name: 'Save library' })
    expect((save as HTMLButtonElement).disabled).toBe(true)               // nothing to save yet
    await userEvent.type(editor, '# mine')
    const calls = api.state.mock.calls.length
    await userEvent.click(save)
    await waitFor(() => expect(api.saveLibrary).toHaveBeenCalledWith('Hub A', `${LIB}# mine`))
    await waitFor(() => expect(screen.getByTestId('library-badge').textContent).toBe('project library'))
    await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(calls))   // the stale flag may have changed
  })

  it('shows the 422 inline, names the field, and keeps the edit', async () => {
    api.saveLibrary.mockRejectedValue(refuse(422, "transformers[TR_1].capex_eur must be positive, got -5"))
    renderPanel()
    const editor = (await screen.findByLabelText('Asset library')) as HTMLTextAreaElement
    await waitFor(() => expect(editor.value).toBe(LIB))
    await userEvent.type(editor, '# bad')
    await userEvent.click(screen.getByRole('button', { name: 'Save library' }))
    expect(await screen.findByText(/transformers\[TR_1\]\.capex_eur must be positive/)).toBeTruthy()
    expect(editor.value).toBe(`${LIB}# bad`)
    expect(screen.getByTestId('library-badge').textContent).toBe('default library')
  })

  it('clears the inline refusal on the next attempt', async () => {
    api.saveLibrary.mockRejectedValueOnce(refuse(422, 'asset library: missing cables'))
    api.saveLibrary.mockImplementation(async (_p: string, yaml: string) => ({ yaml, is_default: false }))
    renderPanel()
    const editor = (await screen.findByLabelText('Asset library')) as HTMLTextAreaElement
    await waitFor(() => expect(editor.value).toBe(LIB))
    await userEvent.type(editor, '#')
    await userEvent.click(screen.getByRole('button', { name: 'Save library' }))
    expect(await screen.findByText(/missing cables/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Save library' }))
    await waitFor(() => expect(screen.queryByText(/missing cables/)).toBeNull())
  })
})

describe('asset library: reset', () => {
  it('is offered only for a project copy', async () => {
    renderPanel()
    await screen.findByLabelText('Asset library')
    expect((screen.getByRole('button', { name: 'Reset to default' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('asks first, and does nothing when the answer is no', async () => {
    api.library.mockResolvedValue({ yaml: LIB, is_default: false })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Reset to default' }))
    expect(confirm).toHaveBeenCalled()
    expect(confirm.mock.calls[0][0]).toMatch(/replaces your library with the shipped default/i)
    expect(api.resetLibrary).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('deletes the copy once confirmed, and shows the shipped default', async () => {
    api.library.mockResolvedValue({ yaml: `${LIB}# mine\n`, is_default: false })
    api.resetLibrary.mockResolvedValue({ yaml: LIB, is_default: true })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Reset to default' }))
    await waitFor(() => expect(api.resetLibrary).toHaveBeenCalledWith('Hub A'))
    await waitFor(() => expect(screen.getByTestId('library-badge').textContent).toBe('default library'))
    expect((screen.getByLabelText('Asset library') as HTMLTextAreaElement).value).toBe(LIB)
    confirm.mockRestore()
  })

  it('shows a refusal of the reset inline', async () => {
    api.library.mockResolvedValue({ yaml: LIB, is_default: false })
    api.resetLibrary.mockRejectedValue(refuse(409, 'project is locked by another user'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Reset to default' }))
    expect(await screen.findByText(/locked by another user/)).toBeTruthy()
    confirm.mockRestore()
  })
})
