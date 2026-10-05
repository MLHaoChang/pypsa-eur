// Plan F1-F follow-ups on the guided flow:
// F2 (gate S8 [S5]) the panel reopens the last open study after a reload, a
//    gone study falls back to the picker with a notice, and the projects
//    home lists that study's project's studies;
// F4 (S8, gate S8 [N-v2-1]) the draft load preview is debounced;
// F5 (gate S9) the ledger step shows the range the tornado tested, marking a
//    re-centred one.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import type { DecisionStudy, Findings, TornadoRow } from '../../api/decisionStudies'
import { findings, ledger, library, previewUpload, run, study } from './__fixtures__/payloads'
import { HELP, REOPEN_LABELS, TESTED_RANGE_LABELS } from '../../utils/decisionVocabulary'

const authMode = vi.hoisted(() => ({ authEnabled: false }))
vi.mock('../../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../../utils/projectActions', () => ({ switchToProject: vi.fn() }))
const toastMock = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toastMock }))
const navigate = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()), useNavigate: () => navigate,
}))
const api = vi.hoisted(() => ({
  availability: vi.fn(), list: vi.fn(), create: vi.fn(), get: vi.fn(), patchStep: vi.fn(), remove: vi.fn(),
  library: vi.fn(), preview: vi.fn(), ledger: vi.fn(), putLedger: vi.fn(), startRun: vi.fn(), run: vi.fn(),
  abortRun: vi.fn(), findings: vi.fn(), startTornado: vi.fn(), tornado: vi.fn(), abortTornado: vi.fn(),
  optionCase: vi.fn(), assembleReport: vi.fn(), report: vi.fn(),
}))
vi.mock('../../api/decisionStudies', async (orig) => ({
  ...(await orig<typeof import('../../api/decisionStudies')>()), decisionStudiesApi: api,
}))

import DecisionPanel from './DecisionPanel'
import DecisionStudiesEntry from './DecisionStudiesEntry'
import Intake, { PREVIEW_DEBOUNCE_MS } from './Intake'
import LedgerReview from './LedgerReview'
import { STUDY_REF_KEY, useDecisionStore } from './decisionStore'
import { useUIStore } from '../../store/uiStore'

function axErr(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } })
}
const REF = { project: 'site-base', studyId: study.study_id }
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

beforeEach(() => {
  authMode.authEnabled = false
  for (const f of Object.values(api)) f.mockReset()
  navigate.mockReset()
  api.get.mockResolvedValue(study)
  api.list.mockResolvedValue([])
  api.ledger.mockResolvedValue(ledger)
  api.library.mockResolvedValue(library)
  api.run.mockResolvedValue(run)
  api.findings.mockResolvedValue(findings)
  api.tornado.mockRejectedValue(axErr(404, { error_kind: 'tornado_never_run', message: 'x' }))
  api.report.mockRejectedValue(axErr(404, { error_kind: 'report_never_assembled', message: 'x' }))
  api.preview.mockResolvedValue(previewUpload)
  localStorage.clear()
  useDecisionStore.setState({ active: null, draft: null, view: null, watch: null, lost: null })
  useUIStore.setState({ currentProject: 'workbench-project', activeSlidePanel: null })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('F2: the last open study survives a reload', () => {
  it('opening a study remembers it, and closing forgets it', () => {
    useDecisionStore.getState().openStudy(REF)
    expect(JSON.parse(localStorage.getItem(STUDY_REF_KEY)!)).toEqual(REF)
    useDecisionStore.getState().close()
    expect(localStorage.getItem(STUDY_REF_KEY)).toBeNull()
  })

  it('a fresh page load reopens that study on its hub, not the current project’s list', async () => {
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(REF))
    vi.resetModules()
    const fresh = await import('./DecisionPanel')
    render(<QueryClientProvider client={client()}><fresh.default pollMs={20} /></QueryClientProvider>)
    expect(await screen.findByTestId('hub-sections')).toBeTruthy()
    expect(api.get).toHaveBeenCalledWith(REF.project, REF.studyId)
    expect(api.list).not.toHaveBeenCalled()
  })

  it('ignores a malformed stored value', async () => {
    localStorage.setItem(STUDY_REF_KEY, '{"project": 3}')
    vi.resetModules()
    const store = await import('./decisionStore')
    expect(store.useDecisionStore.getState().active).toBeNull()
  })

  it('a deleted study falls back to the picker, says why, and is forgotten', async () => {
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(REF))
    useDecisionStore.setState({ active: REF })
    api.get.mockRejectedValue(axErr(404, 'Study not found'))
    render(<QueryClientProvider client={client()}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    const lost = await screen.findByTestId('decision-lost')
    expect(lost.textContent).toContain(REOPEN_LABELS.lostTitle)
    expect(lost.textContent).toContain('site-base')
    expect(useDecisionStore.getState().active).toBeNull()
    expect(localStorage.getItem(STUDY_REF_KEY)).toBeNull()
    // The picker for the workbench's project is back.
    await waitFor(() => expect(api.list).toHaveBeenCalledWith('workbench-project'))
  })

  it('a 404 that means "studies are off" is not taken for a deleted study', async () => {
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(REF))
    useDecisionStore.setState({ active: REF })
    api.get.mockRejectedValue(axErr(404, { error_kind: 'decision_studies_unavailable', message: 'off' }))
    render(<QueryClientProvider client={client()}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    expect(await screen.findByTestId('decision-unavailable')).toBeTruthy()
    expect(useDecisionStore.getState().active).toEqual(REF)
    expect(JSON.parse(localStorage.getItem(STUDY_REF_KEY)!)).toEqual(REF)
  })
})

describe('F2: the projects home lists the last study’s project’s studies', () => {
  const other: DecisionStudy = { ...study, study_id: 'b'.repeat(32), name: 'Second look' }
  const renderEntry = () => render(
    <QueryClientProvider client={client()}><MemoryRouter><DecisionStudiesEntry /></MemoryRouter></QueryClientProvider>)

  it('lists them, marks the last open one, and opens a chosen study in the decision panel', async () => {
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(REF))
    useDecisionStore.setState({ active: REF })
    api.list.mockResolvedValue([study, other])
    renderEntry()
    const entry = await screen.findByTestId('decision-studies-entry')
    expect(api.list).toHaveBeenCalledWith('site-base')
    expect(within(entry).getByText(REOPEN_LABELS.homeHeading)).toBeTruthy()
    const items = within(entry).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain(study.name)
    expect(items[0].textContent).toContain(REOPEN_LABELS.lastOpen)
    expect(items[1].textContent).not.toContain(REOPEN_LABELS.lastOpen)
    fireEvent.click(within(items[1]).getByRole('button', { name: REOPEN_LABELS.openStudy('Second look') }))
    expect(useDecisionStore.getState().active).toEqual({ project: 'site-base', studyId: other.study_id })
    expect(useUIStore.getState().activeSlidePanel).toBe('decision')
    expect(navigate).toHaveBeenCalledWith('/app')
  })

  it('shows nothing without a remembered study, and asks nothing', async () => {
    renderEntry()
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByTestId('decision-studies-entry')).toBeNull()
    expect(api.list).not.toHaveBeenCalled()
  })

  it('shows nothing, and forgets the study, when its project is gone', async () => {
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(REF))
    useDecisionStore.setState({ active: REF })
    api.list.mockRejectedValue(axErr(404, "Project 'site-base' not found"))
    renderEntry()
    await waitFor(() => expect(localStorage.getItem(STUDY_REF_KEY)).toBeNull())
    expect(screen.queryByTestId('decision-studies-entry')).toBeNull()
  })

  it('shows nothing in auth mode, where the study routes refuse', async () => {
    authMode.authEnabled = true
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(REF))
    useDecisionStore.setState({ active: REF })
    renderEntry()
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByTestId('decision-studies-entry')).toBeNull()
    expect(api.list).not.toHaveBeenCalled()
  })
})

describe('F4: the draft load preview is debounced', () => {
  it('typing a connection limit posts one preview with the last value, not one per keystroke', async () => {
    render(<Intake mode="draft" project="workbench-project" initial={study.intake} library={library}
      initialStep="existing" onDraftChange={vi.fn()} saving={false} error={null} />)
    const input = screen.getByLabelText('Grid connection limit')
    for (const v of ['3', '3.', '3.5', '3.55', '3.555']) fireEvent.change(input, { target: { value: v } })
    await waitFor(() => expect(api.preview).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, PREVIEW_DEBOUNCE_MS + 150))
    expect(api.preview).toHaveBeenCalledTimes(1)
    expect(api.preview.mock.calls[0][1].site.connection_mw).toBe(3.555)
  })

  it('still previews a settled answer on its own', async () => {
    render(<Intake mode="draft" project="workbench-project" initial={study.intake} library={library}
      initialStep="existing" onDraftChange={vi.fn()} saving={false} error={null} />)
    await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(1), { timeout: PREVIEW_DEBOUNCE_MS + 1000 })
  })
})

describe('F5: the ledger step shows the range the tornado tested', () => {
  const rowFor = (key: string) => screen.getByTestId(`ledger-row-${key}`)
  const recentred = (rows: TornadoRow[]): TornadoRow[] => rows.map(r => (r.key === 'demand_charge_price'
    ? { ...r, low_value: 9100, high_value: 16900, notes: ['range_recentred_on_user_value'] } : r))
  const show = (tested?: TornadoRow[]) => render(
    <LedgerReview payload={ledger} intake={study.intake} saving={false} error={null} csvUrl="x"
      onSave={vi.fn()} onReset={vi.fn()} tested={tested} />)

  it('prints the tested range on each driver the check moved', () => {
    show(findings.robustness.tornado)
    const t = within(rowFor('discount_rate')).getByTestId('ledger-tested-range')
    expect(t.textContent).toBe(`${TESTED_RANGE_LABELS.tested}: 4.9 % to 9.1 %`)
    expect(within(rowFor('battery_inverter_eur_per_kw')).getByTestId('ledger-tested-range').textContent)
      .toBe(`${TESTED_RANGE_LABELS.tested}: 149.7495 EUR/kW to 278.1063 EUR/kW`)
    // A row the check did not move says nothing about a test.
    expect(within(rowFor('battery_inverter_lifetime_years')).queryByTestId('ledger-tested-range')).toBeNull()
  })

  it('marks a re-centred range and says why', () => {
    show(recentred(findings.robustness.tornado))
    const t = within(rowFor('demand_charge_price')).getByTestId('ledger-tested-range')
    expect(t.textContent).toContain('9100 EUR/MW/month to 16900 EUR/MW/month')
    expect(within(t).getByTestId('ledger-recentred').textContent).toBe(TESTED_RANGE_LABELS.recentred)
    expect(t.textContent).toContain(HELP.range_recentred_on_user_value)
    expect(within(rowFor('discount_rate')).queryByTestId('ledger-recentred')).toBeNull()
  })

  it('says nothing before a tornado has run', () => {
    show(undefined)
    expect(screen.queryAllByTestId('ledger-tested-range')).toHaveLength(0)
  })

  it('the panel’s ledger step reads the tested range from the findings', async () => {
    const f: Findings = { ...findings, robustness: { ...findings.robustness, tornado: recentred(findings.robustness.tornado) } }
    api.findings.mockResolvedValue(f)
    useDecisionStore.setState({ active: REF, view: 'ledger' })
    render(<QueryClientProvider client={client()}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    await screen.findByTestId('ledger-row-demand_charge_price')
    await waitFor(() => expect(within(rowFor('demand_charge_price')).getByTestId('ledger-recentred')).toBeTruthy())
  })
})
