// Gate S8 binding conditions and recommendations (scratchpad gate-s8.md):
// BC-S8-1 the verdict sentence reads exactly (no doubled units); BC-S8-2 a
// bess_pv verdict shows the option's total NPV and the battery-only answer;
// BC-S8-3 plain KPI labels, the by-construction note beside the NPV and the
// glossary; BC-S8-4 edit-mode steps never show unsaved answers as stored;
// BC-S8-5 a draft's load file stays in the browser until the study exists;
// BC-S8-7 a finished re-run or tornado replaces the cached findings; plus the
// case's own assumptions, the tornado values as text, a watched run that
// ends during a draft, and the budget sentence counting the current options.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { Findings, RunRecord } from '../../api/decisionStudies'
import { findings, findingsPre, findingsPv, ledger, library, optionCase, previewUpload, run, study } from './__fixtures__/payloads'
import { GLOSSARY, HELP, KPI_LABELS } from '../../utils/decisionVocabulary'

const authMode = vi.hoisted(() => ({ authEnabled: false }))
vi.mock('../../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../../utils/projectActions', () => ({ switchToProject: vi.fn() }))
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

import Verdict from './Verdict'
import WhyHow from './WhyHow'
import Robust from './Robust'
import RunStep from './RunStep'
import Intake from './Intake'
import DecisionPanel from './DecisionPanel'
import DecisionRunWatcher from './DecisionRunWatcher'
import { useDecisionStore } from './decisionStore'
import { dq } from './decisionQueries'
import { useUIStore } from '../../store/uiStore'

function axErr(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } })
}
const REF = { project: 'site-base', studyId: study.study_id }

beforeEach(() => {
  authMode.authEnabled = false
  for (const f of Object.values(api)) f.mockReset()
  uploadFile.mockReset(); toastMock.mockClear(); toastMock.success.mockClear()
  api.get.mockResolvedValue(study)
  api.ledger.mockResolvedValue(ledger)
  api.library.mockResolvedValue(library)
  api.run.mockResolvedValue(run)
  api.tornado.mockRejectedValue(axErr(404, { error_kind: 'tornado_never_run', message: 'x' }))
  api.report.mockRejectedValue(axErr(404, { error_kind: 'report_never_assembled', message: 'x' }))
  api.optionCase.mockRejectedValue(axErr(404, { error_kind: 'option_not_solved', message: 'x' }))
  api.preview.mockResolvedValue(previewUpload)
  useDecisionStore.setState({ active: null, draft: null, view: null, watch: null })
  useUIStore.setState({ currentProject: 'workbench-project', activeSlidePanel: 'decision' })
})
afterEach(() => cleanup())

const show = (f: Findings) => render(<Verdict findings={f} maturity={ledger.maturity} ledgerRows={ledger.ledger.rows} />)

describe('BC-S8-1: the verdict sentence reads exactly', () => {
  it('battery only', () => {
    show(findings)
    expect(screen.getByTestId('verdict-sentence').textContent).toBe(
      'Recommended: a battery of 0.3 MW with 1 hour of storage has a positive battery NPV of EUR 3.53 M '
      + 'at the centre and at every tornado bound.')
  })

  it('battery with PV', () => {
    show(findingsPv)
    expect(screen.getByTestId('verdict-sentence').textContent).toBe(
      'Recommended: a battery of 0.3 MW with 2 hours of storage has a positive battery NPV of EUR 3.47 M '
      + 'at the centre and at every tornado bound, measured against the same PV of 0.5 MW alone.')
  })
})

describe('BC-S8-2: a bess_pv verdict shows the total NPV and the battery-only answer', () => {
  it('says no battery-only option pays on its own when none does', () => {
    show(findingsPv)
    const box = screen.getByTestId('verdict-pv')
    expect(box.textContent).toContain('EUR 3.01 M')
    expect(box.textContent).toContain('PV included')
    expect(box.textContent).toContain('EUR of 2020')
    expect(box.textContent).toContain('no battery-only option sized a battery above zero')
  })

  it('names the best battery-only option with its size and battery NPV', () => {
    const f: Findings = { ...findingsPv, battery_attribution: findingsPv.battery_attribution.map(a => a.option_id === 'bess_2h'
      ? { ...a, status: 'ok', battery_npv: 1_200_000, battery_p_nom_mw: 0.4, unavailable: {} } : a) }
    show(f)
    const box = screen.getByTestId('verdict-pv')
    expect(box.textContent).toContain('the best battery-only option is a battery of 0.4 MW with 2 hours of storage')
    expect(box.textContent).toContain('EUR 1.2 M')
  })

  it('shows nothing of the kind for a battery-only verdict', () => {
    show(findings)
    expect(screen.queryByTestId('verdict-pv')).toBeNull()
  })
})

describe('BC-S8-3: plain labels, the note beside the NPV, the glossary', () => {
  it('labels the KPIs in plain words with the technical term as a subtitle', () => {
    show(findings)
    const [npv, power, payback] = screen.getAllByTestId('verdict-kpi')
    expect(npv.textContent).toContain(KPI_LABELS.battery_npv.label)
    expect(npv.textContent).toContain(KPI_LABELS.battery_npv.technical)
    expect(power.textContent).toContain(KPI_LABELS.battery_p_nom_mw.label)
    expect(payback.textContent).toContain(KPI_LABELS.battery_payback_simple.label)
  })

  it('puts the by-construction note beside the NPV figure, with the NPV explained', () => {
    show(findings)
    const npv = screen.getAllByTestId('verdict-kpi')[0]
    expect(within(npv).getByTestId('kpi-by-construction').textContent).toBe(HELP.npv_nonnegative_at_optimum_by_construction)
    expect(npv.textContent).toContain(GLOSSARY.npv.text)
    expect(screen.getAllByTestId('verdict-kpi')[2].textContent).toContain(GLOSSARY.payback.text)
  })

  it('explains the tornado bounds under the sentence, and PV-only for a bess_pv verdict', () => {
    show(findingsPv)
    expect(screen.getByTestId('verdict-bounds').textContent).toContain(GLOSSARY.tornado.text)
    expect(screen.getByTestId('verdict-pv').textContent).toContain(GLOSSARY.pv_only.text)
  })

  it('shows the IRR with its explanation and the by-construction bound', () => {
    render(<WhyHow findings={findings} optionCase={optionCase} onExpert={vi.fn()} />)
    const cf = screen.getByTestId('cash-flow')
    expect(cf.textContent).toContain(GLOSSARY.irr.text)
    expect(cf.textContent).toContain('IRR 261.7 %')
  })
})

describe('BC-S8-4: edit mode never shows unsaved answers as stored (gate probe P3)', () => {
  it('Next saves the step, and the summary shows the stored value with the unsaved change marked', () => {
    const onSaveStep = vi.fn()
    const { rerender } = render(<Intake mode="edit" project="p" initial={study.intake} library={library} onSaveStep={onSaveStep} saving={false} error={null} />)
    fireEvent.click(screen.getByRole('button', { name: /What exists today/ }))
    fireEvent.change(screen.getByLabelText('Grid connection limit'), { target: { value: '9' } })
    for (let i = 0; i < 5; i++) { const n = screen.queryByRole('button', { name: 'Next' }); if (n) fireEvent.click(n) }
    expect(onSaveStep).toHaveBeenCalledWith('site', { ...study.intake.site, connection_mw: 9 })
    const summary = screen.getByTestId('intake-summary')
    expect(summary.textContent).toContain('2 MW')
    expect(summary.textContent).not.toMatch(/Grid connection limit9 MW/)
    expect(screen.getByTestId('intake-unsaved').textContent).toContain('9 MW')
    // Once the save lands (the stored intake now says 9), nothing is marked unsaved.
    rerender(<Intake mode="edit" project="p" initial={{ ...study.intake, site: { ...study.intake.site, connection_mw: 9 } }}
      library={library} onSaveStep={onSaveStep} saving={false} error={null} />)
    expect(screen.queryByTestId('intake-unsaved')).toBeNull()
    expect(screen.getByTestId('intake-summary').textContent).toContain('9 MW')
  })

  it('the step rail saves the step it leaves', () => {
    const onSaveStep = vi.fn()
    render(<Intake mode="edit" project="p" initial={study.intake} library={library} onSaveStep={onSaveStep} saving={false} error={null} />)
    fireEvent.change(screen.getByLabelText('Country or grid zone'), { target: { value: 'NL' } })
    fireEvent.click(screen.getByRole('button', { name: /Your consumption/ }))
    expect(onSaveStep).toHaveBeenCalledWith('site', { ...study.intake.site, zone: 'NL' })
  })

  it('an untouched step is not re-saved on Next', () => {
    const onSaveStep = vi.fn()
    render(<Intake mode="edit" project="p" initial={study.intake} library={library} onSaveStep={onSaveStep} saving={false} error={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(onSaveStep).not.toHaveBeenCalled()
  })
})

describe('BC-S8-5: a draft load file stays in the browser until the study is created', () => {
  it('is previewed and created from its text, never uploaded into a user project', async () => {
    const text = 'timestamp,load (kW)\n2025-01-01 00:00,1000\n'
    api.create.mockResolvedValue({ ...study, base_project_name: 'Mine' })
    useDecisionStore.setState({ draft: { pathProject: 'workbench-project', name: 'Mine', baseName: '' }, view: 'intake' })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    fireEvent.change(await screen.findByLabelText('Country or grid zone'), { target: { value: 'DE' } })
    fireEvent.click(screen.getByRole('button', { name: /What exists today/ }))
    fireEvent.change(screen.getByLabelText('Grid connection limit'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: /Your consumption/ }))
    fireEvent.click(screen.getByLabelText(/Upload your metered load/))
    fireEvent.change(screen.getByLabelText('Load file'), { target: { files: [new File([text], 'meter.csv')] } })
    await screen.findByTestId('load-check')
    expect(uploadFile).not.toHaveBeenCalled()
    expect(api.preview).toHaveBeenCalledWith('workbench-project',
      expect.objectContaining({ load: expect.objectContaining({ source: 'upload', csv_text: text, filename: 'meter.csv' }) }))
    fireEvent.click(screen.getByRole('button', { name: /Check your answers/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Create the study' }))
    await waitFor(() => expect(api.create).toHaveBeenCalled())
    expect(api.create.mock.calls[0][1].intake.load).toMatchObject({ source: 'upload', csv_text: text, filename: 'meter.csv' })
    expect(uploadFile).not.toHaveBeenCalled()
  })

  it('keeps the draft answers when the panel is closed and reopened', async () => {
    useDecisionStore.setState({ draft: { pathProject: 'workbench-project', name: 'Mine', baseName: '' }, view: 'intake' })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const r = render(<QueryClientProvider client={qc}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    fireEvent.change(await screen.findByLabelText('Country or grid zone'), { target: { value: 'AT' } })
    r.unmount()
    render(<QueryClientProvider client={qc}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    expect(((await screen.findByLabelText('Country or grid zone')) as HTMLInputElement).value).toBe('AT')
  })
})

describe('BC-S8-7: a finished run or tornado replaces the cached findings', () => {
  const notRec: Findings = { ...findings, verdict: { ...findings.verdict, class: 'not_recommended', sentence: 'Old verdict.' } }

  it('a re-run with findings already cached shows and toasts the NEW verdict', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(dq.findings(REF.project, REF.studyId).queryKey, { data: notRec, error: null })
    const running = { ...(run as RunRecord), status: 'running' as const }
    api.run.mockResolvedValueOnce(running).mockResolvedValueOnce(running).mockResolvedValue(run)
    api.findings.mockResolvedValue(findings)
    useDecisionStore.setState({ active: REF, view: 'run', watch: REF })
    render(<QueryClientProvider client={qc}><DecisionRunWatcher pollMs={20} /><DecisionPanel pollMs={20} /></QueryClientProvider>)
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled(), { timeout: 3000 })
    expect(toastMock.success).toHaveBeenCalledWith('Decision study finished: Recommended')
    await waitFor(() => expect(screen.getByTestId('verdict-class').textContent).toBe('Recommended'))
  })

  it('the end of a tornado re-reads the findings', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(dq.findings(REF.project, REF.studyId).queryKey, { data: findingsPre, error: null })
    const t = { status: 'running', kind: 'tornado', current: 'discount_rate', solves_charged: 0, finished_at: null }
    api.tornado.mockReset()
    api.tornado.mockResolvedValueOnce(t).mockResolvedValueOnce(t).mockResolvedValue({ ...t, status: 'done', current: null, finished_at: 2e9 })
    api.findings.mockResolvedValue(findings)
    useDecisionStore.setState({ active: REF, view: 'verdict' })
    render(<QueryClientProvider client={qc}><DecisionPanel pollMs={20} /></QueryClientProvider>)
    expect((await screen.findByTestId('verdict-class')).textContent).toBe('Verdict not established')
    await waitFor(() => expect(screen.getByTestId('verdict-class').textContent).toBe('Recommended'), { timeout: 3000 })
    expect(api.findings).toHaveBeenCalledTimes(1)
  })
})

describe('recommendations', () => {
  it('a watched run that ends during a draft keeps the draft and does not jump away', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const running = { ...(run as RunRecord), status: 'running' as const }
    api.run.mockResolvedValueOnce(running).mockResolvedValue(run)
    api.findings.mockResolvedValue(findings)
    api.preview.mockReturnValue(new Promise(() => {}))
    useDecisionStore.setState({ draft: { pathProject: 'workbench-project', name: 'B', baseName: '' }, view: 'intake', watch: REF })
    render(<QueryClientProvider client={qc}><DecisionRunWatcher pollMs={20} /><DecisionPanel pollMs={20} /></QueryClientProvider>)
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled(), { timeout: 3000 })
    expect(useDecisionStore.getState().draft).not.toBeNull()
    expect(useDecisionStore.getState().active).toBeNull()
    expect(useDecisionStore.getState().view).toBe('intake')
  })

  it('lists what the option’s case assumes, in words', () => {
    render(<WhyHow findings={findings} optionCase={optionCase} onExpert={vi.fn()} />)
    const notes = screen.getByTestId('case-notes')
    for (const code of ['synthetic_load_profile', 'tariff_illustrative', 'single_year_extrapolated', 'no_degradation']) {
      expect(notes.querySelector(`[data-code="${code}"]`)!.textContent).toContain(HELP[code])
    }
  })

  it('prints each tornado bar’s low and high NPV as text', () => {
    render(<Robust findings={findings} tornado={null} error={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false} />)
    const bar = screen.getAllByTestId('tornado-bar').find(b => b.getAttribute('data-key') === 'discount_rate')!
    expect(within(bar).getByTestId('tornado-values').textContent).toBe('NPV at the low value: EUR 4.34 M · at the high value: EUR 2.93 M')
  })

  it('counts the options the NEXT run solves, not the last run’s', () => {
    render(<RunStep study={{ ...study, intake: { ...study.intake, pv: { enabled: true } } }} run={run as RunRecord}
      error={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false} />)
    expect(screen.getByTestId('budget-sentence').textContent).toContain('5 solves')
  })
})
