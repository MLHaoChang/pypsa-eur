// The `decision` SlidePanel (plan S8): the guided flow for one decision study.
//
// * Availability (review v2 BC-6, OPEN-ITEMS 1): in auth (multi-user) mode the
//   backend refuses every study route, and in local mode without
//   `PYPSAGUI_DECISION_STUDIES=1`; the panel then says why and offers nothing
//   that would fail — it never crashes.
// * Entry state: an incomplete study opens on its intake, a complete one on
//   the hub (`decisionModel.entryState`); a new study is answered in draft and
//   created from "Check your answers".
// * Maturity is read from ONE source, the ledger payload.
// * Polling: the run and tornado status routes only; findings once per change
//   (`decisionQueries.ts`).
// * The Expert view opens the chosen OPTION FORK (never the base project),
//   after a plain warning that editing it there changes the study's case.
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { decisionStudiesApi as api, studyError, studyUrls, type StudyError, type StudyIntake } from '../../api/decisionStudies'
import { useAuthMode } from '../../auth/AuthModeProvider'
import { useUIStore } from '../../store/uiStore'
import { switchToProject } from '../../utils/projectActions'
import {
  EXPERT_REFUSAL, EXPERT_REFUSAL_FALLBACK, EXPERT_WARNING, OPTION_LABELS, UI_LABELS, VIEW_LABELS, errorCopy,
  type DecisionView,
} from '../../utils/decisionVocabulary'
import { dq, invalidateDerived, studyKey } from './decisionQueries'
import { entryState, expertTarget, maturityLabel, pollInterval, runsPack, sectionStatuses } from './decisionModel'
import { useDecisionStore, type Draft, type StudyRef } from './decisionStore'
import { Banner, Button, Card, Refusal } from './DecisionUi'
import DecisionHub from './DecisionHub'
import Intake from './Intake'
import Options from './Options'
import TariffStep from './TariffStep'
import FinanceStep from './FinanceStep'
import LedgerReview, { type LedgerEditOut } from './LedgerReview'
import RunStep from './RunStep'
import Verdict from './Verdict'
import WhyHow from './WhyHow'
import Robust from './Robust'
import ReportStep from './ReportStep'
import { chosenTariff } from './TariffStep'

export function Unavailable({ code, message }: { code: string; message?: string }) {
  const copy = errorCopy(code)
  return (
    <div className="p-6 max-w-2xl" data-testid="decision-unavailable">
      <Banner tone="info" title={copy.title}><span>{copy.action}</span>
        {message && <span className="text-[11px] text-muted">{message}</span>}
      </Banner>
    </div>
  )
}

function StudyPicker({ project }: { project: string | null }) {
  const openStudy = useDecisionStore(s => s.openStudy)
  const q = useQuery({
    queryKey: ['decision', project ?? '', 'list'],
    queryFn: () => api.list(project!),
    enabled: !!project,
    retry: false,
  })
  if (!project) {
    return <div className="p-6"><Banner title="No project is open.">Open a decision study’s project, or start a study from New project → Decision study.</Banner></div>
  }
  if (q.error) {
    const err = studyError(q.error)
    if (err.code?.startsWith('decision_studies_')) return <Unavailable code={err.code} message={err.message} />
    return <div className="p-6"><Refusal error={err} /></div>
  }
  const studies = q.data ?? []
  return (
    <div className="p-6 max-w-2xl flex flex-col gap-3">
      {q.isLoading ? <p className="text-muted">Loading…</p> : studies.length === 0 ? (
        <Banner title="This project has no decision study.">
          <span>Start one from New project → Decision study; it creates its own project and changes none of yours.</span>
        </Banner>
      ) : (
        <Card title="Decision studies in this project">
          <ul className="flex flex-col gap-1">
            {studies.map(s => (
              <li key={s.study_id}>
                <button type="button" className="text-accent hover:underline" onClick={() => openStudy({ project, studyId: s.study_id })}>{s.name}</button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function DraftView({ draft }: { draft: Draft }) {
  const qc = useQueryClient()
  const openStudy = useDecisionStore(s => s.openStudy)
  const library = useQuery(dq.library(draft.pathProject))
  const [error, setError] = useState<StudyError | null>(null)
  const create = useMutation({
    mutationFn: ({ intake, names }: { intake: StudyIntake; names: { name: string; baseName: string } }) =>
      api.create(draft.pathProject, { question_id: 'bess_at_site', name: names.name, project_name: names.baseName, intake }),
    onSuccess: (s, vars) => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success(`Created the decision study “${s.name}”`)
      openStudy({ project: s.base_project_name ?? vars.names.baseName, studyId: s.study_id }, 'hub')
    },
    onError: e => setError(studyError(e)),
  })
  const libErr = library.error ? studyError(library.error) : null
  if (libErr?.code?.startsWith('decision_studies_')) return <Unavailable code={libErr.code} message={libErr.message} />
  return (
    <div className="p-5 overflow-y-auto h-full">
      <h2 className="text-[14px] font-semibold mb-3">New decision study: Do I need a battery at my site?</h2>
      <Intake mode="draft" project={draft.pathProject} initial={draft.intake ?? {}} library={library.data ?? null}
        names={{ name: draft.name, baseName: draft.baseName }}
        onDraftChange={intake => useDecisionStore.getState().updateDraft(intake)}
        saving={create.isPending} error={error}
        onCreate={(intake, names) => { setError(null); create.mutate({ intake, names }) }} />
    </div>
  )
}

const NAV: DecisionView[] = ['hub', 'intake', 'options', 'tariff', 'finance', 'ledger', 'run', 'verdict', 'why', 'robust', 'report']

function StudyView({ at, pollMs }: { at: StudyRef; pollMs: number }) {
  const { project, studyId } = at
  const qc = useQueryClient()
  const view = useDecisionStore(s => s.view)
  const setView = useDecisionStore(s => s.setView)
  const setWatch = useDecisionStore(s => s.setWatch)
  const watch = useDecisionStore(s => s.watch)

  const study = useQuery(dq.study(project, studyId))
  const ledger = useQuery(dq.ledger(project, studyId))
  const library = useQuery(dq.library(project))
  const run = useQuery({ ...dq.run(project, studyId), refetchInterval: q => pollInterval(q.state.data ?? undefined, pollMs) })
  const tornado = useQuery({ ...dq.tornado(project, studyId), refetchInterval: q => pollInterval(q.state.data ?? undefined, pollMs) })
  const runDone = !!run.data && run.data.status !== 'running'
  const findings = useQuery({ ...dq.findings(project, studyId), enabled: runDone })
  const report = useQuery({ ...dq.report(project, studyId), enabled: runDone && (view === 'report' || view === 'hub') })
  const f = findings.data?.data ?? null
  const caseOption = f?.verdict.option_id ?? f?.value_streams_option ?? null
  const optionCase = useQuery({ ...dq.optionCase(project, studyId, caseOption ?? ''), enabled: !!caseOption && view === 'why' })
  const s = study.data
  const preview = useQuery({
    queryKey: [...studyKey(project, studyId), 'preview', JSON.stringify(s?.intake ?? {})],
    queryFn: () => api.preview(project, s!.intake),
    enabled: !!s && view === 'tariff',
    staleTime: Infinity,
    retry: false,
  })

  // A run seen running is watched, so its end opens the verdict (DecisionRunWatcher).
  useEffect(() => {
    if (run.data?.status === 'running' && !watch) setWatch({ project, studyId })
  }, [run.data?.status, watch, setWatch, project, studyId])
  // The tornado's end re-reads the findings and the report once.
  const [tornadoWasRunning, setTornadoWasRunning] = useState(false)
  useEffect(() => {
    if (tornado.data?.status === 'running') setTornadoWasRunning(true)
    else if (tornadoWasRunning && tornado.data) { setTornadoWasRunning(false); invalidateDerived(qc, project, studyId) }
  }, [tornado.data, tornadoWasRunning, qc, project, studyId])

  const [stepError, setStepError] = useState<StudyError | null>(null)
  const afterEdit = () => {
    void qc.invalidateQueries({ queryKey: [...studyKey(project, studyId), 'ledger'] })
    invalidateDerived(qc, project, studyId)
  }
  const patch = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) => api.patchStep(project, studyId, key, value),
    onSuccess: data => { setStepError(null); qc.setQueryData(dq.study(project, studyId).queryKey, data); afterEdit(); toast.success('Saved') },
    onError: e => setStepError(studyError(e)),
  })
  const [ledgerError, setLedgerError] = useState<StudyError | null>(null)
  const putLedger = useMutation({
    mutationFn: (body: { rows?: LedgerEditOut[]; reset?: string[] }) => api.putLedger(project, studyId, body),
    onSuccess: data => {
      setLedgerError(null)
      qc.setQueryData(dq.ledger(project, studyId).queryKey, data)
      invalidateDerived(qc, project, studyId)
      void qc.invalidateQueries({ queryKey: [...studyKey(project, studyId), 'study'] })
    },
    onError: e => setLedgerError(studyError(e)),
  })
  const [runError, setRunError] = useState<StudyError | null>(null)
  const startRun = useMutation({
    mutationFn: () => api.startRun(project, studyId, {}),
    onSuccess: rec => {
      setRunError(null)
      qc.setQueryData(dq.run(project, studyId).queryKey, rec)
      setWatch({ project, studyId })
      void qc.invalidateQueries({ queryKey: dq.run(project, studyId).queryKey })
    },
    onError: e => setRunError(studyError(e)),
  })
  const abortRun = useMutation({
    mutationFn: () => api.abortRun(project, studyId),
    onSettled: () => void qc.invalidateQueries({ queryKey: dq.run(project, studyId).queryKey }),
  })
  const [tornadoError, setTornadoError] = useState<StudyError | null>(null)
  const startTornado = useMutation({
    mutationFn: () => api.startTornado(project, studyId, {}),
    onSuccess: rec => {
      setTornadoError(null)
      qc.setQueryData(dq.tornado(project, studyId).queryKey, rec)
      void qc.invalidateQueries({ queryKey: dq.tornado(project, studyId).queryKey })
    },
    onError: e => setTornadoError(studyError(e)),
  })
  const abortTornado = useMutation({
    mutationFn: () => api.abortTornado(project, studyId),
    onSettled: () => void qc.invalidateQueries({ queryKey: dq.tornado(project, studyId).queryKey }),
  })
  const [assembleError, setAssembleError] = useState<StudyError | null>(null)
  const assemble = useMutation({
    mutationFn: () => api.assembleReport(project, studyId),
    onSuccess: data => { setAssembleError(null); qc.setQueryData(dq.report(project, studyId).queryKey, { data, error: null }) },
    onError: e => setAssembleError(studyError(e)),
  })

  // ── the Expert view: the chosen option fork, after the warning ─────────
  const [expertFor, setExpertFor] = useState<string | null>(null)
  const [expertMsg, setExpertMsg] = useState<string | null>(null)
  const openExpert = async () => {
    if (!f || !s) return
    const target = expertTarget(f, s, expertFor)
    setExpertFor(null)
    if (!target) return
    const res = await switchToProject(target.projectRef, qc)
    if (res.status === 'switched' || res.status === 'noop') useUIStore.getState().setSlidePanel(null)
    else setExpertMsg(EXPERT_REFUSAL[res.status] ?? EXPERT_REFUSAL_FALLBACK)
  }

  if (study.error) {
    const err = studyError(study.error)
    if (err.code?.startsWith('decision_studies_')) return <Unavailable code={err.code} message={err.message} />
    return <div className="p-6"><Refusal error={err} /></div>
  }
  if (!s) return <p className="p-6 text-muted">Loading the study…</p>

  const current: DecisionView = view ?? (entryState(s) === 'intake' ? 'intake' : 'hub')
  const statuses = sectionStatuses({
    study: s, ledger: ledger.data ?? null, run: run.data ?? null,
    findings: { data: f, errorCode: findings.data?.error?.code ?? null },
    report: { data: report.data?.data ?? null, errorCode: report.data?.error?.code ?? null },
  })
  const findingsRefusal = findings.data?.error ?? null
  const defaultTarget = f ? expertTarget(f, s) : null
  const rows = ledger.data?.ledger.rows ?? []
  const tariff = chosenTariff(library.data, s.intake)

  const needFindings = (node: React.ReactNode) => {
    if (!run.data) return <Refusal error={{ status: 404, code: 'study_never_run', message: '' }} />
    if (run.data.status === 'running') return <Banner title="The study is running.">The findings follow when it finishes.</Banner>
    if (findingsRefusal) return <Refusal error={findingsRefusal} testId="findings-refusal" />
    if (!f) return <p className="text-muted">Reading the findings…</p>
    return node
  }

  return (
    <div className="h-full flex flex-col" data-testid="decision-study">
      <div className="flex flex-wrap items-center gap-2 px-5 py-2 border-b border-border bg-bg-2">
        <strong className="text-[13px]">{s.name}</strong>
        <span data-testid="decision-maturity" className="text-[11px] text-muted">Maturity: {maturityLabel(ledger.data?.maturity)}</span>
        <span className="flex-1" />
        <Button onClick={() => defaultTarget && setExpertFor(defaultTarget.optionId)} disabled={!defaultTarget}
          title={defaultTarget ? `Open ${OPTION_LABELS[defaultTarget.optionId] ?? defaultTarget.optionId} in the workbench` : 'Available once the verdict names an option'}
          testId="expert-view">
          {UI_LABELS.expertView}
        </Button>
      </div>
      <nav aria-label="Decision study sections" className="flex flex-wrap gap-1 px-5 py-1.5 border-b border-border">
        {NAV.filter(v => runsPack(s) || !['intake', 'options', 'tariff', 'run', 'verdict', 'why', 'robust', 'report'].includes(v)).map(v => (
          <button key={v} type="button" aria-current={v === current ? 'page' : undefined} onClick={() => setView(v)}
            className={`px-2 h-[24px] rounded text-[11px] ${v === current ? 'bg-panel text-text font-medium' : 'text-muted hover:text-text'}`}>
            {VIEW_LABELS[v]}
          </button>
        ))}
      </nav>
      {expertFor && (
        <div className="px-5 pt-3">
          <Banner tone="warn" testId="expert-warning" title={`Open ${OPTION_LABELS[expertFor] ?? expertFor} in the Expert view?`}>
            <span>{EXPERT_WARNING}</span>
            <span className="flex gap-2 pt-1">
              <Button kind="primary" onClick={() => void openExpert()}>Open anyway</Button>
              <Button onClick={() => setExpertFor(null)}>Cancel</Button>
            </span>
          </Banner>
        </div>
      )}
      {expertMsg && <div className="px-5 pt-3"><Banner tone="warn" title={expertMsg} /></div>}
      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {!runsPack(s) && current !== 'hub' && current !== 'ledger' && current !== 'finance' ? (
          <Refusal error={{ status: 409, code: 'study_not_runnable', message: '' }} />
        ) : current === 'hub' ? (
          <DecisionHub study={s} ledger={ledger.data ?? null} statuses={statuses} onOpen={setView} />
        ) : current === 'intake' ? (
          <Intake mode="edit" project={project} initial={s.intake} library={library.data ?? null}
            hasRun={!!run.data} saving={patch.isPending} error={stepError}
            initialStep={entryState(s) === 'intake' ? 'site' : 'check'}
            onSaveStep={(key, value) => patch.mutate({ key, value })} />
        ) : current === 'options' ? (
          <Options intake={s.intake} ledgerRows={rows} hasRun={!!run.data} saving={patch.isPending} error={stepError}
            onPv={pv => patch.mutate({ key: 'pv', value: pv })} />
        ) : current === 'tariff' ? (
          <TariffStep library={library.data ?? null} intake={s.intake} saving={patch.isPending} error={stepError}
            preview={preview.data ?? null} previewError={preview.error ? studyError(preview.error) : null}
            onChoose={id => patch.mutate({ key: 'tariff', value: { tariff_id: id } })} />
        ) : current === 'finance' ? (
          <FinanceStep study={s} ledgerRows={rows} />
        ) : current === 'ledger' ? (
          ledger.data ? (
            <LedgerReview payload={ledger.data} intake={s.intake} saving={putLedger.isPending} error={ledgerError}
              csvUrl={studyUrls.ledgerCsv(project, studyId)}
              onSave={edits => putLedger.mutate({ rows: edits })}
              onReset={keys => putLedger.mutate({ reset: keys })} />
          ) : ledger.error ? <Refusal error={studyError(ledger.error)} /> : <p className="text-muted">Loading…</p>
        ) : current === 'run' ? (
          <RunStep study={s} run={run.data ?? null} error={runError} busy={startRun.isPending}
            onStart={() => startRun.mutate()} onAbort={() => abortRun.mutate()} />
        ) : current === 'verdict' ? (
          needFindings(f && <Verdict findings={f} maturity={ledger.data?.maturity ?? null} ledgerRows={rows}
            tariffHelp={tariff?.honesty_help} />)
        ) : current === 'why' ? (
          needFindings(f && <WhyHow findings={f} optionCase={optionCase.data?.data ?? null} onExpert={setExpertFor} />)
        ) : current === 'robust' ? (
          needFindings(f && <Robust findings={f} tornado={tornado.data ?? null} error={tornadoError} ledgerRows={rows}
            busy={startTornado.isPending} onStart={() => startTornado.mutate()} onAbort={() => abortTornado.mutate()} />)
        ) : (
          <ReportStep report={report.data?.data ?? null} error={report.data?.error ?? null} assembleError={assembleError}
            run={run.data ?? null} tornado={tornado.data ?? null} busy={assemble.isPending}
            onAssemble={() => assemble.mutate()}
            urls={{ html: studyUrls.reportHtml(project, studyId), docx: studyUrls.reportDocx(project, studyId), xlsx: studyUrls.reportXlsx(project, studyId) }} />
        )}
      </div>
    </div>
  )
}

export default function DecisionPanel({ pollMs = 2000 }: { pollMs?: number }) {
  const { authEnabled } = useAuthMode()
  const active = useDecisionStore(s => s.active)
  const draft = useDecisionStore(s => s.draft)
  const currentProject = useUIStore(s => s.currentProject)
  // BC-6: the backend refuses every study route in auth mode; say so, call nothing.
  if (authEnabled) return <Unavailable code="decision_studies_unavailable" />
  if (draft) return <DraftView draft={draft} />
  if (active) return <StudyView at={active} pollMs={pollMs} />
  return <StudyPicker project={currentProject} />
}
