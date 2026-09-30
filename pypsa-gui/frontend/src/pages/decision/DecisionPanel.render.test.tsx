// The decision panel end to end over a mocked client (plan S8 acceptance):
// availability (auth mode, flag off) degrades to a reason, never a crash; the
// entry state; polling reads the run status route and fetches findings ONCE;
// the Expert view opens the option fork after the warning; a changed intake
// makes the findings say a re-run is needed; an edited ledger row shows
// customised; a new study is answered in draft and created.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { DecisionStudy, LedgerPayload, RunRecord } from '../../api/decisionStudies'
import { findings, ledger, library, previewUpload, run, study } from './__fixtures__/payloads'
import { EXPERT_WARNING, ERROR_COPY } from '../../utils/decisionVocabulary'

const authMode = vi.hoisted(() => ({ authEnabled: false }))
vi.mock('../../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
const switchToProject = vi.hoisted(() => vi.fn(async () => ({ status: 'switched' })))
vi.mock('../../utils/projectActions', () => ({ switchToProject }))
const toastMock = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toastMock }))
const uploadFile = vi.hoisted(() => vi.fn())
vi.mock('../../api/uploads', async (orig) => ({ ...(await orig<typeof import('../../api/uploads')>()), uploadFile }))
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
import DecisionRunWatcher from './DecisionRunWatcher'
import { useDecisionStore } from './decisionStore'
import { useUIStore } from '../../store/uiStore'

function axErr(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } })
}

const REF = { project: 'site-base', studyId: study.study_id }

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <DecisionRunWatcher pollMs={20} />
      <DecisionPanel pollMs={20} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  authMode.authEnabled = false
  for (const f of Object.values(api)) f.mockReset()
  switchToProject.mockClear()
  api.get.mockResolvedValue(study)
  api.ledger.mockResolvedValue(ledger)
  api.library.mockResolvedValue(library)
  api.run.mockResolvedValue(run)
  api.tornado.mockRejectedValue(axErr(404, { error_kind: 'tornado_never_run', message: 'x' }))
  api.findings.mockResolvedValue(findings)
  api.report.mockRejectedValue(axErr(404, { error_kind: 'report_never_assembled', message: 'x' }))
  api.optionCase.mockRejectedValue(axErr(404, { error_kind: 'option_not_solved', message: 'x' }))
  api.preview.mockResolvedValue(previewUpload)
  useDecisionStore.setState({ active: null, draft: null, view: null, watch: null })
  useUIStore.setState({ currentProject: 'workbench-project' })
})
afterEach(() => cleanup())

describe('availability', () => {
  it('in multi-user mode says why and calls nothing', () => {
    authMode.authEnabled = true
    useDecisionStore.setState({ active: REF })
    renderPanel()
    expect(screen.getByTestId('decision-unavailable').textContent).toContain(ERROR_COPY.decision_studies_unavailable.title)
    expect(api.get).not.toHaveBeenCalled()
  })

  it('with the flag off says how to switch it on', async () => {
    api.list.mockRejectedValue(axErr(404, { code: 'decision_studies_disabled', message: 'Set PYPSAGUI_DECISION_STUDIES=1' }))
    renderPanel()
    expect((await screen.findByTestId('decision-unavailable')).textContent).toContain('PYPSAGUI_DECISION_STUDIES=1')
  })
})

describe('the study picker', () => {
  it('lists the current project’s studies and opens one', async () => {
    api.list.mockResolvedValue([study])
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: study.name }))
    expect(useDecisionStore.getState().active).toEqual({ project: 'workbench-project', studyId: study.study_id })
    expect(await screen.findByTestId('decision-study')).toBeTruthy()
  })
})

describe('the entry state', () => {
  it('opens the hub on a complete study, maturity from the ledger', async () => {
    useDecisionStore.setState({ active: REF })
    renderPanel()
    expect(await screen.findByTestId('hub-sections')).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId('decision-maturity').textContent).toContain('Screening'))
  })

  it('opens the intake on an incomplete study', async () => {
    api.get.mockResolvedValue({ ...study, intake: { site: { zone: 'DE' } } } as DecisionStudy)
    useDecisionStore.setState({ active: REF })
    renderPanel()
    expect(await screen.findByRole('navigation', { name: 'Decision study intake steps' })).toBeTruthy()
  })
})

describe('polling', () => {
  it('polls the run status route and fetches the findings once, then opens the verdict', async () => {
    const running = { ...(run as RunRecord), status: 'running' as const, solved: [], current: 'none', pending: run.options }
    api.run.mockResolvedValueOnce(running).mockResolvedValueOnce(running).mockResolvedValue(run)
    useDecisionStore.setState({ active: REF, view: 'run' })
    renderPanel()
    expect(await screen.findByTestId('verdict-class', {}, { timeout: 3000 })).toBeTruthy()
    await new Promise(r => setTimeout(r, 150))
    expect(api.run.mock.calls.length).toBeGreaterThanOrEqual(3)
    expect(api.findings).toHaveBeenCalledTimes(1)
    // The header's watcher names the verdict when the run ends.
    expect(toastMock.success).toHaveBeenCalledWith('Decision study finished: Recommended')
  })
})

describe('the Expert view', () => {
  it('warns, then opens the option fork the verdict names — never the base project', async () => {
    useDecisionStore.setState({ active: REF, view: 'verdict' })
    renderPanel()
    await screen.findByTestId('verdict-class')
    const btn = screen.getByTestId('expert-view')
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(btn)
    expect(screen.getByTestId('expert-warning').textContent).toContain(EXPERT_WARNING)
    expect(switchToProject).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Open anyway' }))
    const fork = findings.options.find(o => o.option_id === findings.verdict.option_id)!.project_ref
    await waitFor(() => expect(switchToProject).toHaveBeenCalledWith(fork, expect.anything()))
    expect(switchToProject).not.toHaveBeenCalledWith(study.base_project, expect.anything())
  })
})

describe('a changed intake', () => {
  it('makes the findings say a re-run is needed, on the page and on the hub', async () => {
    api.findings.mockRejectedValue(axErr(409, { error_kind: 'intake_changed_since_run', message: 'changed' }))
    useDecisionStore.setState({ active: REF, view: 'verdict' })
    renderPanel()
    const r = await screen.findByTestId('findings-refusal')
    expect(r.textContent).toContain(ERROR_COPY.intake_changed_since_run.title)
    expect(r.textContent).toContain('Run the study again')
    fireEvent.click(screen.getByRole('button', { name: 'Overview' }))
    const hub = await screen.findByTestId('hub-sections')
    expect(within(hub.querySelector('[data-section="findings"]') as HTMLElement).getByText('Re-run needed')).toBeTruthy()
  })
})

describe('the ledger through the panel', () => {
  it('shows a row edited in the UI as customised', async () => {
    const edited: LedgerPayload = { ...ledger, ledger: { ...ledger.ledger, rows: ledger.ledger.rows.map(r =>
      r.key === 'battery_inverter_eur_per_kw' ? { ...r, value: 180, provenance: 'user' as const, status: 'customised' as const } : r) } }
    api.putLedger.mockResolvedValue(edited)
    useDecisionStore.setState({ active: REF, view: 'ledger' })
    renderPanel()
    const row = await screen.findByTestId('ledger-row-battery_inverter_eur_per_kw')
    fireEvent.change(within(row).getByRole('spinbutton'), { target: { value: '180' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(within(screen.getByTestId('ledger-row-battery_inverter_eur_per_kw')).getByTestId('ledger-status').textContent).toBe('Customised'))
    expect(api.putLedger).toHaveBeenCalledWith('site-base', study.study_id,
      { rows: [{ key: 'battery_inverter_eur_per_kw', value: 180, unit: 'EUR/kW', currency_year: 2020 }] })
  })
})

describe('a new study in draft', () => {
  it('checks an uploaded load through the backend and creates the study from the answers', async () => {
    uploadFile.mockResolvedValue({ file_id: 'f1', filename: 'meter.csv' })
    api.create.mockResolvedValue({ ...study, base_project_name: 'My battery' })
    useDecisionStore.setState({ draft: { pathProject: 'workbench-project', name: 'My battery', baseName: '' }, view: 'intake' })
    renderPanel()
    fireEvent.change(await screen.findByLabelText('Country or grid zone'), { target: { value: 'DE' } })
    fireEvent.click(screen.getByRole('button', { name: /What exists today/ }))
    fireEvent.change(screen.getByLabelText('Grid connection limit'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: /Your consumption/ }))
    fireEvent.click(screen.getByLabelText(/Upload your metered load/))
    fireEvent.change(screen.getByLabelText('Load file'), { target: { files: [new File(['x'], 'meter.csv')] } })
    const check = await screen.findByTestId('load-check')
    // Gate S8 BC-S8-5: a draft's file stays in the browser until creation.
    expect(uploadFile).not.toHaveBeenCalled()
    expect(check.textContent).toContain('in kW')
    expect(screen.getByTestId('load-peak').textContent).toContain('above your grid connection')
    expect(screen.getByTestId('load-warning-timeseries_spike').textContent).toContain('misplaced decimal point')
    fireEvent.click(screen.getByRole('button', { name: /Check your answers/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Create the study' }))
    await waitFor(() => expect(api.create).toHaveBeenCalled())
    const [path, body] = api.create.mock.calls[0]
    expect(path).toBe('workbench-project')
    expect(body).toMatchObject({ question_id: 'bess_at_site', name: 'My battery', project_name: 'My battery',
      intake: { site: { zone: 'DE', connection_mw: 2 }, load: { source: 'upload', csv_text: 'x', filename: 'meter.csv' } } })
    await waitFor(() => expect(useDecisionStore.getState().active).toEqual({ project: 'My battery', studyId: study.study_id }))
  })
})
