// GC-09: the grid-compliance workflow in Studies › Campus electrical.
// The 25 TRGC deliverables by phase: status chip, next step, what blocks
// submission, and per deliverable the data list, the data freeze, the TSO
// comment log and the report pack. Statuses are what the engineer declares;
// the backend guards submission (prerequisites judged, a fresh freeze).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  GRID_COMPLIANCE_KEY, gridComplianceApi,
  type ComplianceState, type DataItem, type Deliverable, type FreezeState, type GcStatus,
  type TsoComment,
} from '../api/gridCompliance'
import { errorText } from '../api/campusElectrical'
import { withReturnFrom } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'
import { Btn, PageSection, Tag } from './PageKit'

type Tone = 'accent' | 'ok' | 'warn' | 'err' | 'neutral'

export const STATUS_LABEL: Record<GcStatus, string> = {
  not_started: 'Not started',
  data: 'Data',
  exported: 'Exported',
  results_in: 'Results in',
  judged: 'Judged',
  submitted: 'Submitted',
  tso_comments: 'TSO comments',
  accepted: 'Accepted',
}

const STATUS_TONE: Record<GcStatus, Tone> = {
  not_started: 'neutral',
  data: 'neutral',
  exported: 'accent',
  results_in: 'accent',
  judged: 'accent',
  submitted: 'accent',
  tso_comments: 'warn',
  accepted: 'ok',
}

export const FREEZE_LABEL: Record<FreezeState, string> = {
  none: 'Not frozen',
  fresh: 'Frozen',
  stale: 'Out of date',
  refreeze_needed: 'Refreeze needed',
  unreadable: 'Inputs unreadable',
  submitted: 'As submitted',
}

const FREEZE_TONE: Record<FreezeState, Tone> = {
  none: 'neutral', fresh: 'ok', stale: 'warn', refreeze_needed: 'warn', unreadable: 'err', submitted: 'ok',
}

const COMMENT_LABEL: Record<TsoComment['status'], string> = {
  open: 'Open', responded: 'Responded', closed: 'Closed',
}

const SUBMITTED_RANK = 5 // index of 'submitted' in the backend's status ladder

const today = () => new Date().toISOString().slice(0, 10)

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

const inputCls = 'h-6 px-1.5 rounded border border-border-2 bg-bg text-[11px] text-text '
  + 'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]'
const linkCls = 'text-accent underline focus:outline-none focus-visible:ring-2 '
  + 'focus-visible:ring-[var(--color-focus)]'
const rowBtnCls = 'w-full grid grid-cols-[44px_1fr_auto] items-center gap-2 px-3 py-1.5 text-left '
  + 'hover:bg-panel focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]'

export default function GridComplianceSection({ name }: { name: string }) {
  const q = useQuery({
    queryKey: GRID_COMPLIANCE_KEY(name),
    queryFn: () => gridComplianceApi.state(name),
    retry: false,
  })
  const [open, setOpen] = useState<string | null>(null)

  const data = q.data
  const accepted = data?.deliverables.filter(d => d.status === 'accepted').length ?? 0
  const started = data?.deliverables.filter(d => d.status !== 'not_started').length ?? 0
  return (
    <PageSection
      title="Grid compliance (TRGC)"
      count={data ? `${accepted} of ${data.deliverables.length} accepted` : undefined}
      hint="The grid-connection study chain: data, freeze, external study, judgment, TSO submission."
      panelSection="grid_compliance"
    >
      {q.isPending && <p className="text-[11px] text-muted px-4 py-3">Loading the deliverables…</p>}
      {q.isError && (
        <p role="alert" className="text-[12px] text-danger px-4 py-3">{errorText(q.error)}</p>
      )}
      {data && (
        <div className="px-3 py-2 border-b border-border text-[10.5px] text-muted space-y-1">
          <p>
            {started === 0
              ? 'Not started: no deliverable has moved past “Not started” yet.'
              : `Started: ${started} of ${data.deliverables.length} deliverables have moved past “Not started”; `
                + `${accepted} accepted by the TSO.`}
          </p>
          <ChipKey />
        </div>
      )}
      {data && <Phases name={name} state={data} open={open} setOpen={setOpen} />}
    </PageSection>
  )
}

/** The visible key of every chip style the section uses (UX #1199). */
function ChipKey() {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1" aria-label="Chip key">
      <span>Key:</span>
      <Tag tone="neutral">Not started</Tag><span>no work yet or collecting data</span>
      <Tag tone="accent">Judged</Tag><span>in progress with you</span>
      <Tag tone="warn">TSO comments</Tag><span>needs your answer</span>
      <Tag tone="ok">Accepted</Tag><span>done</span>
      <Tag tone="ok">Frozen</Tag><span>inputs unchanged since the freeze</span>
      <Tag tone="warn">Out of date</Tag><span>inputs changed after the freeze</span>
      <Tag tone="err">Inputs unreadable</Tag><span>the freeze cannot be checked</span>
    </p>
  )
}

function Phases({ name, state, open, setOpen }: {
  name: string; state: ComplianceState; open: string | null; setOpen: (c: string | null) => void
}) {
  return (
    <div className="divide-y divide-border">
      {Object.entries(state.phases).map(([phase, title]) => {
        const rows = state.deliverables.filter(d => String(d.phase) === phase)
        return (
          <div key={phase} role="group" aria-label={`Phase ${phase}: ${title}`}>
            <h4 className="px-3 py-1.5 bg-bg-2 text-[10px] font-semibold uppercase tracking-wide text-muted">
              Phase {phase} · {title}
            </h4>
            <ul>
              {rows.map(d => (
                <DeliverableRow key={d.code} name={name} d={d} statuses={state.statuses}
                  hasCampusInput={state.has_campus_input}
                  open={open === d.code} onToggle={() => setOpen(open === d.code ? null : d.code)} />
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}

export function blockedMessage(d: Deliverable, statuses: GcStatus[]): string | null {
  if (!d.blocked_by.length || statuses.indexOf(d.status) >= SUBMITTED_RANK) return null
  return `Submission waits for ${d.blocked_by.map(c => `TRGC ${c}`).join(', ')} to be judged`
}

function DeliverableRow({ name, d, statuses, hasCampusInput, open, onToggle }: {
  name: string; d: Deliverable; statuses: GcStatus[]; hasCampusInput: boolean
  open: boolean; onToggle: () => void
}) {
  const blocked = blockedMessage(d, statuses)
  const detailId = `gc-detail-${d.code}`
  return (
    <li className="border-t border-border first:border-t-0" data-testid={`gc-row-${d.code}`}>
      <button type="button" className={rowBtnCls} aria-expanded={open} aria-controls={detailId}
        onClick={onToggle}>
        <span className="font-mono text-[11px] text-text">{d.code}</span>
        <span className="min-w-0">
          <span className="block text-[11.5px] text-text truncate">{d.title}</span>
          <span className="block text-[10px] text-muted truncate">
            {d.tool} · Next: {d.next_step}
            {blocked && <span className="text-warn"> · {blocked}</span>}
          </span>
        </span>
        <span className="flex items-center gap-1">
          {d.freeze_state !== 'none' && <Tag tone={FREEZE_TONE[d.freeze_state]}>{FREEZE_LABEL[d.freeze_state]}</Tag>}
          <Tag tone={STATUS_TONE[d.status]}>{STATUS_LABEL[d.status]}</Tag>
        </span>
      </button>
      {open && (
        <div id={detailId} className="px-3 pb-3 pt-1 bg-panel space-y-3">
          <Detail name={name} d={d} statuses={statuses} hasCampusInput={hasCampusInput} />
        </div>
      )}
    </li>
  )
}

function useRefresh(name: string) {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: GRID_COMPLIANCE_KEY(name) })
}

function Detail({ name, d, statuses, hasCampusInput }: {
  name: string; d: Deliverable; statuses: GcStatus[]; hasCampusInput: boolean
}) {
  const refresh = useRefresh(name)
  const [error, setError] = useState<string | null>(null)
  const on = { onMutate: () => setError(null), onSuccess: refresh, onError: (e: unknown) => setError(errorText(e)) }
  const status = useMutation({ mutationFn: (s: GcStatus) => gridComplianceApi.setStatus(name, d.code, s), ...on })
  const freeze = useMutation({ mutationFn: () => gridComplianceApi.freeze(name, d.code), ...on })
  const pack = useMutation({
    mutationFn: () => gridComplianceApi.pack(name, d.code),
    onMutate: () => setError(null),
    onSuccess: ({ blob, filename }) => { saveBlob(blob, filename); refresh() },
    onError: (e: unknown) => setError(errorText(e)),
  })
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const requestPanelSection = useUIStore(s => s.requestPanelSection)
  const toCampusFile = () => withReturnFrom(() => {
    setSlidePanel('campusElectrical')
    requestPanelSection('campusElectrical', 'campus_file')
  })
  const pending = d.feeds.filter(f => f.state === 'pending')
  const unavailable = d.feeds.filter(f => f.state === 'unavailable')
  const submittedOn = statuses.indexOf(d.status) >= SUBMITTED_RANK ? d.submissions.at(-1) ?? null : null
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-muted" htmlFor={`gc-status-${d.code}`}>Status</label>
        <select id={`gc-status-${d.code}`} className={inputCls} value={d.status}
          disabled={status.isPending} onChange={e => status.mutate(e.target.value as GcStatus)}>
          {statuses.map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
        <Btn onClick={() => freeze.mutate()} disabled={freeze.isPending || !hasCampusInput || submittedOn !== null}>
          {freeze.isPending ? 'Freezing…' : d.freeze ? 'Freeze again' : 'Freeze data'}
        </Btn>
        <Btn onClick={() => pack.mutate()} disabled={pack.isPending}>
          {pack.isPending ? 'Building…' : 'Build report pack'}
        </Btn>
        <span className="text-[10px] text-muted">Status is declared by you; the tool does not judge it.</span>
      </div>
      {!hasCampusInput && (
        <p className="text-[11px] text-muted">
          Freeze is off: there is no campus file yet, and the freeze records it.{' '}
          <button type="button" className={linkCls} onClick={toCampusFile}>Campus file ›</button>
        </p>
      )}
      {hasCampusInput && submittedOn && (
        <p className="text-[11px] text-muted">
          Freeze is off: this deliverable was submitted on the data frozen on {submittedOn.frozen_at.slice(0, 10)},
          and that record is kept. Set the status back to Judged to freeze again.
        </p>
      )}
      {error && <p role="alert" className="text-[11px] text-danger">{error}</p>}
      <div className="text-[11px] text-ink-700 space-y-0.5">
        {d.freeze ? (
          <p>Data frozen on {d.freeze.frozen_at.slice(0, 10)}{' '}
            <Tag tone={FREEZE_TONE[d.freeze_state]}>{FREEZE_LABEL[d.freeze_state]}</Tag></p>
        ) : <p className="text-muted">The data is not frozen for this deliverable.</p>}
        {submittedOn && d.since_submission?.inputs === 'changed' && (
          <p className="text-muted">
            The inputs changed after submission. What was submitted stays as frozen; later steps follow that record.
          </p>
        )}
        {submittedOn && d.since_submission?.inputs === 'not_compared' && (
          <p className="text-muted">
            The current inputs were not compared with what was submitted. {d.since_submission.reason}
          </p>
        )}
        {hasCampusInput && !submittedOn && (d.freeze_state === 'stale' || d.freeze_state === 'unreadable') && (
          <p>
            {d.freeze_state === 'stale'
              ? 'The campus file, asset library or network changed after the freeze: freeze again once they are right. '
              : 'The inputs cannot be read, so the freeze cannot be checked. '}
            <button type="button" className={linkCls} onClick={toCampusFile}>Campus file ›</button>
          </p>
        )}
        {pending.length > 0 && (
          <p className="text-muted">Not in yet: {pending.map(f => f.label).join('; ')}.</p>
        )}
        {unavailable.length > 0 && (
          <p className="text-warning">Could not be read: {unavailable.map(f => f.label).join('; ')}.</p>
        )}
        {d.packs.length > 0 && <p className="text-muted">Report packs built: {d.packs.length}.</p>}
      </div>
      <DataList name={name} d={d} onError={setError} />
      <CommentLog name={name} d={d} onError={setError} />
    </>
  )
}

function DataList({ name, d, onError }: { name: string; d: Deliverable; onError: (e: string | null) => void }) {
  const refresh = useRefresh(name)
  const save = useMutation({
    mutationFn: (items: DataItem[]) => gridComplianceApi.setData(name, d.code, items),
    onMutate: () => onError(null), onSuccess: refresh, onError: (e: unknown) => onError(errorText(e)),
  })
  const update = (i: number, patch: Partial<DataItem>) =>
    save.mutate(d.data.map((it, j) => (j === i ? { ...it, ...patch } : it)))
  return (
    <div>
      <h5 className="text-[10.5px] font-semibold text-text mb-1">Data list</h5>
      {d.data.length === 0 ? <p className="text-[11px] text-muted">No input data for this deliverable.</p> : (
        <table className="w-full text-[11px]">
          <thead><tr className="text-left text-muted">
            <th className="font-normal py-0.5">Item</th><th className="font-normal">Supplier</th>
            <th className="font-normal">Due</th><th className="font-normal">Status</th>
          </tr></thead>
          <tbody>
            {d.data.map((it, i) => (
              <tr key={it.item_id} className="border-t border-border">
                <td className="py-0.5 pr-2 text-text">{it.label}</td>
                <td><input aria-label={`Supplier of ${it.label}`} className={inputCls} maxLength={80}
                  defaultValue={it.supplier} placeholder="role, e.g. TSO"
                  onBlur={e => e.target.value !== it.supplier && update(i, { supplier: e.target.value })} /></td>
                <td><input type="date" aria-label={`Due date of ${it.label}`} className={inputCls}
                  defaultValue={it.due ?? ''}
                  onBlur={e => (e.target.value || null) !== it.due && update(i, { due: e.target.value || null })} /></td>
                <td><select aria-label={`Status of ${it.label}`} className={inputCls} value={it.status}
                  onChange={e => update(i, { status: e.target.value as DataItem['status'] })}>
                  <option value="requested">Requested</option>
                  <option value="received">Received</option>
                  <option value="checked">Checked</option>
                </select></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function CommentLog({ name, d, onError }: { name: string; d: Deliverable; onError: (e: string | null) => void }) {
  const refresh = useRefresh(name)
  const [draft, setDraft] = useState({ date: today(), clause: '', comment: '' })
  const add = useMutation({
    mutationFn: () => gridComplianceApi.addComment(name, d.code, draft),
    onMutate: () => onError(null),
    onSuccess: () => { setDraft({ date: today(), clause: '', comment: '' }); refresh() },
    onError: (e: unknown) => onError(errorText(e)),
  })
  return (
    <div>
      <h5 className="text-[10.5px] font-semibold text-text mb-1">TSO comments and responses</h5>
      {d.comments.length === 0 && <p className="text-[11px] text-muted">No TSO comments yet.</p>}
      <ul className="space-y-1.5">
        {d.comments.map(c => <CommentItem key={c.comment_id} name={name} code={d.code} c={c}
          packs={d.packs.map(p => p.version)} onError={onError} />)}
      </ul>
      <form className="mt-2 flex flex-wrap items-end gap-1.5"
        onSubmit={e => { e.preventDefault(); if (draft.comment.trim()) add.mutate() }}>
        <input type="date" aria-label="Comment date" className={inputCls} value={draft.date} required
          onChange={e => setDraft({ ...draft, date: e.target.value })} />
        <input aria-label="Clause" placeholder="Clause" className={`${inputCls} w-24`} maxLength={64}
          value={draft.clause} onChange={e => setDraft({ ...draft, clause: e.target.value })} />
        <input aria-label="TSO comment" placeholder="TSO comment" className={`${inputCls} flex-1 min-w-40`}
          maxLength={4000} value={draft.comment} onChange={e => setDraft({ ...draft, comment: e.target.value })} />
        <Btn type="submit" disabled={add.isPending || !draft.comment.trim()}>Log comment</Btn>
      </form>
      {!draft.comment.trim() && (
        <p className="text-[10px] text-muted mt-0.5">Log comment is off until the TSO comment is typed.</p>
      )}
    </div>
  )
}

function CommentItem({ name, code, c, packs, onError }: {
  name: string; code: string; c: TsoComment; packs: number[]; onError: (e: string | null) => void
}) {
  const refresh = useRefresh(name)
  const [response, setResponse] = useState(c.response)
  const [revision, setRevision] = useState(c.revision)
  const [pack, setPack] = useState<string>(c.pack ? String(c.pack.version) : '')
  const answer = useMutation({
    mutationFn: () => gridComplianceApi.updateComment(name, code, c.comment_id, {
      response, revision, status: 'responded', ...(pack ? { pack_version: Number(pack) } : {}),
    }),
    onMutate: () => onError(null), onSuccess: refresh, onError: (e: unknown) => onError(errorText(e)),
  })
  const close = useMutation({
    mutationFn: () => gridComplianceApi.updateComment(name, code, c.comment_id, { status: 'closed' }),
    onMutate: () => onError(null), onSuccess: refresh, onError: (e: unknown) => onError(errorText(e)),
  })
  const tone: Tone = c.status === 'open' ? 'warn' : c.status === 'responded' ? 'accent' : 'ok'
  return (
    <li className="rounded border border-border bg-bg p-2 text-[11px]" data-testid={`gc-comment-${c.comment_id}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-text">{c.comment_id}</span>
        <span className="text-muted">{c.date}{c.clause && ` · ${c.clause}`}</span>
        <Tag tone={tone}>{COMMENT_LABEL[c.status]}</Tag>
      </div>
      <p className="text-text mt-0.5 whitespace-pre-wrap">{c.comment}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <input aria-label={`Response to ${c.comment_id}`} placeholder="Response" maxLength={4000}
          className={`${inputCls} flex-1 min-w-40`} value={response} onChange={e => setResponse(e.target.value)} />
        <input aria-label={`Revision for ${c.comment_id}`} placeholder="Rev" maxLength={16}
          className={`${inputCls} w-14`} value={revision} onChange={e => setRevision(e.target.value)} />
        {packs.length > 0 && (
          <select aria-label={`Pack for ${c.comment_id}`} className={inputCls} value={pack}
            onChange={e => setPack(e.target.value)}>
            <option value="">No pack</option>
            {packs.map(v => <option key={v} value={v}>Pack v{v}</option>)}
          </select>
        )}
        <Btn onClick={() => answer.mutate()} disabled={answer.isPending || !response.trim()}>
          Save response
        </Btn>
        {c.status === 'responded' && (
          <Btn onClick={() => close.mutate()} disabled={close.isPending}>Close</Btn>
        )}
      </div>
      {!response.trim() && (
        <p className="text-[10px] text-muted mt-0.5">Save response is off until a response is typed.</p>
      )}
    </li>
  )
}
